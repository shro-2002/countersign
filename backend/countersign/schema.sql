-- Countersign MVP schema. Idempotent: safe to run on every startup.

CREATE TABLE IF NOT EXISTS cases (
    id                  uuid PRIMARY KEY,
    status              text NOT NULL CHECK (status IN (
                            'FUNDED', 'MANDATE_CONFIRMED', 'AGENT_COMPLETE', 'AWAITING_APPROVAL',
                            'EXECUTING', 'VERIFIED', 'VERIFICATION_FAILED', 'FAILED')),
    operator_instruction text NOT NULL,
    untrusted_message   text NOT NULL,          -- attacker-controllable; never used for authorization
    order_id            text NOT NULL,
    capture_id          text NOT NULL,
    capture_amount      numeric(12, 2) NOT NULL CHECK (capture_amount > 0),
    currency            char(3) NOT NULL,
    mandate             jsonb,                  -- set only by explicit operator confirmation
    mandate_confirmed_at timestamptz,
    agent_model         text,
    agent_transcript    jsonb,
    claim_checks        jsonb,
    verification        jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mandate_needs_confirmation CHECK ((mandate IS NULL) = (mandate_confirmed_at IS NULL))
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id              uuid PRIMARY KEY,
    case_id         uuid NOT NULL REFERENCES cases(id),
    seq             integer NOT NULL,
    origin          text NOT NULL CHECK (origin IN ('agent', 'operator_correction')),
    action          text NOT NULL,
    capture_id      text NOT NULL,
    amount          numeric(12, 2) NOT NULL CHECK (amount > 0),
    currency        char(3) NOT NULL,
    verdict         text NOT NULL CHECK (verdict IN ('ALLOW', 'ESCALATE', 'BLOCK')),
    checks          jsonb NOT NULL,
    provenance      jsonb NOT NULL,
    correction      jsonb,
    state           text NOT NULL CHECK (state IN (
                        'ESCALATED', 'BLOCKED', 'APPROVED', 'REJECTED', 'EXECUTING',
                        'SUCCEEDED', 'FAILED', 'UNKNOWN')),
    paypal_request_id text UNIQUE,             -- one PayPal request id per execution: exactly-once guard
    paypal_response jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (case_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
    id          bigserial PRIMARY KEY,
    case_id     uuid NOT NULL REFERENCES cases(id),
    ts          timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_case_id ON tool_calls(case_id);
CREATE INDEX IF NOT EXISTS idx_events_case_id ON events(case_id, id);

-- At most one operator correction per escalated tool call (guards double-click / concurrent approvals).
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_correction_per_escalation
    ON tool_calls ((correction->>'for_tool_call')) WHERE origin = 'operator_correction';

-- Evaluation baselines: 'none' (unprotected) and 'cap' (amount-cap rule). The console always uses 'countersign'.
ALTER TABLE cases ADD COLUMN IF NOT EXISTS guard text NOT NULL DEFAULT 'countersign'
    CHECK (guard IN ('countersign', 'cap', 'none'));

-- Judge demo mode: which demo session owns a case, and which frozen seed it was created from (NULL in the console).
ALTER TABLE cases ADD COLUMN IF NOT EXISTS session_id text;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS seed text;
CREATE INDEX IF NOT EXISTS idx_cases_session_id ON cases(session_id);
CREATE INDEX IF NOT EXISTS idx_cases_created_at ON cases(created_at);
