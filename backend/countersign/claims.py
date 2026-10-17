"""Check an agent's natural-language completion claims against verified PayPal refunds. Deterministic.

Each sentence is split into clauses, and every clause that mentions a refund amount gets one kind:
  COMPLETED        the agent says it has itself completed a refund ("I have issued a refund of $0.25")
  NOT_COMPLETED    negated or unsuccessful attempts ("I wasn't able to...", "my attempt to refund $1.00 was escalated")
  FUTURE           intentions, conditions, ability, instructions ("I will refund", "if you approve", "instructed to")
  HUMAN            actions that need or got a human's approval ("once you approve the $0.25 refund")
  OBSERVATION      reported state of PayPal or another source ("PayPal shows $0.00 already refunded")
Only COMPLETED clauses are checked against PayPal. Quoted text and code spans (IDs) are removed first.

Limits (measured, not hidden; see evals/claims/): pattern-based English heuristics. It does not resolve
pronouns beyond "this/that/the same amount" in the same sentence, does not check whether OBSERVATION statements
about PayPal are themselves true, and can miss unusual phrasings of completion.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_SPLIT = re.compile(r";|\(|\)|,\s*(?:and|but|so|while|whereas)\b|\s+but\s+", re.IGNORECASE)
QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|`[^`]*`")
AMOUNT = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s?(?:USD|dollars)\b", re.IGNORECASE)
# Finite past or perfect forms only: "to issue" or "refund" (noun) are not completions.
COMPLETION = re.compile(r"\b(refunded|issued|processed|credited|sent back|returned|completed|executed|went through|"
                        r"succeeded|(?:was|were) able to (?:refund|issue|process))\b", re.IGNORECASE)
# An observation needs a state source AND a reporting verb, so "refunded according to your instructions" stays a claim.
STATE_SOURCE = r"(paypal|system|records?|capture|payment|order|api|dashboard|ledger|account|status|message)"
REPORTING = r"(shows?|showed|indicates?|indicated|reports?|reported|says|said|states|stated|lists|listed|reflects?)"
OBSERVATION = (rf"\b{STATE_SOURCE}\b[^.]{{0,40}}?\b{REPORTING}\b"  # "PayPal's system indicates ..."
               rf"|\baccording to\b[^.]{{0,20}}?\b{STATE_SOURCE}\b")  # "according to PayPal, ..."
KIND_RULES = (  # first match wins; order encodes precedence
    ("HUMAN", re.compile(r"\b(approv\w*|operator|human|manual(?:ly)?|reviewer)\b", re.IGNORECASE)),
    ("NOT_COMPLETED", re.compile(r"\b(not|never|unable|cannot|can't|couldn't|wasn't|weren't|didn't|failed|"
                                 r"attempt\w*|tried|escalat\w*|blocked|rejected|declined|denied|refused|pending|"
                                 r"awaiting|on hold)\b|n't\b", re.IGNORECASE)),
    ("FUTURE", re.compile(r"\b(will|shall|going to|plan\w*|intend\w*|would|could|can|may|might|should|if|once|"
                          r"unless|eligible|instructed|asked to|to be)\b|'ll\b", re.IGNORECASE)),
    ("OBSERVATION", re.compile(OBSERVATION, re.IGNORECASE)),
)
# An amount that describes the payment or the authorization, not what was refunded.
DESCRIPTIVE_BEFORE = re.compile(r"\b(remaining|left|balance|maximum|max|limit|up to|mandate\w*|authori[sz]ed|"
                                r"captured|original|paid|charged)\b(?:\W+\w+){0,2}\W*$", re.IGNORECASE)
DESCRIPTIVE_AFTER = re.compile(r"^\s*(?:USD\s*)?(payment|capture|order|charge|purchase|balance)\b", re.IGNORECASE)
ANAPHORA = re.compile(r"\b(this|that|the same)\s+(amount|sum)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Statement:
    kind: str
    clause: str
    amounts: tuple[Decimal, ...]


@dataclass(frozen=True)
class ClaimCheck:
    sentence: str
    claimed_amount: str
    supported: bool
    evidence: str


def _amounts(clause: str, *, descriptive_too: bool = False) -> tuple[Decimal, ...]:
    found = []
    for m in AMOUNT.finditer(clause):
        is_descriptive = DESCRIPTIVE_BEFORE.search(clause[:m.start()]) or DESCRIPTIVE_AFTER.match(clause[m.end():])
        if descriptive_too or not is_descriptive:
            found.append(Decimal(m.group(1) or m.group(2)).quantize(Decimal("0.01")))
    return tuple(found)


def _kind(clause: str) -> str:
    if not COMPLETION.search(clause):
        return "NONE"
    return next((kind for kind, rule in KIND_RULES if rule.search(clause)), "COMPLETED")


def classify(text: str) -> list[Statement]:
    """Every clause that mentions a refund amount, with its kind. Deterministic; no model involved."""
    statements = []
    for sentence in filter(None, (s.strip() for s in SENTENCE_SPLIT.split(QUOTED.sub(" ", text or "")))):
        previous: tuple[Decimal, ...] = ()
        for clause in filter(None, (c.strip(" ,.*-:") for c in CLAUSE_SPLIT.split(sentence))):
            amounts = _amounts(clause)
            if not amounts and ANAPHORA.search(clause):  # "...is $1.00, and I have issued a refund for this amount"
                amounts = previous
            kind = _kind(clause)
            if amounts and kind != "NONE":
                statements.append(Statement(kind, clause, amounts))
            previous = _amounts(clause, descriptive_too=True) or previous
    return statements


def check_claims(final_text: str, verified_refunds: list[dict]) -> list[ClaimCheck]:
    """verified_refunds: refunds read back from PayPal, [{refund_id, amount, currency, status}].
    Only COMPLETED statements are claims; each distinct (clause, amount) is checked once."""
    completed = [r for r in verified_refunds if r.get("status") == "COMPLETED"]
    results, seen = [], set()
    for statement in (s for s in classify(final_text) if s.kind == "COMPLETED"):
        for amount in statement.amounts:
            if (statement.clause, amount) in seen:
                continue
            seen.add((statement.clause, amount))
            match = next((r for r in completed if Decimal(r["amount"]) == amount), None)
            evidence = (f"PayPal refund {match['refund_id']} {match['amount']} {match['currency']} COMPLETED"
                        if match else f"no COMPLETED PayPal refund of {amount} exists for this capture")
            results.append(ClaimCheck(statement.clause, str(amount), match is not None, evidence))
    return results
