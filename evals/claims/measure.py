"""Score a claim detector against the hand-labelled real-message corpus. False positives and false negatives are
reported separately, at two levels:
  claims:   (message, amount) pairs the agent says it completed      -> extracted vs labelled
  messages: "this message contains a false completion claim" flag   -> flagged vs labelled

Usage: python3 evals/claims/measure.py [path/to/claims.py]   (default: backend/countersign/claims.py)
"""

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "real_agent_messages.json"


def load_detector(path: Path):
    spec = importlib.util.spec_from_file_location("claims_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measure(detector) -> dict:
    counts = {"claims_tp": 0, "claims_fp": 0, "claims_fn": 0, "flag_tp": 0, "flag_fp": 0, "flag_fn": 0, "flag_tn": 0}
    errors = []
    for msg in json.loads(CORPUS.read_text())["messages"]:
        refunds = [{"refund_id": f"R{i}", "amount": a, "currency": "USD", "status": "COMPLETED"}
                   for i, a in enumerate(msg["paypal_completed_at_agent_finish"])]
        checks = detector.check_claims(msg["final_text"], refunds)
        found, labelled = {c.claimed_amount for c in checks}, set(msg["label_completed_claims"])
        counts["claims_tp"] += len(found & labelled)
        counts["claims_fp"] += len(found - labelled)
        counts["claims_fn"] += len(labelled - found)
        flagged, truth = any(not c.supported for c in checks), msg["label_false_claim"]
        counts[{(True, True): "flag_tp", (True, False): "flag_fp", (False, True): "flag_fn",
                (False, False): "flag_tn"}[(flagged, truth)]] += 1
        if found != labelled or flagged != truth:
            errors.append({"id": msg["id"], "extracted": sorted(found), "labelled": sorted(labelled),
                           "flagged": flagged, "label_false_claim": truth})
    return {"counts": counts, "errors": errors}


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / "backend" / "countersign" / "claims.py"
    print(json.dumps(measure(load_detector(path)), indent=2))
