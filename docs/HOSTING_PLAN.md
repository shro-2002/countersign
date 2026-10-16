# Hosted judge demo: safety, access and cost plan (proposal, nothing created)

**Goal:** judges open a URL, enter an access code, and run real Countersign cases against the PayPal **sandbox**, with no keys of their own. The demo must stay up until judging ends on **15 December 2026** (judging runs 1–15 Dec; winners are announced around 21 Dec).

**Status:** judge mode is implemented and tested locally (see below). **No Render resources exist, and no billing is enabled.** Creating anything needs your approval.

## 1. What a public visitor can and cannot do

| Control | Where it is enforced | How |
|---|---|---|
| **Access gate** | Web proxy (`web/app/api/[...path]/route.ts`) | Every API route except `/health` needs a session. A session comes from `POST /api/session` with the judge access code: a timing-safe compare, then a random 128-bit session id in an **httpOnly, Secure, SameSite=Strict** cookie signed with HMAC-SHA256. It expires after 8 h. Login is limited to 10 attempts per IP per 15 min. |
| **Operator token never in the browser** | Web proxy | The proxy alone holds `COUNTERSIGN_OPERATOR_TOKEN`. It forwards only allow-listed paths. The backend rejects any request without the token (401). |
| **Seeded cases only** | Backend (`countersign/demo.py`, `COUNTERSIGN_DEMO_MODE=judge`) | `POST /api/cases` must name a frozen seed (F1–F8). Visitor text, amount and guard are ignored. Each case uses a fixed $1.00 sandbox payment under the `countersign` guard. |
| **Per-session isolation** | Backend | Each case stores its `session_id`. Every read or action on a case from another session returns 404. |
| **Bounded money** | Backend + PayPal | The mandate can never exceed the case's own $1.00 sandbox capture. Countersign authorizes every refund against the live PayPal record and the local ledger, under a per-case lock. |
| **Request limits** | Backend | 8 cases per session. 60 demo cases per rolling 24 h. 25 live-model agent runs per 24 h, counted at start so failed runs count too. 12 LLM requests per run. Over a limit, the API returns **429** with a clear message. |
| **API spend** | Backend + provider | Agents are allow-listed: Gemini Flash Lite, Gemini 2.5 Flash and the scripted adversary. No other model can be named. The Gemini key is free tier only, with no billing account attached, so the most it can cost is $0. When the daily budget is used, only the scripted agent runs. |
| **Sandbox only** | Backend config | `PAYPAL_BASE_URL` must be `https://api-m.sandbox.paypal.com`, or startup fails. Every request asserts the sandbox host. Use a **separate sandbox REST app** for the hosted demo. |
| **Operator access** | Local only | The unrestricted console (free text, all guards, all agents) runs only without `COUNTERSIGN_DEMO_MODE`. It is never exposed publicly. |

Tests: `backend/tests/test_demo.py` covers sessions, agent allow-list and seeds, offline. `test_vertical_slice_sandbox.py` covers isolation, seed text and caps against the real sandbox.

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

1. Create a new sandbox REST app for the demo. Its credentials go only into Render secrets.
2. Claim the $50 credit. Set up the Hobby workspace.
3. Create Postgres (Basic-256mb). Create `countersign-api` with `COUNTERSIGN_DEMO_MODE=judge`, the PayPal sandbox values, `COUNTERSIGN_DB_URL`, `COUNTERSIGN_OPERATOR_TOKEN` and `GEMINI_API_KEY`. The schema migrates on startup.
4. Create `countersign-web` with `COUNTERSIGN_API_URL` (the private URL of the API), `COUNTERSIGN_OPERATOR_TOKEN`, `COUNTERSIGN_DEMO_ACCESS_CODE` and `COUNTERSIGN_SESSION_SECRET` (32 bytes or more of random data).
5. Smoke test:
   - a wrong code returns 401;
   - seeds F1, F5 and F7 run end to end;
   - a second browser session cannot see the first session's cases;
   - the replay panel loads.
6. Put the URL and access code in the Devpost testing instructions. Do not commit them.

**Rollback:** delete the three services. Revoke the demo sandbox app.
