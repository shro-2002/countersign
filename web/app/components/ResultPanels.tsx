import type { Case, CaseEvent } from "@/lib/api";

const OUTCOME_TONE: Record<string, string> = { EXACT: "ok", INCOMPLETE: "correction" };
const OUTCOME_TEXT: Record<string, string> = {
  EXACT: "PayPal shows exactly the authorized refund",
  INCOMPLETE: "No refund executed yet: awaiting the merchant's decision",
};

export function VerificationPanel({ c }: { c: Case }) {
  const v = c.verification;
  if (!v) return <p className="muted">Runs after execution.</p>;
  const exact = v.outcome === "EXACT";
  return (
    <>
      <div className={`callout ${OUTCOME_TONE[v.outcome] ?? "alarm"}`}>
        <h3>{OUTCOME_TEXT[v.outcome] ?? `Mismatch: ${v.outcome}`}</h3>
        <span className={`badge ${exact ? "ok" : "bad"}`}>{v.outcome}</span>
      </div>
      <dl className="kv" style={{ marginTop: 16 }}>
        <dt>Excess unauthorized</dt><dd className="big-number">${v.excess_unauthorized_amount}</dd>
        <dt>Capture status</dt><dd>{v.capture_status}</dd>
        <dt>Mandate</dt><dd>refund {c.mandate?.amount} {c.mandate?.currency} on {c.capture_id}</dd>
        <dt>PayPal refunds</dt>
        <dd>{v.paypal_refunds.length === 0 ? "none" : v.paypal_refunds.map((r) => (
          <div key={r.refund_id}>{r.refund_id} · {r.amount} {r.currency} · {r.status}</div>))}</dd>
        <dt>Blocked unauthorized</dt><dd>{v.blocked_unauthorized_proposals} agent proposal(s)</dd>
      </dl>
      <p className="muted">Read independently from PayPal (GET order) after execution — not taken from the agent or our own call result.</p>
    </>
  );
}

export function ClaimPanel({ c }: { c: Case }) {
  const t = c.agent_transcript;
  if (!t) return <p className="muted">Runs after the agent finishes.</p>;
  const checks = c.claim_checks ?? [];
  return (
    <>
      <p className="muted">Agent&apos;s final message to the operator ({c.agent_model}):</p>
      <p className="quote">{t.final_text || "(no final message)"}</p>
      {checks.length === 0 && <p className="muted">No refund completion claims found in the message.</p>}
      {checks.map((k, i) => (
        <div key={i} className={`callout ${k.supported ? "ok" : "alarm"}`}>
          <h3>{k.supported ? "Claim verified" : "False completion claim"}</h3>
          <p className="quote flagged">{k.sentence}</p>
          <p className="muted">{k.evidence}</p>
        </div>
      ))}
      <p className="muted">Checked against PayPal refunds at the moment the agent finished.</p>
    </>
  );
}

const TONE: Record<string, string> = {
  authorization_escalate: "warn", authorization_block: "bad", false_claim_detected: "bad",
  paypal_refund_succeeded: "ok", paypal_refund_failed: "bad", authorization_allow: "ok", claim_supported: "ok",
};
const LABEL: Record<string, string> = {
  demo_payment_captured: "Sandbox payment captured", untrusted_content_stored: "Untrusted customer text stored",
  mandate_confirmed: "Merchant confirmed authorization", agent_started: "Agent started",
  authorization_escalate: "Countersign escalated proposal", authorization_allow: "Countersign allowed proposal",
  authorization_block: "Countersign blocked proposal", agent_finished: "Agent finished",
  false_claim_detected: "False completion claim detected", claim_supported: "Agent claim verified",
  paypal_state_verified: "PayPal state verified", operator_approved_correction: "Merchant approved correction",
  paypal_refund_succeeded: "PayPal refund executed", paypal_refund_failed: "PayPal refund failed",
  paypal_refund_unknown: "PayPal outcome unknown (reconcile)", operator_rejected: "Merchant rejected",
  duplicate_approval_ignored: "Duplicate approval ignored", proposal_rejected_invalid: "Invalid proposal rejected",
};

function summary(e: CaseEvent): string {
  const d = e.detail as Record<string, unknown>;
  if (e.kind === "paypal_state_verified") return `${d.outcome} · excess $${d.excess_unauthorized_amount} · ${d.capture_status}`;
  if (e.kind.startsWith("authorization_")) return `${d.origin} · ${d.amount} · ${(d.failed_checks as string[] | undefined)?.join(", ") || "all checks passed"}`;
  if (e.kind === "paypal_refund_succeeded") return `${d.refund_id} · ${d.amount} ${d.status}`;
  if (e.kind === "mandate_confirmed") return `refund ${d.amount} ${d.currency}`;
  if (e.kind === "demo_payment_captured") return `${d.capture_id} · $${d.amount}`;
  if (e.kind === "false_claim_detected") return String(d.evidence);
  if (e.kind === "agent_started") return String(d.model);
  return "";
}

export function Timeline({ events }: { events: CaseEvent[] }) {
  return (
    <aside className="timeline" aria-label="Audit timeline">
      <h2>Audit timeline</h2>
      {events.length === 0 ? <p className="muted">Events appear as the case progresses.</p> : (
        <ol>
          {events.map((e, i) => (
            <li key={i} data-tone={TONE[e.kind]}>
              <div className="when">{new Date(e.ts).toLocaleTimeString()}</div>
              <div className="what">{LABEL[e.kind] ?? e.kind}</div>
              <div className="info">{summary(e)}</div>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
