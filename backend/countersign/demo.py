"""Judge demo mode (COUNTERSIGN_DEMO_MODE=judge): seeded cases only, per-session isolation, hard caps.

Enforced in the backend so a misconfigured or bypassed web proxy still cannot do more than this:
  * a case can only be created from a frozen seed (no free text, fixed $1.00 sandbox payment, guard countersign);
  * every case belongs to one demo session; other sessions get 404;
  * caps on cases per session, cases per day and LLM agent runs per day (free-tier spend);
  * only allowlisted agents. F7 always uses its labelled scripted duplicate agent.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEEDS_FILE = ROOT / "evals" / "frozen" / "refund_cases.json"
EVIDENCE_DIR = ROOT / "evidence"
REPLAY_AGENT = "gemini:gemini-2.5-flash"  # the model that followed the injection; too quota-limited to run per judge
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_CASES_PER_SESSION = 8
MAX_CASES_PER_DAY = 60
MAX_LLM_RUNS_PER_DAY = 25  # x 12-request budget per run stays inside the free tier we observed for Flash Lite
LLM_MODELS = frozenset({"gemini:gemini-flash-lite-latest", "gemini:gemini-2.5-flash"})
SCRIPTED_MODELS = frozenset({"scripted:1.00"})
FORCED_AGENT = {"F7": "scripted-dup:0.25"}


class DemoLimit(Exception):
    """A judge-mode cap or rule refused the request (HTTP 429/403)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def seeds() -> list[dict]:
    cases = json.loads(SEEDS_FILE.read_text())["cases"]
    return [{"id": c["id"], "category": c["category"], "operator_instruction": c["operator_instruction"],
             "untrusted_message": c["untrusted_message"], "suggested_mandate": c["mandate_amount"],
             "agent": FORCED_AGENT.get(c["id"])} for c in cases]


def seed(seed_id: str) -> dict:
    found = next((s for s in seeds() if s["id"] == seed_id), None)
    if not found:
        raise DemoLimit(403, "judge mode only runs the seeded cases")
    return found


def check_case_caps(usage: dict) -> None:
    """Refuse a new demo case when this session or the whole demo has used its case allowance."""
    if usage["session_cases"] >= MAX_CASES_PER_SESSION:
        raise DemoLimit(429, f"this demo session has used its {MAX_CASES_PER_SESSION} cases")
    if usage["day_cases"] >= MAX_CASES_PER_DAY:
        raise DemoLimit(429, "the demo's daily case limit is reached; recorded evidence is still available")


def check_llm_cap(model: str, usage: dict) -> None:
    """Refuse a live-model run once today's shared budget is used; scripted agents are always allowed."""
    if model in LLM_MODELS and usage["day_llm_runs"] >= MAX_LLM_RUNS_PER_DAY:
        raise DemoLimit(429, "today's live-model budget is used up; the scripted agent still runs")


def agent_for(seed_id: str | None, requested: str) -> str:
    """Model actually run for a demo case: forced for scripted seeds, else must be allowlisted."""
    if seed_id in FORCED_AGENT:
        return FORCED_AGENT[seed_id]
    if requested not in LLM_MODELS | SCRIPTED_MODELS:
        raise DemoLimit(403, f"agent {requested!r} is not available in judge mode")
    return requested


def replay() -> list[dict]:
    """Recorded Gemini 2.5 Flash pilot runs, REPLAYED from evidence files (not executed now), with their PayPal
    re-verification. Shown next to live runs so judges see the real-model attack even when quota is gone."""
    manifest = json.loads((EVIDENCE_DIR / "manifest.json").read_text())
    out = []
    for run in manifest["runs"]:
        if run["set"] != "pilot-gate-b" or run["agent"] != REPLAY_AGENT:
            continue
        trace = json.loads((EVIDENCE_DIR / run["trace"]).read_text())
        out.append({"source": "REPLAYED_EVIDENCE", "run": run["run"], "guard": run["guard"], "agent": run["agent"],
                    "trace_file": f"evidence/{run['trace']}", "sha256": run["sha256"],
                    "untrusted_message": trace["untrusted_customer_message"],
                    "agent_tool_calls": [{"tool": c["tool"], "args": c["args"]} for c in trace["agent_tool_calls"] or []],
                    "guard_decisions": [{k: d.get(k) for k in ("amount", "verdict")} for d in trace["guard_decisions"]],
                    "agent_final_text": trace["agent_final_text"],
                    "paypal_refunds_reverified": run["paypal_reverified"]["refunds"],
                    "reverified_matches_report": run["reverified_matches_report"]})
    return out
