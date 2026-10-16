"""Deterministic authorization of money-moving tool calls. No LLM, no I/O.

A proposal is ALLOWED only if it fits a confirmed operator mandate AND the live PayPal record.
Mismatches against the mandate are ESCALATED with a safe correction (the remaining in-mandate action).
Proposals PayPal state makes unsafe regardless of intent (non-refundable capture) are BLOCKED.
Provenance is computed for explanation only; it never changes the verdict.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .money import fmt

REFUNDABLE_STATUSES = frozenset({"COMPLETED", "PARTIALLY_REFUNDED"})


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class Mandate:
    """Operator authorization. Constructed only from explicitly confirmed operator input."""
    action: str
    capture_id: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class PaymentRecord:
    """What PayPal reports right now for the capture."""
    capture_id: str
    capture_status: str
    capture_amount: Decimal
    currency: str
    refunded_total: Decimal


@dataclass(frozen=True)
class Proposal:
    action: str
    capture_id: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    checks: tuple[Check, ...]
    correction: Proposal | None
    provenance: dict = field(default_factory=dict)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def _mandate_checks(mandate: Mandate, proposal: Proposal, record: PaymentRecord) -> list[Check]:
    remaining = mandate.amount - record.refunded_total
    return [
        Check("action_in_mandate", proposal.action == mandate.action,
              f"proposed '{proposal.action}', mandate allows '{mandate.action}'"),
        Check("capture_matches_mandate", proposal.capture_id == mandate.capture_id,
              f"proposed capture {proposal.capture_id}, mandate capture {mandate.capture_id}"),
        Check("currency_matches_mandate", proposal.currency == mandate.currency,
              f"proposed {proposal.currency}, mandate {mandate.currency}"),
        Check("within_mandate_amount", proposal.amount <= remaining,
              f"PayPal shows {fmt(record.refunded_total)} already refunded; mandate {fmt(mandate.amount)}; "
              f"remaining {fmt(max(remaining, Decimal('0')))}; proposed {fmt(proposal.amount)}"),
    ]


def _record_checks(proposal: Proposal, record: PaymentRecord) -> list[Check]:
    remaining_capture = record.capture_amount - record.refunded_total
    return [
        Check("capture_is_refundable", record.capture_status in REFUNDABLE_STATUSES,
              f"PayPal capture status {record.capture_status}"),
        Check("within_captured_amount", proposal.amount <= remaining_capture,
              f"capture {fmt(record.capture_amount)}, refundable remainder {fmt(remaining_capture)}"),
        Check("record_matches_proposal", record.capture_id == proposal.capture_id and record.currency == proposal.currency,
              f"PayPal record {record.capture_id} {record.currency}"),
    ]


def provenance(value: str, operator_text: str, record_values: set[str], untrusted_texts: list[str]) -> list[str]:
    sources = []
    if value and value in operator_text:
        sources.append("operator_instruction")
    if value in record_values:
        sources.append("paypal_record")
    if any(value and value in text for text in untrusted_texts):
        sources.append("untrusted_content")
    return sources or ["agent_generated"]


def evaluate(mandate: Mandate | None, proposal: Proposal, record: PaymentRecord,
             operator_text: str = "", untrusted_texts: list[str] | None = None) -> Decision:
    untrusted_texts = untrusted_texts or []
    record_values = {record.capture_id, fmt(record.capture_amount), fmt(record.refunded_total)}
    prov = {
        "amount": provenance(fmt(proposal.amount), operator_text, record_values, untrusted_texts),
        "capture_id": provenance(proposal.capture_id, operator_text, record_values, untrusted_texts),
    }
    if mandate is None:
        checks = (Check("mandate_confirmed", False, "no operator-confirmed mandate for this case"),)
        return Decision(Verdict.ESCALATE, checks, None, prov)

    record_checks = _record_checks(proposal, record)
    mandate_checks = _mandate_checks(mandate, proposal, record)
    checks = (Check("mandate_confirmed", True, "operator confirmed mandate"), *mandate_checks, *record_checks)
    if not record_checks[0].passed:
        return Decision(Verdict.BLOCK, checks, None, prov)
    if all(c.passed for c in checks):
        return Decision(Verdict.ALLOW, checks, None, prov)
    return Decision(Verdict.ESCALATE, checks, _safe_correction(mandate, record), prov)


def _safe_correction(mandate: Mandate, record: PaymentRecord) -> Proposal | None:
    """The largest action that is fully inside the mandate and the PayPal record, if any."""
    if record.capture_id != mandate.capture_id or record.currency != mandate.currency:
        return None
    remaining = min(mandate.amount - record.refunded_total, record.capture_amount - record.refunded_total)
    if remaining <= 0 or record.capture_status not in REFUNDABLE_STATUSES:
        return None
    return Proposal(mandate.action, mandate.capture_id, remaining, mandate.currency)


CAP_BASELINE_LIMIT = Decimal("5.00")


def evaluate_baseline(guard: str, proposal: Proposal) -> Decision:
    """Comparison baselines for evaluation only. 'none' executes everything; 'cap' auto-allows refunds up to a
    fixed amount (a typical threshold rule). Neither knows the operator's intent or PayPal's record."""
    if guard == "none":
        return Decision(Verdict.ALLOW, (Check("unprotected", True, "no guard"),), None, {})
    passed = proposal.amount <= CAP_BASELINE_LIMIT
    check = Check("amount_cap", passed, f"proposed {fmt(proposal.amount)} vs cap {fmt(CAP_BASELINE_LIMIT)}")
    return Decision(Verdict.ALLOW if passed else Verdict.ESCALATE, (check,), None, {})
