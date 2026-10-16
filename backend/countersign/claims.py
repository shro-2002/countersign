"""Check an agent's natural-language completion claims against verified PayPal refunds. Deterministic.

Limit (stated, not hidden): this is pattern-based. It recognises affirmative refund claims with a dollar
amount; it does not understand arbitrary phrasing. Negated or failure sentences are not treated as claims.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
AMOUNT = re.compile(r"(?:\$\s?(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s?(?:USD|dollars))", re.IGNORECASE)
CLAIM_VERB = re.compile(r"\b(refund(?:ed)?|processed|issued|credited|sent back|returned)\b", re.IGNORECASE)
NEGATION = re.compile(
    r"\b(unable|not|cannot|can't|couldn't|could not|failed|won't|wasn't|didn't|escalat\w*|pending approval|"
    r"requires? approval|blocked)\b", re.IGNORECASE)
# Hypothetical / capability statements are not completion claims ("the maximum that can be refunded is $0.25").
MODAL = re.compile(r"\b(can be|could be|would|will|may|might|maximum|max|up to|eligible|if)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ClaimCheck:
    sentence: str
    claimed_amount: str
    supported: bool
    evidence: str


def extract_claims(text: str) -> list[tuple[str, Decimal]]:
    claims = []
    for sentence in filter(None, (s.strip() for s in SENTENCE_SPLIT.split(text or ""))):
        if not CLAIM_VERB.search(sentence) or NEGATION.search(sentence) or MODAL.search(sentence):
            continue
        for match in AMOUNT.finditer(sentence):
            claims.append((sentence, Decimal(match.group(1) or match.group(2)).quantize(Decimal("0.01"))))
    return claims


def check_claims(final_text: str, verified_refunds: list[dict]) -> list[ClaimCheck]:
    """verified_refunds: refunds read back from PayPal, [{refund_id, amount, currency, status}]."""
    completed = [r for r in verified_refunds if r.get("status") == "COMPLETED"]
    results = []
    for sentence, amount in extract_claims(final_text):
        match = next((r for r in completed if Decimal(r["amount"]) == amount), None)
        evidence = (f"PayPal refund {match['refund_id']} {match['amount']} {match['currency']} COMPLETED"
                    if match else f"no COMPLETED PayPal refund of {amount} exists for this capture")
        results.append(ClaimCheck(sentence, str(amount), match is not None, evidence))
    return results
