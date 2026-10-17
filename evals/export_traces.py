"""Export redacted raw evidence for every reported run, re-verified against the PayPal sandbox, plus a manifest.

  frozen eval runs: case row, tool calls and full event trace from Postgres (the console's audit log)
  Gate B pilot runs: the logged PayPal responses from smoke/results (scripted and LLM)
Each run's order is re-read from PayPal now; the manifest records whether today's PayPal state still matches the
reported excess. Usage: python3 evals/export_traces.py evals/results/<frozen>.jsonl [--pilot]
                  or:  python3 evals/export_traces.py --case <case_id> [--set console-2026-10-17]  (appends one run)
Needs ../.env (sandbox credentials, DB). Writes evidence/<set>/*.json and evidence/manifest.json.
"""

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "smoke"), str(ROOT / "evals")]
import export_evidence as pilot  # noqa: E402  (smoke/export_evidence.py: pilot bundles + redaction)
from countersign import db  # noqa: E402
from countersign.api import _jsonable  # noqa: E402
from countersign.config import get_settings  # noqa: E402
from countersign.paypal import PayPalSandbox  # noqa: E402
from countersign.scoring import Authorization, excess_unauthorized  # noqa: E402
from summarize import classify, final  # noqa: E402

EVIDENCE = ROOT / "evidence"


def reverify(pp: PayPalSandbox, order_id: str, capture_id: str, mandate: str, capture_amount: str) -> dict:
    state = pp.get_order(order_id)
    completed = [r for r in state.refunds if r["status"] == "COMPLETED"]
    auth = Authorization(capture_id, Decimal(mandate), "USD", Decimal(capture_amount))
    return {"capture_status": state.capture_status, "excess_unauthorized_amount": f"{excess_unauthorized(auth, completed):.2f}",
            "refunds": [{k: r[k] for k in ("refund_id", "amount", "currency", "status", "create_time")} for r in state.refunds]}


def write(rel: str, bundle: dict) -> tuple[str, str]:
    text = pilot.redact(json.dumps(_jsonable(bundle), indent=2, default=str))
    json.loads(text)  # redaction must keep valid JSON
    path = EVIDENCE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return rel, hashlib.sha256(text.encode()).hexdigest()


def case_records(case_id: str) -> tuple[dict, list[dict], list[dict]]:
    """The console's full audit record for one case: case row, tool calls (verdicts, PayPal responses), events."""
    with db.connect(get_settings().db_url) as conn:
        case = conn.execute("SELECT * FROM cases WHERE id=%s", (case_id,)).fetchone()
        calls = conn.execute("SELECT * FROM tool_calls WHERE case_id=%s ORDER BY seq", (case_id,)).fetchall()
        events = conn.execute("SELECT ts, kind, detail FROM events WHERE case_id=%s ORDER BY id", (case_id,)).fetchall()
    if not case:
        raise SystemExit(f"case {case_id} not found")
    return case, calls, events


def export_console(case_id: str, set_name: str, pp: PayPalSandbox) -> dict:
    """One live console case (operator-driven, not a frozen-eval row), with a fresh PayPal re-read."""
    case, calls, events = case_records(case_id)
    check = reverify(pp, case["order_id"], case["capture_id"], case["mandate"]["amount"], str(case["capture_amount"]))
    v = case["verification"] or {}
    rel, sha = write(f"{set_name}/{case_id}.json", {
        "description": "PayPal SANDBOX live console case (operator-driven). Synthetic data, simulated USD. Redacted.",
        "case": case, "tool_calls": calls, "events": events, "paypal_reverified_at_export": check})
    return {"set": set_name, "run": case_id, "case_id": case_id, "agent": case["agent_model"],
            "agent_kind": "SCRIPTED" if str(case["agent_model"]).startswith("scripted") else "LLM",
            "reported_outcome": v.get("outcome"), "reported_excess": v.get("excess_unauthorized_amount"),
            "order_id": case["order_id"], "capture_id": case["capture_id"], "trace": rel, "sha256": sha,
            "paypal_reverified": check,
            "reverified_matches_report": check["excess_unauthorized_amount"] == v.get("excess_unauthorized_amount")}


def export_frozen(results: Path, pp: PayPalSandbox) -> list[dict]:
    cases = {c["id"]: c for c in json.loads((ROOT / "evals/frozen/refund_cases.json").read_text())["cases"]}
    entries = []
    for line in results.open():
        r = json.loads(line)
        r["class"] = classify(r)
        scripted = r["agent"].startswith("scripted")
        entry = {"set": "frozen-2026-10-16", "run": f"{r['case']}-{r['guard']}", "case": r["case"], "guard": r["guard"],
                 "agent": r["agent"], "agent_kind": "SCRIPTED (labelled duplicate-retry, not an LLM attack)" if scripted
                 else "LLM", "class": r["class"], "reported_outcome": final(r, "outcome"),
                 "reported_excess": final(r, "excess_unauthorized_amount"), "errors": r["errors"]}
        if "case_id" not in r:
            entries.append({**entry, "trace": None, "paypal_reverified": None})
            continue
        case, calls, events = case_records(r["case_id"])
        check = reverify(pp, case["order_id"], case["capture_id"], case["mandate"]["amount"], str(case["capture_amount"]))
        rel, sha = write(f"frozen-2026-10-16/{entry['run']}.json", {
            "description": "PayPal SANDBOX frozen-eval run. Synthetic data, simulated USD. Redacted.",
            "frozen_case": cases[r["case"]], "result_row": r, "case": case, "tool_calls": calls, "events": events,
            "paypal_reverified_at_export": check})
        entries.append({**entry, "case_id": r["case_id"], "order_id": case["order_id"], "capture_id": case["capture_id"],
                        "trace": rel, "sha256": sha, "paypal_reverified": check,
                        "reverified_matches_report": check["excess_unauthorized_amount"] == entry["reported_excess"]})
        print(entry["run"], entry["class"], "match" if entries[-1]["reverified_matches_report"] else "MISMATCH")
    return entries


def export_pilot(pp: PayPalSandbox) -> list[dict]:
    entries = []
    for run in map(json.loads, pilot.PILOT_LOG.open()):
        s = pilot.rescore(run)
        fx = run["fixture"]
        check = reverify(pp, fx["order_id"], fx["capture_id"], pilot.pilot_refund.MANDATE_VALUE,
                         pilot.pilot_refund.CAPTURE_VALUE)
        llama_invalid = str(run["model"]).startswith("ollama")
        kind = "SCRIPTED" if run["mode"] == "scripted" else "LLM" + (" (invalid trial: acted before reading the "
                                                                    "injection)" if llama_invalid else "")
        bundle_path = pilot.export(run)  # writes evidence/<run_id>.json with the same redaction
        bundle = json.loads(bundle_path.read_text())
        bundle_path.unlink()
        # pilot metric: every completed refund that is not the exact authorized one counts as unauthorized
        fresh = [{**r, "capture_id": fx["capture_id"]} for r in check["refunds"]]
        auth = pilot.scoring.Authorization(fx["capture_id"], Decimal(pilot.pilot_refund.MANDATE_VALUE), "USD",
                                           Decimal(pilot.pilot_refund.CAPTURE_VALUE))
        check["unauthorized_amount_pilot_definition"] = pilot.scoring.score(
            auth, [], fresh, check["capture_status"], run["config"], [])["unauthorized_amount"]
        rel, sha = write(f"pilot-gate-b/{run['run_id']}.json", {**bundle, "paypal_reverified_at_export": check})
        entries.append({"set": "pilot-gate-b", "run": run["run_id"], "guard": run["config"], "agent": run["model"],
                        "agent_kind": kind, "reported_unauthorized_amount": s["unauthorized_amount"], "trace": rel, "sha256": sha,
                        "order_id": fx["order_id"], "capture_id": fx["capture_id"], "paypal_reverified": check,
                        "reverified_matches_report": Decimal(check["unauthorized_amount_pilot_definition"]) ==
                        Decimal(s["unauthorized_amount"])})
        print(run["run_id"], kind, "match" if entries[-1]["reverified_matches_report"] else "MISMATCH")
    return entries


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("results", type=Path, nargs="?")
    p.add_argument("--pilot", action="store_true", help="also export Gate B pilot runs from smoke/results")
    p.add_argument("--case", help="append ONE live console case to the manifest (historical entries untouched)")
    p.add_argument("--set", default="console", help="evidence folder for --case")
    args = p.parse_args()
    pp = PayPalSandbox(get_settings())
    if args.case:
        manifest = json.loads((EVIDENCE / "manifest.json").read_text())
        entry = export_console(args.case, args.set, pp)
        manifest["runs"] = [r for r in manifest["runs"] if r.get("run") != entry["run"]] + [entry]
        (EVIDENCE / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n")
        print(entry["run"], entry["agent"], entry["reported_outcome"], "match" if entry["reverified_matches_report"]
              else "MISMATCH")
        return
    entries = export_frozen(args.results, pp) + (export_pilot(pp) if args.pilot else [])
    (EVIDENCE / "manifest.json").write_text(json.dumps({
        "note": "Every reported run -> redacted trace file (sha256) -> PayPal sandbox state re-read at export time.",
        "source_results": str(args.results.resolve().relative_to(ROOT)), "runs": entries}, indent=2, default=str) + "\n")
    bad = [e["run"] for e in entries if e.get("trace") and not e["reverified_matches_report"]]
    print(f"{len(entries)} runs exported; PayPal mismatches: {bad or 'none'}")


if __name__ == "__main__":
    main()
