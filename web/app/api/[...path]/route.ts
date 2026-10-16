// Server-side proxy to the Countersign API. Attaches the operator token so it never reaches the browser.
// Judge mode (COUNTERSIGN_DEMO_ACCESS_CODE set): visitors log in with the access code and get a signed, httpOnly
// session cookie; the proxy forwards that session id so the backend isolates and caps each visitor's cases.
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.COUNTERSIGN_API_URL ?? "http://localhost:8000";
const ACCESS_CODE = process.env.COUNTERSIGN_DEMO_ACCESS_CODE;
const SESSION_SECRET = process.env.COUNTERSIGN_SESSION_SECRET ?? "";
const COOKIE = "cs_session";
const SESSION_HOURS = 8;
const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const LOGIN_MAX_ATTEMPTS = 10;
const ALLOWED = /^(cases(\/[0-9a-f-]{36}(\/(mandate|agent|verify|tool-calls\/[0-9a-f-]{36}\/(approve-correction|reject)))?)?|health|seeds|evidence)$/;

// ponytail: in-memory per-IP login limiter; fine for one instance, move to the DB if the web tier scales out
const attempts = new Map<string, { count: number; since: number }>();

const json = (body: unknown, status: number) => NextResponse.json(body, { status });
const digest = (s: string) => createHash("sha256").update(s).digest();
const sign = (sid: string) => createHmac("sha256", SESSION_SECRET).update(sid).digest("hex");

function sessionFrom(req: NextRequest): string | null {
  const [sid, sig] = (req.cookies.get(COOKIE)?.value ?? "").split(".");
  if (!sid || !sig || !/^[0-9a-f]{32}$/.test(sid)) return null;
  const expected = Buffer.from(sign(sid));
  const given = Buffer.from(sig);
  return given.length === expected.length && timingSafeEqual(given, expected) ? sid : null;
}

function tooManyAttempts(ip: string): boolean {
  const now = Date.now();
  const entry = attempts.get(ip);
  if (!entry || now - entry.since > LOGIN_WINDOW_MS) {
    attempts.set(ip, { count: 1, since: now });
    return false;
  }
  entry.count += 1;
  return entry.count > LOGIN_MAX_ATTEMPTS;
}

async function login(req: NextRequest): Promise<NextResponse> {
  if (!ACCESS_CODE) return json({ error: { message: "judge mode is not enabled" } }, 404);
  if (SESSION_SECRET.length < 32) return json({ error: { message: "server is missing COUNTERSIGN_SESSION_SECRET" } }, 500);
  const ip = (req.headers.get("x-forwarded-for") ?? "local").split(",")[0].trim();
  if (tooManyAttempts(ip)) return json({ error: { message: "too many attempts; try again in 15 minutes" } }, 429);
  const { code } = (await req.json().catch(() => ({}))) as { code?: string };
  if (typeof code !== "string" || !timingSafeEqual(digest(code), digest(ACCESS_CODE))) {
    return json({ error: { message: "wrong access code" } }, 401);
  }
  const sid = randomBytes(16).toString("hex");
  const res = json({ ok: true }, 200);
  res.cookies.set(COOKIE, `${sid}.${sign(sid)}`, {
    httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "strict", path: "/",
    maxAge: SESSION_HOURS * 3600,
  });
  return res;
}

async function forward(req: NextRequest, path: string[]): Promise<NextResponse> {
  const joined = path.join("/");
  if (joined === "session" && req.method === "POST") return login(req);
  const token = process.env.COUNTERSIGN_OPERATOR_TOKEN;
  if (!ALLOWED.test(joined)) return json({ error: { message: "not found" } }, 404);
  if (!token) return json({ error: { message: "server is missing COUNTERSIGN_OPERATOR_TOKEN" } }, 500);
  const headers: Record<string, string> = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  if (ACCESS_CODE && joined !== "health") {
    const sid = sessionFrom(req);
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
