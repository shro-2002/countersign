# Architecture

```text
Browser (Next.js console, no secrets)
   │  same-origin fetch /api/*
   ▼
Next.js route handler  ── adds Authorization: Bearer <operator token> (server env only)
   │
   ▼
FastAPI (backend/countersign/api.py) ── 401 without token
   │
   ├─ service.py   case lifecycle / state machine
   │     FUNDED → MANDATE_CONFIRMED → AGENT_COMPLETE | AWAITING_APPROVAL → VERIFIED | VERIFICATION_FAILED
   │
   ├─ agent.py     Gemini (OpenAI-compatible, native tool calls) · Ollama · labelled scripted adversary
   │     create_refund ──► service._agent_refund ──► policy (never PayPal directly)
   │
   ├─ policy.py    deterministic: mandate × fresh PayPal record → ALLOW | ESCALATE(+safe correction) | BLOCK
   ├─ paypal.py    sandbox-only client (host asserted), PayPal-Request-Id per execution
   ├─ claims.py    agent final text vs PayPal refunds
   ├─ scoring.py   outcome + excess unauthorized amount + separate metrics
   └─ db.py        PostgreSQL: cases, tool_calls (UNIQUE request id, one correction per escalation), events (audit)
```

## A refund request, step by step

1. **The agent proposes** `create_refund(capture, amount)`.
2. **The service re-reads PayPal** (`GET /v2/checkout/orders/{id}`) and builds a `PaymentRecord`: capture status, amount and completed refunds.
3. **`policy.evaluate(mandate, proposal, record)` decides:**
   - **ALLOW:** the tool call is stored as `APPROVED`, then executed.
   - **ESCALATE:** stored as `ESCALATED` along with a correction, which is the largest in-mandate refund still remaining.
   - **BLOCK:** stored as `BLOCKED`, for example when the capture is no longer refundable.
4. **Execution is exactly once:**
   - an atomic `APPROVED → EXECUTING` transition stores a deterministic `paypal_request_id` (UNIQUE);
   - the PayPal refund is sent with that `PayPal-Request-Id`;
   - on a timeout it is re-sent with the same ID;
   - the state ends as `SUCCEEDED`, `FAILED` or `UNKNOWN`.
5. **Verification re-reads PayPal** and scores the result against the mandate.
6. **The claim check compares** the agent's final text with the PayPal refunds.

## Evaluation baselines

Cases created with `guard=none` (unprotected) or `guard=cap` (auto-allow refunds of $5 or less) use `policy.evaluate_baseline`. The console always uses `countersign`.
