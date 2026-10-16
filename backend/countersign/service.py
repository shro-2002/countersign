"""Case lifecycle: fund -> confirm mandate -> agent -> authorize -> (approve correction) -> execute -> verify.

Invariants enforced here:
  * The mandate comes only from explicit operator confirmation; customer text is never parsed into it.
  * Every money proposal is authorized against a fresh PayPal read.
  * A tool call executes at most once (state transition + UNIQUE PayPal-Request-Id + PayPal idempotency).
  * Verification and claim checks read PayPal state; they never trust the agent or our own call results.
"""

import hashlib
import logging
import uuid
from dataclasses import asdict
from decimal import Decimal

import psycopg

from . import agent as agent_mod
from . import db
from .claims import check_claims
from .config import Settings
from .money import MoneyError, fmt, parse_amount, parse_currency
from .paypal import OrderState, PayPalError, PayPalSandbox, PayPalTimeout
from .policy import Mandate, PaymentRecord, Proposal, Verdict, evaluate, evaluate_baseline
from .scoring import Authorization, score_run

log = logging.getLogger("countersign.service")
# PayPal refund states that use up mandate (FAILED/CANCELLED do not).
CONSUMING_REFUND_STATUSES = frozenset({"COMPLETED", "PENDING"})


class CaseError(ValueError):
    """Client error: invalid request for the case's current state."""


class CaseNotFound(CaseError):
    """No such case, or (judge mode) it belongs to another demo session."""


class Countersign:
    def __init__(self, settings: Settings, paypal: PayPalSandbox):
        self.s, self.paypal = settings, paypal

    # ---------- case setup ----------
    def create_case(self, operator_instruction: str, untrusted_message: str, payment_amount: str,
                    guard: str = "countersign", *, session_id: str | None = None, seed: str | None = None) -> str:
        amount = parse_amount(payment_amount)
        case_id = str(uuid.uuid4())
        order = self.paypal.create_demo_payment(amount, reference=case_id)
        with db.connect(self.s.db_url) as conn:
            conn.execute(
                "INSERT INTO cases (id, status, operator_instruction, untrusted_message, order_id, capture_id,"
                " capture_amount, currency, guard, session_id, seed) VALUES (%s,'FUNDED',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (case_id, operator_instruction, untrusted_message, order.order_id, order.capture_id,
                 order.capture_amount, order.currency, guard, session_id, seed))
            db.add_event(conn, case_id, "demo_payment_captured",
                         {"order_id": order.order_id, "capture_id": order.capture_id,
                          "amount": fmt(order.capture_amount), "status": order.capture_status,
                          "note": "Demo setup: real PayPal sandbox card payment"})
            db.add_event(conn, case_id, "untrusted_content_stored", {"source": "customer_message"})
        return case_id

    def confirm_mandate(self, case_id: str, action: str, capture_id: str, amount: str, currency: str) -> None:
        case = self._case(case_id)
        if case["status"] != "FUNDED":
            raise CaseError(f"mandate can only be confirmed once, before the agent runs (status {case['status']})")
        try:
            value, code = parse_amount(amount), parse_currency(currency)
        except MoneyError as exc:
            raise CaseError(str(exc)) from exc
        if action != "refund" or capture_id != case["capture_id"] or code != case["currency"].strip():
            raise CaseError("mandate must name refund, this case's capture and its currency")
        if value > case["capture_amount"]:
            raise CaseError("mandate amount exceeds the captured amount")
        mandate = {"action": action, "capture_id": capture_id, "amount": fmt(value), "currency": code,
                   "confirmed_by": "operator (console)"}
        with db.connect(self.s.db_url) as conn:
            conn.execute("UPDATE cases SET mandate=%s, mandate_confirmed_at=now(), status='MANDATE_CONFIRMED',"
                         " updated_at=now() WHERE id=%s AND status='FUNDED'", (db.json(mandate), case_id))
            db.add_event(conn, case_id, "mandate_confirmed", mandate)

    # ---------- agent ----------
    def run_agent(self, case_id: str, model: str) -> None:
        case = self._case(case_id)
        if case["status"] != "MANDATE_CONFIRMED":
            raise CaseError(f"agent runs once, after mandate confirmation (status {case['status']})")
        task = (f"{case['operator_instruction']}\n\nCase context (from the PayPal record): order {case['order_id']}, "
                f"capture_id {case['capture_id']}, captured {fmt(case['capture_amount'])} {case['currency'].strip()}.")
        ctx = agent_mod.AgentContext(
            operator_instruction=task, untrusted_message=case["untrusted_message"],
            capture={"capture_id": case["capture_id"], "amount": fmt(case["capture_amount"]),
                     "currency": case["currency"].strip(), "status": "COMPLETED"},
            refund_handler=lambda cap, amt: self._agent_refund(case_id, cap, amt))
        self._event(case_id, "agent_started", {"model": model})
        try:
            if model.startswith("scripted:"):
                run = agent_mod.run_scripted_adversary(ctx, injected_amount=model.split(":", 1)[1])
            elif model.startswith("scripted-dup:"):
                run = agent_mod.run_scripted_adversary(ctx, injected_amount=model.split(":", 1)[1], repeats=2)
            else:
                run = agent_mod.run_llm_agent(model, self.s.gemini_api_key, ctx, self.s.llm_request_budget)
        except agent_mod.AgentError as exc:
            self._agent_failed(case_id, str(exc))
            raise
        self._finish_agent(case_id, run)

    def _agent_failed(self, case_id: str, error: str) -> None:
        """No tool call yet: the case stays retryable. Otherwise verify now, which records PayPal state and moves the
        case out of MANDATE_CONFIRMED so a half-finished run cannot be started again on the same mandate."""
        proposed = bool(self.tool_calls(case_id))
        self._event(case_id, "agent_failed", {"error": error[:500], "retryable": not proposed})
        if proposed:
            self.verify(case_id)

    def _agent_refund(self, case_id: str, capture_id: str, amount: str) -> dict:
        try:
            proposal = Proposal("refund", capture_id, parse_amount(amount), "USD")
        except MoneyError as exc:
            self._event(case_id, "proposal_rejected_invalid", {"amount": amount, "error": str(exc)})
            return {"result": "REJECTED", "reason": str(exc)}
        with db.case_lock(self.s.db_url, case_id):
            call = self._authorize(case_id, proposal, origin="agent")
            if call["verdict"] == Verdict.ALLOW.value:
                return self._execute(case_id, call["id"])
        reason = "; ".join(c["detail"] for c in call["checks"] if not c["passed"])
        return {"result": "ESCALATED_TO_HUMAN" if call["verdict"] == "ESCALATE" else "BLOCKED", "reason": reason}

    def _finish_agent(self, case_id: str, run: agent_mod.AgentRun) -> None:
        state = self.paypal.get_order(self._case(case_id)["order_id"])
        claims = [asdict(c) for c in check_claims(run.final_text, state.refunds)]
        transcript = {"model": run.model, "tool_calls": run.tool_calls, "final_text": run.final_text,
                      "usage": run.usage, "tool_sequence": run.tool_sequence}
        refunded = sum((Decimal(r["amount"]) for r in state.refunds if r["status"] == "COMPLETED"), Decimal("0"))
        mandate_left = Decimal(self._case(case_id)["mandate"]["amount"]) - refunded
        with db.connect(self.s.db_url) as conn:
            if mandate_left <= 0:  # agent already used the mandate: withdraw stale corrections so none is offered
                stale = conn.execute("UPDATE tool_calls SET correction=NULL, updated_at=now() WHERE case_id=%s AND"
                                     " state='ESCALATED' AND correction IS NOT NULL RETURNING id", (case_id,)).fetchall()
                for row in stale:
                    db.add_event(conn, case_id, "correction_withdrawn",
                                 {"tool_call_id": str(row["id"]), "reason": "mandate already fully used per PayPal"})
            pending = conn.execute("SELECT 1 FROM tool_calls WHERE case_id=%s AND state='ESCALATED' AND correction"
                                   " IS NOT NULL", (case_id,)).fetchone()
            status = "AWAITING_APPROVAL" if pending else "AGENT_COMPLETE"
            conn.execute("UPDATE cases SET agent_model=%s, agent_transcript=%s, claim_checks=%s, status=%s,"
                         " updated_at=now() WHERE id=%s",
                         (run.model, db.json(transcript), db.json(claims), status, case_id))
            db.add_event(conn, case_id, "agent_finished", {"final_text": run.final_text, "usage": run.usage})
            for c in claims:
                db.add_event(conn, case_id, "claim_supported" if c["supported"] else "false_claim_detected", c)
        self.verify(case_id)  # always record what PayPal shows right after the agent, even when awaiting approval

    # ---------- authorization & execution ----------
    def _record(self, case: dict) -> tuple[PaymentRecord, OrderState]:
        """Mandate consumption = max(PayPal refunds not failed, our own in-flight/succeeded executions), so neither a
        PENDING refund nor PayPal read lag can make already-spent mandate look available again."""
        state = self.paypal.get_order(case["order_id"])
        at_paypal = sum((Decimal(r["amount"]) for r in state.refunds if r["status"] in CONSUMING_REFUND_STATUSES),
                        Decimal("0"))
        with db.connect(self.s.db_url) as conn:
            ledger = conn.execute("SELECT COALESCE(SUM(amount),0) AS n FROM tool_calls WHERE case_id=%s AND state IN"
                                  " ('EXECUTING','SUCCEEDED','UNKNOWN')", (case["id"],)).fetchone()["n"]
        return PaymentRecord(state.capture_id, state.capture_status, state.capture_amount, state.currency,
                             max(at_paypal, Decimal(ledger))), state

    def _authorize(self, case_id: str, proposal: Proposal, origin: str, correction_for: str | None = None) -> dict:
        case = self._case(case_id)
        m = case["mandate"]
        mandate = Mandate(m["action"], m["capture_id"], Decimal(m["amount"]), m["currency"]) if m else None
        record, _ = self._record(case)
        if case["guard"] == "countersign" or origin == "operator_correction":
            decision = evaluate(mandate, proposal, record, case["operator_instruction"], [case["untrusted_message"]])
        else:
            decision = evaluate_baseline(case["guard"], proposal)
        state = {"ALLOW": "APPROVED", "ESCALATE": "ESCALATED", "BLOCK": "BLOCKED"}[decision.verdict.value]
        correction = ({"action": decision.correction.action, "capture_id": decision.correction.capture_id,
                       "amount": fmt(decision.correction.amount), "currency": decision.correction.currency}
                      if decision.correction else None)
        if correction_for:
            correction = {"for_tool_call": correction_for}
        call_id = str(uuid.uuid4())
        checks = [asdict(c) for c in decision.checks]
        with db.connect(self.s.db_url) as conn:
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM tool_calls WHERE case_id=%s",
                               (case_id,)).fetchone()["n"]
            conn.execute(
                "INSERT INTO tool_calls (id, case_id, seq, origin, action, capture_id, amount, currency, verdict,"
                " checks, provenance, correction, state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (call_id, case_id, seq, origin, proposal.action, proposal.capture_id, proposal.amount,
                 proposal.currency, decision.verdict.value, db.json(checks), db.json(decision.provenance),
                 db.json(correction), state))
            db.add_event(conn, case_id, f"authorization_{decision.verdict.value.lower()}",
                         {"tool_call_id": call_id, "origin": origin, "amount": fmt(proposal.amount),
                          "capture_id": proposal.capture_id, "failed_checks": [c["name"] for c in checks
                                                                               if not c["passed"]],
                          "paypal_refunded_so_far": fmt(record.refunded_total)})
        return {"id": call_id, "verdict": decision.verdict.value, "checks": checks, "correction": correction}

    def approve_correction(self, case_id: str, tool_call_id: str) -> dict:
        with db.case_lock(self.s.db_url, case_id):
            result = self._approve_locked(case_id, tool_call_id)
        self.verify(case_id)
        return result

    def _approve_locked(self, case_id: str, tool_call_id: str) -> dict:
        with db.connect(self.s.db_url) as conn, conn.transaction():
            row = conn.execute("SELECT * FROM tool_calls WHERE id=%s AND case_id=%s FOR UPDATE",
                               (tool_call_id, case_id)).fetchone()
            if not row or row["state"] != "ESCALATED" or not row["correction"]:
                raise CaseError("only an escalated tool call with a safe correction can be approved")
            existing = conn.execute("SELECT id FROM tool_calls WHERE case_id=%s AND correction->>'for_tool_call'=%s",
                                    (case_id, tool_call_id)).fetchone()
        if existing:  # double-click / retry: return the existing correction, never execute twice
            self._event(case_id, "duplicate_approval_ignored", {"escalated_tool_call_id": tool_call_id,
                                                                "existing_correction_id": str(existing["id"])})
            return self._execute(case_id, existing["id"])
        c = row["correction"]
        self._event(case_id, "operator_approved_correction", {"escalated_tool_call_id": tool_call_id, **c})
        proposal = Proposal(c["action"], c["capture_id"], Decimal(c["amount"]), c["currency"])
        try:
            call = self._authorize(case_id, proposal, origin="operator_correction", correction_for=tool_call_id)
        except psycopg.errors.UniqueViolation as exc:  # unreachable under case_lock; the index is the backstop
            raise CaseError("correction already recorded for this escalation") from exc
        if call["verdict"] != "ALLOW":
            raise CaseError("correction no longer fits the mandate and PayPal record; not executed")
        return self._execute(case_id, call["id"])

    def reject(self, case_id: str, tool_call_id: str) -> None:
        with db.connect(self.s.db_url) as conn:
            updated = conn.execute("UPDATE tool_calls SET state='REJECTED', updated_at=now() WHERE id=%s AND"
                                   " case_id=%s AND state='ESCALATED'", (tool_call_id, case_id)).rowcount
            if not updated:
                raise CaseError("only an escalated tool call can be rejected")
            db.add_event(conn, case_id, "operator_rejected", {"tool_call_id": tool_call_id})
        self.verify(case_id)

    def _execute(self, case_id: str, call_id: str) -> dict:
        with db.connect(self.s.db_url) as conn:
            row = conn.execute("SELECT * FROM tool_calls WHERE id=%s", (call_id,)).fetchone()
            request_id = hashlib.sha256(f"{case_id}:{row['capture_id']}:{fmt(row['amount'])}:{call_id}"
                                        .encode()).hexdigest()[:36]
            claimed = conn.execute("UPDATE tool_calls SET state='EXECUTING', paypal_request_id=%s, updated_at=now()"
                                   " WHERE id=%s AND state='APPROVED' RETURNING id", (request_id, call_id)).fetchone()
        if not claimed:  # already executing or executed: report, never re-send
            return {"result": row["state"], "paypal_response": row["paypal_response"]}
        try:
            body = self._refund_with_reconcile(row, request_id)
            state, detail = "SUCCEEDED", {"refund_id": body.get("id"), "status": body.get("status"),
                                          "amount": (body.get("amount") or {}).get("value")}
        except PayPalError as exc:
            state, body, detail = "FAILED", exc.body, {"issue": exc.issue, "debug_id": exc.debug_id}
        except PayPalTimeout:
            state, body, detail = "UNKNOWN", {}, {"note": "timeout; reconcile by reading PayPal, do not retry"}
        with db.connect(self.s.db_url) as conn:
            conn.execute("UPDATE tool_calls SET state=%s, paypal_response=%s, updated_at=now() WHERE id=%s",
                         (state, db.json(body), call_id))
            db.add_event(conn, case_id, f"paypal_refund_{state.lower()}", {"tool_call_id": call_id,
                                                                            "request_id": request_id, **detail})
        return {"result": state, **detail}

    def _refund_with_reconcile(self, row: dict, request_id: str) -> dict:
        """On timeout the outcome is unknown. Re-sending with the SAME PayPal-Request-Id is safe: PayPal returns the
        original refund if the first request executed, or executes it once if it did not (observed in sandbox)."""
        args = (row["capture_id"], row["amount"], row["currency"].strip(), request_id)
        try:
            return self.paypal.refund(*args)
        except PayPalTimeout:
            log.warning("refund timed out; reconciling with same request id", extra={"request_id": request_id})
            return self.paypal.refund(*args)

    # ---------- verification ----------
    def verify(self, case_id: str) -> dict:
        case = self._case(case_id)
        state = self.paypal.get_order(case["order_id"])
        m = case["mandate"]
        auth = Authorization(m["capture_id"], Decimal(m["amount"]), m["currency"], case["capture_amount"])
        calls = self.tool_calls(case_id)
        proposals = [{"capture_id": c["capture_id"], "amount": fmt(c["amount"]), "currency": c["currency"].strip(),
                      "executed": c["state"] == "SUCCEEDED"} for c in calls if c["origin"] == "agent"]
        transcript = case["agent_transcript"] or {}
        result = score_run(auth, refunds=state.refunds, capture_status=state.capture_status, proposals=proposals,
                           tool_sequence=transcript.get("tool_sequence", []), claim_checks=case["claim_checks"] or [])
        verification = {**result, "paypal_refunds": state.refunds, "mandate": m,
                        "order_id": state.order_id, "capture_id": state.capture_id}
        status = "VERIFIED" if result["exact_success"] else "VERIFICATION_FAILED"
        if case["status"] == "AWAITING_APPROVAL" and not result["legit_task_completed"]:
            status = "AWAITING_APPROVAL"
        with db.connect(self.s.db_url) as conn:
            conn.execute("UPDATE cases SET verification=%s, status=%s, updated_at=now() WHERE id=%s",
                         (db.json(verification), status, case_id))
            db.add_event(conn, case_id, "paypal_state_verified",
                         {"outcome": result["outcome"], "capture_status": state.capture_status,
                          "excess_unauthorized_amount": result["excess_unauthorized_amount"],
                          "refunds": [{k: r[k] for k in ("refund_id", "amount", "currency", "status")}
                                      for r in state.refunds]})
        return verification

    # ---------- reads ----------
    def _case(self, case_id: str) -> dict:
        with db.connect(self.s.db_url) as conn:
            case = conn.execute("SELECT * FROM cases WHERE id=%s", (case_id,)).fetchone()
        if not case:
            raise CaseNotFound("case not found")
        return case

    def tool_calls(self, case_id: str) -> list[dict]:
        with db.connect(self.s.db_url) as conn:
            return conn.execute("SELECT * FROM tool_calls WHERE case_id=%s ORDER BY seq", (case_id,)).fetchall()

    def view(self, case_id: str) -> dict:
        case = self._case(case_id)
        with db.connect(self.s.db_url) as conn:
            events = conn.execute("SELECT ts, kind, detail FROM events WHERE case_id=%s ORDER BY id",
                                  (case_id,)).fetchall()
        return {"case": case, "tool_calls": self.tool_calls(case_id), "events": events}

    def demo_usage(self, session_id: str) -> dict:
        """Counts behind the judge-mode caps (rolling 24 h). Cases: demo sessions only. LLM runs: every Gemini run on
        this deployment (one shared key), counted at start so failed runs count too."""
        with db.connect(self.s.db_url) as conn:
            return conn.execute(
                "SELECT (SELECT count(*) FROM cases WHERE session_id=%s) AS session_cases,"
                " (SELECT count(*) FROM cases WHERE session_id IS NOT NULL AND created_at > now() - interval '1 day') AS day_cases,"
                " (SELECT count(*) FROM events WHERE kind='agent_started' AND detail->>'model' LIKE 'gemini:%%'"
                "  AND ts > now() - interval '1 day') AS day_llm_runs", (session_id,)).fetchone()

    def _event(self, case_id: str, kind: str, detail: dict) -> None:
        with db.connect(self.s.db_url) as conn:
            db.add_event(conn, case_id, kind, detail)
