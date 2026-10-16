import type { Case, ToolCall } from "@/lib/api";

type Props = {
  agentCall: ToolCall;
  agentName: string;
  mandate: NonNullable<Case["mandate"]>;
  correctionCall?: ToolCall;
  isBusy: boolean;
  onApprove: () => void;
  onReject: () => void;
};

const VERDICT_TEXT: Record<ToolCall["verdict"], string> = {
  ALLOW: "Within the confirmed authorization: executed",
  ESCALATE: "Execution stopped: conflicts with the merchant's authorization",
  BLOCK: "Execution stopped: unsafe given PayPal's current record",
};

const PROVENANCE_LABEL: Record<string, string> = {
  operator_instruction: "operator instruction",
  paypal_record: "PayPal record",
  untrusted_content: "customer message (untrusted)",
  agent_generated: "agent-generated",
};

export function DecisionPanel({ agentCall, agentName, mandate, correctionCall, isBusy, onApprove, onReject }: Props) {
  const amountMismatch = agentCall.amount !== mandate.amount;
  const failed = agentCall.checks.filter((c) => !c.passed);
  const canDecide = agentCall.state === "ESCALATED" && agentCall.correction?.amount && !correctionCall;

  return (
    <>
      <div className="toolcall" aria-label="Proposed tool call">
        create_refund(capture_id=&quot;{agentCall.capture_id}&quot;, amount=
        <span className={amountMismatch ? "arg-bad" : undefined}>&quot;{agentCall.amount}&quot;</span>,
        currency=&quot;{agentCall.currency}&quot;)
      </div>
      <p className="muted">Proposed by {agentName} · mandate allows {mandate.amount} {mandate.currency} on this capture</p>

      <div className="step-head" style={{ marginTop: 16 }}>
        <span className={`badge ${agentCall.verdict}`}>{agentCall.verdict}</span>
        <strong>{VERDICT_TEXT[agentCall.verdict]}</strong>
      </div>
      {failed.length > 0 && (
        <p>{failed.map((c) => c.detail).join(" · ")}</p>
      )}
      <ul className="checks" aria-label="Authorization checks">
        {agentCall.checks.map((c) => (
          <li key={c.name}>
            <span className={c.passed ? "pass" : "fail"} aria-label={c.passed ? "passed" : "failed"}>{c.passed ? "✓" : "✕"}</span>
            <span className="name">{c.name}</span>
            <span className="detail">{c.detail}</span>
          </li>
        ))}
      </ul>
      <div style={{ marginTop: 12 }}>
        <span className="muted">Where the proposed amount <code>{agentCall.amount}</code> appears:</span>
        <div className="chips">
          {(agentCall.provenance.amount ?? []).map((p) => (
            <span key={p} className={`chip ${p}`}>{PROVENANCE_LABEL[p] ?? p}</span>
          ))}
        </div>
        <p className="muted">Provenance explains the conflict; the decision itself comes from the mandate and PayPal checks.</p>
      </div>

      {agentCall.correction?.amount && (
        <div className="callout correction">
          <h3>Safe correction: refund {agentCall.correction.amount} {agentCall.correction.currency}</h3>
          <p className="muted">The largest action fully inside your confirmed authorization and PayPal&apos;s record.
            It is re-checked against PayPal before it runs, and can execute only once.</p>
          {canDecide && (
            <div className="form-row">
              <button className="btn approve" disabled={isBusy} onClick={onApprove}>
                {isBusy && <span className="spinner" aria-hidden />}Approve {agentCall.correction.amount} refund
              </button>
              <button className="btn secondary" disabled={isBusy} onClick={onReject}>Reject — refund nothing</button>
            </div>
          )}
          {correctionCall && (
            <p><span className={`badge ${correctionCall.state === "SUCCEEDED" ? "ok" : "bad"}`}>{correctionCall.state}</span>
              {" "}Operator-approved correction · PayPal request id is unique to this approval</p>
          )}
          {agentCall.state === "REJECTED" && <p><span className="badge bad">Rejected</span> No refund issued.</p>}
        </div>
      )}
    </>
  );
}
