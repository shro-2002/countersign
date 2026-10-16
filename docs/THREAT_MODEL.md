# Threat model and known limitations

**Scope:** a merchant support agent that can issue PayPal refunds, with Countersign deciding whether each refund may run. Sandbox only.

## Assets and trust boundaries

| Source | Trust level | How Countersign uses it |
|---|---|---|
| Operator mandate (confirmed in the console, behind the operator token) | **Trusted** | The only input that authorizes money movement |
| PayPal sandbox API responses (order, capture, refunds) | **Trusted ground truth** | Re-read at every decision and at verification |
| Customer message, agent output, agent tool arguments, agent claims | **Untrusted** | Stored and shown; never promoted into the authorization |

## Threats and current controls

| # | Threat | Control | Tested |
|---|---|---|---|
| T1 | Prompt injection makes the agent request a larger refund | A deterministic mandate check against fresh PayPal refund totals; escalation with a safe correction | Unit + sandbox; Gemini 2.5 Flash pilot |
| T2 | Refund on the wrong capture or in the wrong currency | Capture, currency and action must match the mandate *and* the PayPal record | Unit |
| T3 | Replayed or duplicate tool call (agent retry) | Cumulative check: the second refund exceeds the remaining mandate, so it is escalated | Sandbox (`scripted-dup`) |
| T4 | Double-click or concurrent operator approvals | Row lock + a UNIQUE index on one correction per escalation + `paypal_request_id` UNIQUE + `PayPal-Request-Id` | Sandbox: 4 concurrent approvals produced exactly 1 refund |
| T5 | Timeout after PayPal executed the request (unknown outcome) | Re-send once with the **same** `PayPal-Request-Id`; a second timeout leaves state `UNKNOWN`, never a new request | Unit (fake PayPal) |
| T6 | Stale PayPal state | Every decision and every correction re-reads the order; the correction is re-evaluated at approval time | Sandbox |
| T7 | Mandate changed after the agent ran or after approval | The mandate can be confirmed once, only in `FUNDED` status; a database CHECK ties the mandate to its confirmation time | Sandbox (409 on a second confirmation) |
| T8 | Agent falsely claims completion | Deterministic claim check against PayPal refunds; flagged in the UI and audit log | Unit (verbatim Gemini text) + sandbox |
| T9 | Unauthenticated use of payment controls | Every `/api/cases*` route requires `Authorization: Bearer <operator token>` (constant-time compare). The browser talks to a Next.js server route that adds the token. | Unit: 15 cases returning 401 |
| T10 | Credential exposure | Secrets only in a git-ignored `.env`; no `NEXT_PUBLIC_*` secrets; the PayPal host is asserted to be the sandbox; logs omit tokens; a secret scan runs before staging | Scan: 0 hits |
| T11 | Webhook replay | **Not applicable yet.** No webhook endpoint is exposed. If added: signature verification plus UNIQUE `event_id`. | — |
| T12 | Operator approval races the agent's own in-mandate refund (both read "$0 refunded" and both execute) | Per-case Postgres advisory lock around authorize->execute; mandate use = max(PayPal COMPLETED+PENDING refunds, local EXECUTING/SUCCEEDED/UNKNOWN ledger) | Sandbox: reproduced $0.50 against a $0.25 mandate (1 of 3) before the fix; 6 of 6 clean after |
| T13 | Public visitors abuse the hosted demo (free-text attacks on our key, unbounded cases, reading other visitors' cases) | Judge mode: access code + signed httpOnly session; seeded cases only; per-session isolation (404); caps per session and per day plus a daily LLM-run cap; allow-listed agents | Unit (`test_demo.py`) + sandbox (isolation, cap) |
| T14 | Agent run fails mid-way and is started again on the same mandate | An `agent_failed` audit event; if any tool call happened, the case is verified and leaves `MANDATE_CONFIRMED`, so it cannot be re-run | Sandbox (quota-429 path) |
| T15 | Replayed or concurrent "Run agent" starts several agent runs on one mandate (double LLM spend, competing proposals) | Atomic claim `MANDATE_CONFIRMED → AGENT_RUNNING`; losers get 409 | Sandbox: reproduced 3 runs on one case before the fix; 1 after |
| T16 | Parallel case creation bypasses the judge-mode caps | Cap check and insert under one advisory lock | Sandbox: reproduced 3 cases against a cap of 1 before the fix; exact after |
| T17 | Stolen or copied judge cookie used indefinitely; access-code brute force with rotating spoofed addresses | Signed issue time, so the server enforces an 8 h expiry; per-address limit (rightmost XFF hop) plus a global login cap; minimum 12-character code; cross-origin POSTs refused | `web/lib/session.test.ts` (7 tests) + curl against the proxy |

## Known limitations (stated, not hidden)

- **Single operator token.** Locally, one shared operator token and no users. In judge mode, cases are partitioned by demo session, but there are still no accounts or roles. Fine for a demo; not for production.
- **The case lock holds one DB connection during the PayPal call** (about 2 s). That is fine at demo scale; production would use a queue.
- **The login limiter is in memory** (one web instance). It must move to shared storage if the web tier scales out.
- **Refunds only.** The mandate model covers one refund action per case. Payouts and invoices were proven feasible in the sandbox but are not wired up.
- **The claim check is pattern-based.** It recognizes affirmative refund claims that state a dollar amount; unusual phrasing can be missed.
- **Provenance tracking is for explanation only.** A value can appear in both trusted and untrusted text, so the mandate check, not provenance, makes the decision.
- **The agent tool has no currency argument.** The wrong-currency case (F3) can only be attempted through the amount; currency enforcement is covered by unit tests.
- **No automated reconciliation job.** `UNKNOWN` tool calls need a manual reconcile (re-send with the same request ID). There is no background sweeper.
- **Sandbox behaviour may differ from live.** For example, in the sandbox an over-limit refund request was reduced to the remaining balance instead of being rejected (see GATE_B_PILOT_REPORT §3.3).
