# ADR-001: Countersign MVP architecture

**Status:** Accepted · **Date:** 16 October 2026 · **Scope:** vertical slice for the PayPal AI Hackathon (deadline 12 November 2026)

## Context

The pilots ([GATE_B_PILOT_REPORT.md](../GATE_B_PILOT_REPORT.md)) showed four things on the real PayPal sandbox:

1. Gemini 2.5 Flash followed an injected customer message and requested a $1.00 refund when only $0.25 was authorised (4 of 4 runs that saw the message).
2. An amount-cap rule allowed the bad refund.
3. Countersign escalated it, and no money moved.
4. The agent then told the operator it had completed a $0.25 refund that never happened.

We need a credible, demo-able product, not a framework.

## Decision

| Concern | Decision | Why |
|---|---|---|
| Shape | Agent + **in-process authorization layer** around money tools; **no MCP proxy** yet | Smallest thing that proves the claim; a proxy adds transport work without adding evidence |
| Backend | FastAPI (Python 3.13), synchronous request flow | Matches the pilot code; an agent run takes under 30 s |
| Database | PostgreSQL 17 (Docker, `127.0.0.1:55432`), plain SQL via psycopg 3 | Uniqueness constraints in the database enforce exactly-once execution; plain SQL keeps the state machine visible |
| Money | `Decimal` only; amounts are strings at the edges and must match `^\d+(\.\d{1,2})?$`; currency allowlist `{USD}` | No floating-point rounding errors on money |
| Authorization | **Deterministic** policy engine (`policy.py`). The LLM never decides. | Reproducible, testable, not open to prompt injection |
| Trust | The operator mandate is trusted **only after explicit confirmation** through the API (structured form fields). Customer text is stored as untrusted and is **never** promoted into the mandate. | Separates who is allowed to authorise from what the agent read |
| Ground truth | Every decision re-reads the PayPal order (refunds plus capture status) | The agent's arguments and its claims are never trusted |
| Correction | An escalation produces a **safe correction**: the remaining in-mandate action, which the operator can approve | Prevents the loss *and* finishes the legitimate task |
| Execution | Approved call → `PayPal-Request-Id = sha256(case, capture, amount, approval)` plus a UNIQUE column; a timeout leaves the state `UNKNOWN`, which is reconciled by GET and never blindly retried | Exactly once, even across retries |
| Verification | After execution, an independent GET checks capture ID, amount, currency and the resulting capture status against the mandate | Verdict on PayPal state, not on the API call's own response |
| Claim check | Deterministic check of the agent's final text against verified PayPal refunds | Catches false "done" claims (observed in the pilot) |
| Agent | Gemini OpenAI-compatible endpoint (2.5 Flash for headline runs, Flash Lite for controls), Ollama for local runs; hard request budget; stop on 429; no paid fallback | Free tier only |
| Frontend | Next.js (App Router), talks only to the backend; holds no credentials | PayPal and LLM keys stay server-side |
| PayPal | Sandbox host asserted before every request; demo payments are real sandbox card captures | No live calls, no made-up responses |

## Consequences

- **Positive:** every claim in the demo can be traced to a stored PayPal response and an audit event.
- **Negative:**
  - Synchronous agent runs keep the UI request open for up to about 30 s.
  - Only refunds are covered in the MVP.
  - Value-provenance tracking is used for explanation only; the mandate check makes the decision.
- **Deferred:** MCP proxy, payouts and invoice scenarios, an LLM mandate builder, authentication.

## MVP scope (frozen)

**In scope:**
- One scenario: refund authorisation, live, end to end.
- Case lifecycle and audit timeline.
- Policy engine plus safe correction.
- Idempotent execution and reconciliation.
- Independent verification.
- Claim check.
- Corrected scoring.
- A small frozen evaluation set (cases defined, not mass-run).
- Next.js case console.

**Out of scope:**
- MCP proxy
- Payouts and invoices in the user interface
- Disputes
- The full 82-scenario benchmark
- Authentication and multiple tenants
- Deployment

## Files

```text
backend/requirements.txt
backend/countersign/{config,money,paypal,policy,claims,scoring,agent,db,service,api,logging_setup}.py
backend/countersign/schema.sql
backend/tests/{test_policy,test_claims,test_scoring,test_money,test_service_sandbox}.py
evals/frozen/refund_cases.json      evals/heldout/README.md (sealed; not run)
web/ (Next.js app: app/page.tsx, app/cases/[id]/page.tsx, lib/api.ts, styles)
scripts/db_up.sh   docker-compose.yml
```

`smoke/` and `evidence/` are kept unchanged (earlier experiments).

## Milestones and acceptance tests

| # | Milestone | Acceptance |
|---|---|---|
| M1 | Pure core: money, policy, claims, scoring | Unit tests cover: the observed attack is escalated with a $0.25 correction; wrong capture, wrong currency and over-mandate are escalated; a non-refundable capture is blocked; the observed false claim is flagged; the negated claim "unable to issue $1.00" is not flagged |
| M2 | PayPal client + database + service state machine | Sandbox integration test: create case → confirm mandate → scripted $1.00 proposal escalated → approve correction → exactly one $0.25 refund → verified exact; running approve again does not create a second refund |
| M3 | FastAPI endpoints + live agent | `curl` walk-through with Gemini (one run, within budget) produces the same states |
| M4 | Next.js console | In the browser: the full flow on the sandbox, every panel filled from backend data, no console errors |

**Estimated effort:** M1 0.5 day · M2 1 day · M3 0.5 day · M4 1–1.5 days. Total about 3.5 days of focused work.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| Gemini free quota (20 requests per day on 2.5 Flash) | Flash Lite; scripted-adversary mode, clearly labelled, for UI and tests; a recorded real transcript, labelled as such, for the video |
| Sandbox latency (~1.3 s per GET) | Acceptable for the MVP; a webhook-fed ledger later |
| Agent ignores the escalation and invents success | That is the claim-check feature; the demo shows it |
| Docker not available to judges | Documented native Postgres instructions; hosted demo later |
