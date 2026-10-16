// Typed client for the Countersign backend. The browser never sees PayPal or LLM credentials.

// Same-origin calls go through app/api/[...path]/route.ts, which adds the operator token server-side.
export const API_BASE = "";

export type Check = { name: string; passed: boolean; detail: string };

export type ToolCall = {
  id: string;
  seq: number;
  origin: "agent" | "operator_correction";
  action: string;
  capture_id: string;
  amount: string;
  currency: string;
  verdict: "ALLOW" | "ESCALATE" | "BLOCK";
  checks: Check[];
  provenance: Record<string, string[]>;
  correction: { action?: string; capture_id?: string; amount?: string; currency?: string; for_tool_call?: string } | null;
  state: string;
  paypal_response: Record<string, unknown> | null;
};

export type Refund = { refund_id: string; amount: string; currency: string; status: string };

export type Verification = {
  outcome: string;
  exact_success: boolean;
  legit_task_completed: boolean;
  excess_unauthorized_amount: string;
  unauthorized_proposals: number;
  blocked_unauthorized_proposals: number;
  false_completion_claim: boolean;
  attack_encountered_before_money_call: boolean;
  capture_status: string;
  paypal_refunds: Refund[];
};

export type ClaimCheck = { sentence: string; claimed_amount: string; supported: boolean; evidence: string };

export type Case = {
  id: string;
  status: string;
  operator_instruction: string;
  untrusted_message: string;
  order_id: string;
  capture_id: string;
  capture_amount: string;
  currency: string;
  mandate: { action: string; capture_id: string; amount: string; currency: string } | null;
  agent_model: string | null;
  agent_transcript: { tool_calls: { tool: string; args: Record<string, string> }[]; final_text: string;
    usage: Record<string, number>; tool_sequence: string[] } | null;
  claim_checks: ClaimCheck[] | null;
  verification: Verification | null;
};

export type CaseEvent = { ts: string; kind: string; detail: Record<string, unknown> };
export type CaseView = { case: Case; tool_calls: ToolCall[]; events: CaseEvent[] };

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) { super(message); }
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data?.error?.message ?? `Request failed (${res.status})`, res.status, data?.error?.code);
  return data as T;
}

const call = (path: string, body?: unknown) => request<CaseView>(path, body);

export type Seed = { id: string; category: string; operator_instruction: string; untrusted_message: string;
  suggested_mandate: string; agent: string | null };

export type Replay = { source: "REPLAYED_EVIDENCE"; run: string; guard: string; agent: string; trace_file: string;
  sha256: string; agent_tool_calls: { tool: string; args: Record<string, string> }[];
  guard_decisions: { amount: string; verdict: string }[]; agent_final_text: string;
  paypal_refunds_reverified: Refund[]; reverified_matches_report: boolean };

export const api = {
  seeds: () => request<{ seeds: Seed[]; demo_mode: boolean }>("/api/seeds"),
  evidence: () => request<{ replay: Replay[] }>("/api/evidence"),
  login: (code: string) => request<{ ok: boolean }>("/api/session", { code }),
  createCase: (operator_instruction: string, untrusted_message: string) =>
    call("/api/cases", { operator_instruction, untrusted_message, payment_amount: "1.00" }),
  createSeededCase: (seed: string) => call("/api/cases", { seed }),
  confirmMandate: (id: string, capture_id: string, amount: string) =>
    call(`/api/cases/${id}/mandate`, { action: "refund", capture_id, amount, currency: "USD" }),
  runAgent: (id: string, model: string) => call(`/api/cases/${id}/agent`, { model }),
  approve: (id: string, toolCallId: string) => call(`/api/cases/${id}/tool-calls/${toolCallId}/approve-correction`, {}),
  reject: (id: string, toolCallId: string) => call(`/api/cases/${id}/tool-calls/${toolCallId}/reject`, {}),
  get: (id: string) => call(`/api/cases/${id}`),
};
