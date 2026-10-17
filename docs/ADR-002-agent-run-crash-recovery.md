# ADR-002: Recovering cases stuck in AGENT_RUNNING after a crash

**Status:** proposed (not implemented). **Date:** 17 Oct 2026.

## Problem

`run_agent` atomically claims a case (`MANDATE_CONFIRMED → AGENT_RUNNING`). If the API process dies mid-run (deploy, OOM, host restart), nothing moves the case on. It stays `AGENT_RUNNING` forever and cannot be re-run, approved, or closed except by hand.

## Where a crash can leave money

| Crash point | Tool-call state | Did PayPal move money? |
|---|---|---|
| Before any proposal | none | No |
| After authorize, before the execution claim | `APPROVED` (no `paypal_request_id`) | No. No request was sent |
| After the claim, during or after the PayPal call | `EXECUTING` (has `paypal_request_id`) | **Unknown** |
| After the result was recorded | `SUCCEEDED` / `FAILED` / `UNKNOWN` | Recorded |

**What is already safe today:**
- `EXECUTING` and `UNKNOWN` rows count as **spent mandate**. Every authorization uses `max(PayPal refunds, local ledger)`, so a stuck row can block the remaining mandate but can never allow a second spend.
- Advisory locks are session-scoped, so Postgres releases them when a crashed process's connection drops.

## Decision (proposed)

### 1. Prove the runner is dead without timing guesses

While the agent loop runs, `run_agent` holds a second advisory lock, `agent:{case_id}`, on its own connection. A dead process loses its connection, and with it the lock. Recovery first calls `pg_try_advisory_lock('agent:{case_id}')`:
- **Fails:** a live runner holds it, so recovery refuses with 409.
- **Succeeds:** the runner is gone.

### 2. Make PayPal matching deterministic

Send `custom_id = tool_call_id` on every refund. This is confirmed in PayPal's published Payments v2 schema: "The API caller-provided external ID. Used to reconcile API caller-initiated transactions", max length 127, and it is returned on the refund resource. Recovery then never has to guess by amount or time.

### 3. An operator-only `POST /api/cases/{id}/reconcile`, read-only toward PayPal

Under `case_lock`, and only after step 1 succeeds:
1. Read the order's refunds, then `GET /v2/payments/refunds/{id}` for each one to read `custom_id`.
2. For each tool call in `EXECUTING` or `UNKNOWN`:
   - **A PayPal refund carries its `custom_id`:** mark the tool call `SUCCEEDED` and record that refund.
   - **No matching refund, and the row is older than a grace period:** mark it `NOT_EXECUTED`. The grace period is 15 minutes, well beyond the 20 s HTTP timeout and one reconcile re-send, which rules out a late-landing request from a zombie process.
   - **No matching refund, but still within the grace period:** leave it `UNKNOWN`. It keeps counting as spent, and the operator is told to try again later.
3. `APPROVED` rows with no `paypal_request_id` become `NOT_EXECUTED`, because no request was ever sent.
4. Run `verify()`, which records PayPal's state and moves the case to `VERIFIED` or `VERIFICATION_FAILED`. Every step writes an audit event (`reconcile_started`, `tool_call_reconciled`, `reconcile_finished`).

### 4. Retry rules: never automatic

- **No background re-send.** At most, a read-only sweeper lists stale `AGENT_RUNNING` cases for the operator.
- **Agent re-run:** offered only if the case had **zero** tool calls and PayPal shows **zero** refunds on the capture. It requires an explicit "release for retry" click (`AGENT_RUNNING → MANDATE_CONFIRMED`, with an audit event).
- **Any proposal happened:** no agent re-run. Any remaining in-mandate amount goes through the existing correction approval, which re-authorizes against fresh PayPal state.
- **Never** re-send a reconciled request with its old `PayPal-Request-Id`. That would execute an old authorization under new conditions.

## Consequences

- Recovery costs one extra DB connection per running agent, and one extra PayPal GET per refund during reconcile.
- The schema gains `NOT_EXECUTED` in the tool-call state CHECK. The refund body gains `custom_id`, which needs a sandbox check that it round-trips before rollout.
- Tests (sandbox), all against the real PayPal sandbox:
  - kill the runner mid-loop, simulated by closing its lock connection;
  - reconcile finds the refund via `custom_id`;
  - reconcile inside the grace period keeps the row as spent;
  - reconcile with a live runner returns 409;
  - zero-proposal release allows exactly one re-run.
