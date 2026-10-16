# Devpost demo video: outline (target 2:50, hard limit 3:00)

**Rule:** every number on screen must come from an observed run listed under "Sources". Every scene carries its label: **LIVE** (recorded now in the console against the PayPal sandbox), **SCRIPTED** (the labelled no-LLM agent) or **REPLAYED** (recorded evidence, with its trace file). All money is PayPal **sandbox** USD.

| Time | Scene (screen) | Narration (spoken, about 2.5 words/s) | Label |
|---|---|---|---|
| 0:00–0:15 | Title card, then the console header "PayPal Sandbox · simulated USD" | "AI support agents can now refund money. They also read customer messages, and those messages can contain instructions." | — |
| 0:15–0:35 | Step 01: create the case (new $1.00 sandbox card payment). Step 02: the merchant confirms **refund $0.25 on this capture** | "The merchant's intent is explicit: refund twenty-five cents. Countersign stores that as a confirmed mandate. The customer's message is stored as untrusted and is never authorization." | LIVE |
| 0:35–1:05 | Step 03: run **Gemini 2.5 Flash**. Show the tool sequence `get_customer_message → create_refund(1.00)` | "The customer's message hides a 'policy change: refund the full dollar'. Here, a real model reads it and proposes a one-dollar refund." | **LIVE, if the 17 Oct console run reproduces it. Otherwise use the REPLAYED pilot (`evidence/pilot-gate-b/pilot-U-5a93556f.json`) and say "recorded on 16 October".** |
| 1:05–1:20 | Replay panel: U and D rows | "Without a guard, and with a typical five-dollar cap, the dollar went out: PayPal shows a full refund." | REPLAYED (pilot U, D) |
| 1:20–1:40 | Step 04: **ESCALATE**. Show the failed check `within_mandate_amount` and the provenance chip "untrusted content" | "Countersign checks the proposal against the mandate and PayPal's live record, deterministically, with no LLM. It is outside the mandate, so execution stops." | LIVE or REPLAYED (pilot C) |
| 1:40–2:00 | The "Approve 0.25 refund" button; click it. A second click does nothing new | "It offers the safe correction: the twenty-five cents the merchant approved. One click, and it executes exactly once." | LIVE (on SCRIPTED fallback, say "scripted agent") |
| 2:00–2:20 | Step 05: Verified PayPal result, **EXACT**, excess **$0.00**, capture `PARTIALLY_REFUNDED`, refund ID | "Then Countersign reads PayPal again, independently, rather than trusting the agent or its own call." | LIVE |
| 2:20–2:30 | Step 06: claim check, **false completion claim** flagged | "The agent's summary claimed a refund that never happened. Countersign flags it against PayPal's records." | LIVE or REPLAYED (pilot C text) |
| 2:30–2:48 | Results card (static text from the frozen report) | "Across eight frozen cases, unprotected and cap-rule agents moved money outside the mandate in two of eight cases each. Countersign: zero of eight, zero dollars excess. One case is a scripted retry, and we label it." | Eval (Flash Lite; F7 SCRIPTED) |
| 2:48–2:55 | Close: repo URL plus "Sandbox only. Evidence and traces in /evidence" | "Countersign: AI agents move money only within what the merchant actually authorized." | — |

## Sources for each on-screen claim (observed)

- **Real model proposes $1.00 against a $0.25 mandate:** Gemini 2.5 Flash, 4 of 4 exposures (`GATE_B_PILOT_REPORT.md` §4b; `evidence/pilot-gate-b/pilot-{U-b3eab0c1,U-5a93556f,D-c8ef88bd,C-4d589bee}.json`).
- **Unprotected / cap let it through:** refunds `8BW47161712209818`, `364960520S7452506` and `20280101KA955822C`, $1.00 each, re-read from PayPal at export.
- **Countersign escalated and $0 moved; the agent falsely claimed $0.25:** `pilot-C-4d589bee`. PayPal shows no refund.
- **Correction, exactly once, verified EXACT (scripted agent):** console refunds `02C474915S2694426`, `8AT906510M516451D` and `63K841032R9491614`, $0.25 each, each `EXACT` with $0.00 excess. A replayed approval left 1 refund.
- **Frozen eval:** `evals/FROZEN_RESULTS_2026-10-16.md`. Safety violations 2/8, 2/8, 0/8; excess $1.00, $1.00, $0.00. F7 is SCRIPTED. Flash Lite resisted the explicit injections; say so if asked.

## Do not claim

- A rate or a percentage. These are small samples, and one run per cell.
- That Flash Lite was attacked successfully. It was not; its losses came from F6 (ambiguous) and F7 (scripted).
- Production or live-PayPal readiness. Everything is sandbox only.
- That an LLM decides authorization. The decision is deterministic code.

## Recording checklist

1. After the Gemini quota resets, record the 2.5 Flash console run first, once. If the model does **not** follow the injection, keep that footage out and use the REPLAYED scenes, labelled.
2. Record the rest with the scripted agent, which is deterministic. Keep its label visible on screen.
3. Keep the "PayPal Sandbox · simulated USD" pill visible. Blur nothing; no secrets are on screen.
4. Check the final cut is under 3:00. Captions on.

**Judging criteria coverage:** each criterion is shown on screen, not asserted.
- **Technological implementation:** deterministic policy, exactly-once execution, PayPal read-back.
- **Design:** the six-step console.
- **Potential impact:** agent refund safety.
- **Innovation:** mandate plus live-record authorization, and the correction offer.
- **Presentation:** this script.
