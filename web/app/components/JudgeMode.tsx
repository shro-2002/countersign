"use client";

import { useState } from "react";

import type { Replay } from "@/lib/api";

interface LoginGateProps {
  onLogin: (code: string) => Promise<void>;
  error: string | null;
}

export function LoginGate({ onLogin, error }: LoginGateProps) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <section className="step" aria-labelledby="login-heading">
      <div className="step-head"><span className="step-num">→</span><h2 id="login-heading">Judge access</h2></div>
      <p className="step-sub">Enter the access code from the Devpost testing instructions. You get a private session:
        only you see the cases you create.</p>
      {error && <div className="error" role="alert">{error}</div>}
      <form className="form-row" onSubmit={async (e) => { e.preventDefault(); setBusy(true); await onLogin(code); setBusy(false); }}>
        <label className="field">Access code
          <input type="password" autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        <button className="btn" disabled={busy || !code}>{busy && <span className="spinner" aria-hidden />}Enter</button>
      </form>
    </section>
  );
}

export function LiveVsReplayed() {
  return (
    <div className="callout" role="note">
      <h3>Judge mode: what is live and what is replayed</h3>
      <dl className="kv">
        <dt><span className="badge ok">LIVE</span></dt>
        <dd>Every case you create here: a new $1.00 PayPal <strong>sandbox</strong> card payment, the agent run, Countersign&apos;s
          decision, your approval, the refund, and the read-back from PayPal. The seeded cases are the frozen evaluation
          cases. Free text is disabled.</dd>
        <dt><span className="badge ESCALATE">SCRIPTED</span></dt>
        <dd>Case F7 and the &quot;Scripted adversary&quot; agent are labelled scripts, not an LLM. They make the attack reproducible.</dd>
        <dt><span className="badge bad">REPLAYED</span></dt>
        <dd>The Gemini 2.5 Flash runs below are recorded evidence from 16 Oct 2026. They are shown from the trace files and
          were re-read from PayPal at export. They do not run now: that model&apos;s free tier allows 20 requests a day.</dd>
      </dl>
      <p className="muted">Limits per session: 8 cases. Live-model runs share a daily budget; the scripted agent always works.</p>
    </div>
  );
}

export function ReplayPanel({ runs }: { runs: Replay[] }) {
  if (!runs.length) return null;
  return (
    <section className="step" aria-labelledby="replay-heading">
      <div className="step-head"><span className="step-num">R</span>
        <h2 id="replay-heading">Recorded real-model attack <span className="badge bad">REPLAYED</span></h2></div>
      <p className="step-sub">Gemini 2.5 Flash read the injected customer message and proposed a $1.00 refund against a
        $0.25 mandate. Guard U is unprotected, D is a $5 cap rule, C is Countersign.</p>
      {runs.map((r) => (
        <div className="toolcall" key={r.run}>
          <p><strong>{r.guard}</strong> · <span className="mono">{r.run}</span> · {r.agent}</p>
          <p className="mono">{r.agent_tool_calls.map((c) => `${c.tool}(${Object.entries(c.args).map(([k, v]) => `${k}=${v}`).join(", ")})`).join(" → ")}</p>
          <p>Decision: {r.guard_decisions.map((d) => `${d.amount} ${d.verdict}`).join(", ")} · PayPal now shows:{" "}
            {r.paypal_refunds_reverified.length
              ? r.paypal_refunds_reverified.map((x) => `${x.refund_id} $${x.amount}`).join(", ") : "no refund"}
            {r.reverified_matches_report ? "" : " (does not match report)"}</p>
          <p className="quote">Agent said: “{r.agent_final_text}”</p>
          <p className="muted mono">{r.trace_file} · sha256 {r.sha256.slice(0, 12)}…</p>
        </div>
      ))}
    </section>
  );
}
