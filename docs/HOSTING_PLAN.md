# Hosted judge demo: safety, access and cost plan (proposal, nothing created)

**Goal:** judges open a URL, enter an access code, and run real Countersign cases against the PayPal **sandbox**, with no keys of their own. The demo must stay up until judging ends on **15 December 2026** (judging runs 1–15 Dec; winners are announced around 21 Dec).

**Status:** judge mode is implemented and tested locally (see below). **No Render resources exist, and no billing is enabled.** Creating anything needs your approval.

## 1. What a public visitor can and cannot do

| Control | Where it is enforced | How |
|---|---|---|
| **Access gate** | Web proxy (`web/app/api/[...path]/route.ts`, rules in `web/lib/session.ts`) | Every API route except `/health` needs a session. A session comes from `POST /api/session` with the judge access code, compared in constant time. The session is a random 128-bit id plus its issue time, signed with HMAC-SHA256, in an **httpOnly, Secure, SameSite=Strict** cookie. The **server** rejects it after 8 h even if a browser keeps it. The proxy refuses to start judge mode if the access code is shorter than 12 characters or the signing secret is shorter than 32. Login allows 10 attempts per address and **200 in total** per 15 min, so rotating addresses does not help. The address is the rightmost `X-Forwarded-For` hop, the one the hosting proxy added. Cross-origin browser POSTs are refused (403). |
| **Operator token never in the browser** | Web proxy | The proxy alone holds `COUNTERSIGN_OPERATOR_TOKEN`. It forwards only allow-listed paths. The backend rejects any request without the token (401). |
| **Seeded cases only** | Backend (`countersign/demo.py`, `COUNTERSIGN_DEMO_MODE=judge`) | `POST /api/cases` must name a frozen seed (F1–F8). Visitor text, amount and guard are ignored. Each case uses a fixed $1.00 sandbox payment under the `countersign` guard. |
| **Per-session isolation** | Backend | Each case stores its `session_id`. Every read or action on a case from another session returns 404. |
| **Bounded money** | Backend + PayPal | The mandate can never exceed the case's own $1.00 sandbox capture. Countersign authorizes every refund against the live PayPal record and the local ledger, under a per-case lock. |
| **Request limits** | Backend | 8 cases per session. 60 demo cases per rolling 24 h. Both caps are exact under parallel requests, because the check and the insert run under one lock. 25 live-model agent runs per 24 h, counted at start so failed runs count too. 12 LLM requests per run. Over a limit, the API returns **429** with a clear message. In judge mode, `/verify` is closed (every step already verifies), so visitors cannot generate extra PayPal calls. |
| **Replay / double submit** | Backend + PayPal | A case's agent runs **once**: an atomic `MANDATE_CONFIRMED → AGENT_RUNNING` claim, so concurrent or replayed "Run agent" requests get 409. The mandate can be confirmed once. An approval executes at most once (per-case lock, unique correction, `PayPal-Request-Id`). A replayed approval returns the same refund. |
| **Gemini quota exhausted** | Backend + UI | A provider 429 becomes `QUOTA_EXHAUSTED` (HTTP 429) with a message that points to the labelled scripted agent. Nothing moves, and the case stays retryable. Judges can always finish a genuine sandbox case (payment → escalation → approval → refund → PayPal read-back) with the scripted agent and **no API key of their own**. |
| **API spend** | Backend + provider | Agents are allow-listed: Gemini Flash Lite, Gemini 2.5 Flash and the scripted adversary. No other model can be named. The Gemini key is free tier only, with no billing account attached, so the most it can cost is $0. When the daily budget is used, only the scripted agent runs. |
| **Sandbox only** | Backend config | `PAYPAL_BASE_URL` must be `https://api-m.sandbox.paypal.com`, or startup fails. Every request asserts the sandbox host. Use a **separate sandbox REST app** for the hosted demo. |
| **Operator access** | Local only | The unrestricted console (free text, all guards, all agents) runs only without `COUNTERSIGN_DEMO_MODE`. It is never exposed publicly. |

**Tests:**
- `web/lib/session.test.ts`, run with `npm test`: expiry, tampering, spoofed addresses, global limiter, cross-origin.
- `backend/tests/test_demo.py`, offline: sessions, agent allow-list, seeds, caps, closed re-verify, quota message.
- `test_vertical_slice_sandbox.py`, against the real sandbox: isolation, seed text, caps, parallel creates, concurrent agent runs.

**Two of these defects were reproduced before they were fixed:**
- 3 agent runs started on one case;
- 3 cases were created against a cap of 1.

**Verified end to end through the judge proxy on 16 Oct:**
- login, then seed F1;
- the live model was refused with 429, and the case was left untouched;
- scripted agent: $1.00 ESCALATED;
- approval: refund `8AT906510M516451D` $0.25, `EXACT`, with the false claim flagged;
- a replayed approval still left 1 refund;
- re-running the agent gave 409, and re-verify gave 403;
- a second visitor got 401 without logging in and 404 after logging in;
- free text was refused with 403.

## 2. Judge demo mode: what is live and what is replayed

- **LIVE**: every case a judge creates. That is a new $1.00 sandbox card payment, a real agent run, Countersign's decision, the judge's one-click approval, the PayPal refund, and an independent PayPal read-back with claim checking.
- **SCRIPTED**: seed F7 (a duplicate retry) and the "Scripted adversary" agent. Both are labelled in the UI. They make the attack reproducible without any model quota.
- **REPLAYED**: the four Gemini 2.5 Flash pilot runs. They are shown from `evidence/pilot-gate-b/*.json`, with sha256 and the PayPal re-read made at export. They do not run per judge, because that model's free tier allows 20 requests a day. The UI marks them REPLAYED.

**Verified locally on 16 Oct:**
- a wrong access code was refused;
- after login, seed F1 ran with the scripted adversary, and its $1.00 was ESCALATED;
- the judge approved the $0.25 correction, and PayPal refund `02C474915S2694426` came back COMPLETED;
- the read-back was `EXACT`, with $0.00 excess;
- the false "$1.00 processed" claim was flagged;
- the replay panel showed all four recorded runs.

## 3. Cost, reconfirmed on render.com/pricing and render.com/docs/free (16 Oct 2026)

| Item | Price |
|---|---|
| Workspace **Hobby** | $0/month, plus compute. Includes 5 GB bandwidth and 500 build minutes a month |
| Web service, 512 MB, below 1 CPU (Starter) | **$7/month**, prorated per second |
| Postgres, 256 MB (Basic-256mb) | **$6/month**, 1 GB SSD included, then $0.30 per GB |
| Free web service | $0. Spins down after 15 min idle; about 1 min cold start; 750 instance-hours a month per workspace |
| Free Postgres | $0, but **expires 30 days after creation** (14-day grace period). It would expire before 15 Dec, so it is not usable |
| Hackathon credit | **$50 of Render credits** (Devpost Render page). Credits are drawn down from a balance that is billed at month end, so **any overrun is charged to the card on file** |

**Option A (recommended):** two Starter web services (API and web) plus Basic-256mb Postgres, about $20/month. From around 5 Nov to 15 Dec that is about 1.35 months, roughly **$27**, inside the $50 credit. Everything stays on, with no cold starts during judging.

**Option B (cheapest):** two free web services plus paid Postgres, about **$8** in total. The catch is a cold start of about 1 minute after 15 idle minutes, which a judge will notice on first load.

**Guardrails before creating anything:**
- claim and apply the credit first;
- set a Render spend notification;
- create only the three resources above;
- delete them after 21 Dec.

## 4. Deployment steps (after your approval only)

The repository includes **`render.yaml`**, a Render Blueprint that has been validated against Render's published schema. Applying it **creates paid resources**:

| Resource | Type | Plan | Notes |
|---|---|---|---|
| `countersign-db` | Postgres 17 | `0.1c-256mb` (about $6/month) | `ipAllowList: []`, so no external connections |
| `countersign-api` | **Private service** (no public URL) | `0.5c-512mb` (about $7/month) | Judge mode on, sandbox host fixed, operator token generated |
| `countersign-web` | Web service (the only public endpoint) | `0.5c-512mb` (about $7/month) | Gets the API address and operator token from the API service; session secret generated |

Auto-deploy is **off** on both services, so a push never redeploys during judging.

1. **Prepare credentials** (free):
   - create a new PayPal sandbox REST app used only for the demo;
   - create a Gemini API key in a separate Google project with no billing account, so judges never share the development quota;
   - generate an access code: `openssl rand -base64 12`, which gives 16 characters.
2. **Claim the $50 Render credit** from the Devpost Render page. Set a spend notification.
3. **Apply the Blueprint:** Render Dashboard → New → Blueprint → choose `shro-2002/countersign`, branch `main`. Render prompts for the `sync: false` values:
   - `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`;
   - `GEMINI_API_KEY`;
   - `COUNTERSIGN_DEMO_ACCESS_CODE`.

   **This is the step that starts billing.**
4. **Wait for both deploys.** The schema migrates when the API starts. The web health check `/api/health` passes only when the web service can reach the API.
5. **Smoke test** on the public URL:
   - a wrong code returns 401;
   - log in, then run seed F1 with the scripted agent: ESCALATE, approve, then `EXACT` with $0.00 excess;
   - run seed F5 (a benign full refund): ALLOW, then `EXACT`;
   - run seed F7 (scripted duplicate): the second call is escalated;
   - a second browser session cannot see the first session's case;
   - the replay panel lists 4 runs.
6. **Publish access:** put the URL and access code in the Devpost testing instructions only. Never commit them.
7. **Rehearsed locally on 16 Oct.** The setup:
   - a clean export with no `.env`, configured through environment variables only;
   - API in judge mode;
   - `next build` plus `next start` with `NODE_ENV=production`;
   - the API address given scheme-less (`localhost:8010`), as Render's private-network `hostport` is.

   Results:
   - health check through the web service: 200;
   - wrong code: 401;
   - session cookie: `Secure; HttpOnly; SameSite=strict`, 8 h;
   - F1: ESCALATE, then approve, then refund `63K841032R9491614` $0.25 `EXACT`;
   - F5: ALLOW, then refund `0HV06203F1888400R` $1.00 `EXACT`;
   - F7: Flash Lite was requested, but the forced scripted duplicate ran. Its second call was escalated, then refund `16U312847X812183C` $0.25 `EXACT`;
   - second session: 404;
   - replay runs: 4.

**Rollback:** delete the three resources in the Render Dashboard. Revoke the demo sandbox app and the demo Gemini key.

**After judging (around 21 Dec):** delete the resources to stop billing.
