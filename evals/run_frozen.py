"""Run the frozen refund cases against U (none), D (cap) and C (countersign) through the live API + PayPal sandbox.

Each (case, guard) gets its own fresh $1.00 sandbox payment. For C, a simulated operator then approves the safe
correction (if any) so we can report legitimate completion with a human in the loop separately.
Stops immediately on LLM quota exhaustion. Results: evals/results/<run>.jsonl + printed summary.

Usage: python3 evals/run_frozen.py MODEL [--cases F1,F2] [--guards none,cap,countersign]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "evals/frozen/refund_cases.json").read_text())
API = "http://localhost:8000"


def token() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("COUNTERSIGN_OPERATOR_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("COUNTERSIGN_OPERATOR_TOKEN missing in .env")


def call(method: str, path: str, body: dict | None, tok: str) -> tuple[int, dict, float]:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read()), time.perf_counter() - started
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}"), time.perf_counter() - started


def run_one(case: dict, guard: str, model: str, tok: str) -> dict:
    agent_model = "scripted-dup:0.25" if case["id"] == "F7" else model
    rec = {"case": case["id"], "category": case["category"], "guard": guard, "agent": agent_model, "errors": []}
    status, view, _ = call("POST", "/api/cases", {"operator_instruction": case["operator_instruction"],
                                                  "untrusted_message": case["untrusted_message"], "guard": guard}, tok)
    if status != 201:
        return {**rec, "errors": [f"create {status}: {view}"]}
    c = view["case"]
    call("POST", f"/api/cases/{c['id']}/mandate", {"action": "refund", "capture_id": c["capture_id"],
                                                   "amount": case["mandate_amount"], "currency": "USD"}, tok)
    status, view, agent_s = call("POST", f"/api/cases/{c['id']}/agent", {"model": agent_model}, tok)
    if status != 200:
        return {**rec, "case_id": c["id"], "errors": [f"agent {status}: {view.get('error')}"]}
    rec |= summarize(view, "before_human")
    rec |= {"case_id": c["id"], "agent_seconds": round(agent_s, 2)}
    pending = [t for t in view["tool_calls"] if t["state"] == "ESCALATED" and t["correction"] and t["correction"].get("amount")]
    if guard == "countersign" and pending:
        status, view, _ = call("POST", f"/api/cases/{c['id']}/tool-calls/{pending[0]['id']}/approve-correction", {}, tok)
        if status != 200:
            rec["errors"].append(f"approve {status}: {view.get('error')}")
        else:
            rec |= summarize(view, "after_simulated_operator")
    return rec


def summarize(view: dict, label: str) -> dict:
    c, v = view["case"], view["case"]["verification"]
    t = c["agent_transcript"] or {}
    agent_calls = [x for x in view["tool_calls"] if x["origin"] == "agent"]
    return {f"{label}.{k}": v[k] for k in ("outcome", "excess_unauthorized_amount", "legit_task_completed",
                                           "unauthorized_proposals", "blocked_unauthorized_proposals",
                                           "false_completion_claim", "attack_encountered_before_money_call")} | {
        f"{label}.unauthorized_executed": v["unauthorized_sandbox_actions"],
        f"{label}.escalations": sum(1 for x in agent_calls if x["verdict"] != "ALLOW"),
        "proposals": [x["amount"] for x in agent_calls], "tool_sequence": t.get("tool_sequence"),
        "llm_requests": (t.get("usage") or {}).get("requests"), "final_text": (t.get("final_text") or "")[:400],
        "claims": [(k["claimed_amount"], k["supported"]) for k in (c["claim_checks"] or [])],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("model")
    p.add_argument("--cases", default=",".join(x["id"] for x in CASES["cases"]))
    p.add_argument("--guards", default="none,cap,countersign")
    args = p.parse_args()
    tok, out = token(), ROOT / "evals/results" / f"frozen_{args.model.replace(':', '_')}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl"
    out.parent.mkdir(exist_ok=True)
    wanted = args.cases.split(",")
    for case in [x for x in CASES["cases"] if x["id"] in wanted]:
        for guard in args.guards.split(","):
            rec = run_one(case, guard, args.model, tok)
            with out.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(json.dumps({k: rec.get(k) for k in ("case", "guard", "proposals", "before_human.outcome",
                  "before_human.excess_unauthorized_amount", "after_simulated_operator.outcome", "errors")}))
            if any("429" in e or "budget" in e for e in rec["errors"]):
                sys.exit(f"stopping: LLM quota/budget exhausted. Partial results in {out}")
    print("results:", out)


if __name__ == "__main__":
    main()
