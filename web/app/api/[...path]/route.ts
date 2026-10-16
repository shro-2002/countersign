// Server-side proxy to the Countersign API. Attaches the operator token so it never reaches the browser.
// Judge mode (COUNTERSIGN_DEMO_ACCESS_CODE set): visitors log in with the access code and get a signed, httpOnly,
// server-expiring session cookie; the proxy forwards that session id so the backend isolates and caps each visitor.
import { NextRequest, NextResponse } from "next/server";

import {
  clientIp, codeMatches, isCrossOrigin, issueSession, LoginLimiter, MIN_ACCESS_CODE_LENGTH, MIN_SESSION_SECRET_LENGTH,
  readSession, SESSION_HOURS,
} from "@/lib/session";

const ACCESS_CODE = process.env.COUNTERSIGN_DEMO_ACCESS_CODE;
const SESSION_SECRET = process.env.COUNTERSIGN_SESSION_SECRET ?? "";
const COOKIE = "cs_session";
const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const ALLOWED = /^(cases(\/[0-9a-f-]{36}(\/(mandate|agent|verify|tool-calls\/[0-9a-f-]{36}\/(approve-correction|reject)))?)?|health|seeds|evidence)$/;

// ponytail: in-memory limiter is per web instance; move it to the DB if the web tier ever runs more than one
const limiter = new LoginLimiter(10, 200, LOGIN_WINDOW_MS);

const json = (body: unknown, status: number) => NextResponse.json(body, { status });

const RAW_API_URL = process.env.COUNTERSIGN_API_URL ?? "http://localhost:8000";
const API_URL = RAW_API_URL.includes("://") ? RAW_API_URL : `http://${RAW_API_URL}`; // Render hostport has no scheme

/** Judge mode refuses to run with a guessable code or a short signing secret (fail closed). */
function judgeConfigError(): string | null {
  if ((ACCESS_CODE ?? "").length < MIN_ACCESS_CODE_LENGTH) return `access code must be at least ${MIN_ACCESS_CODE_LENGTH} characters`;
  if (SESSION_SECRET.length < MIN_SESSION_SECRET_LENGTH) return "COUNTERSIGN_SESSION_SECRET is missing or too short";
  return null;
}

// Settings are fixed at startup, so check them once. Non-null means every judge-mode request fails closed.
const JUDGE_CONFIG_ERROR = ACCESS_CODE ? judgeConfigError() : null;
const misconfigured = () => json({ error: { message: `server misconfigured: ${JUDGE_CONFIG_ERROR}` } }, 500);

async function login(req: NextRequest): Promise<NextResponse> {
  if (!ACCESS_CODE) return json({ error: { message: "judge mode is not enabled" } }, 404);
  if (JUDGE_CONFIG_ERROR) return misconfigured();
  if (limiter.isLimited(clientIp(req.headers.get("x-forwarded-for")))) {
    return json({ error: { message: "too many attempts; try again in 15 minutes" } }, 429);
  }
  const { code } = (await req.json().catch(() => ({}))) as { code?: unknown };
  if (!codeMatches(code, ACCESS_CODE)) return json({ error: { message: "wrong access code" } }, 401);
  const { cookie } = issueSession(SESSION_SECRET);
  const res = json({ ok: true }, 200);
  res.cookies.set(COOKIE, cookie, {
    httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "strict", path: "/",
    maxAge: SESSION_HOURS * 3600,
  });
  return res;
}

async function forward(req: NextRequest, path: string[]): Promise<NextResponse> {
  if (req.method === "POST" && isCrossOrigin(req.headers.get("origin"), req.headers.get("host"))) {
    return json({ error: { message: "cross-origin request refused" } }, 403);
  }
  const joined = path.join("/");
  if (joined === "session" && req.method === "POST") return login(req);
  const token = process.env.COUNTERSIGN_OPERATOR_TOKEN;
  if (!ALLOWED.test(joined)) return json({ error: { message: "not found" } }, 404);
  if (!token) return json({ error: { message: "server is missing COUNTERSIGN_OPERATOR_TOKEN" } }, 500);
  const headers: Record<string, string> = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  if (ACCESS_CODE && joined !== "health") {
    if (JUDGE_CONFIG_ERROR) return misconfigured();
    const sid = readSession(req.cookies.get(COOKIE)?.value, SESSION_SECRET);
    if (!sid) return json({ error: { code: "LOGIN_REQUIRED", message: "enter the judge access code" } }, 401);
    headers["X-Countersign-Session"] = sid;
  }
  const res = await fetch(`${API_URL}/api/${joined}`, {
    method: req.method,
    headers,
    body: req.method === "POST" ? await req.text() : undefined,
    cache: "no-store",
  });
  return new NextResponse(await res.text(), { status: res.status, headers: { "Content-Type": "application/json" } });
}

type Ctx = { params: Promise<{ path: string[] }> };
export async function GET(req: NextRequest, ctx: Ctx) { return forward(req, (await ctx.params).path); }
export async function POST(req: NextRequest, ctx: Ctx) { return forward(req, (await ctx.params).path); }
