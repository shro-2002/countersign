// Judge-mode session and login rules. Pure (node:crypto only) so they are unit-tested by `npm test`.
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const SESSION_HOURS = 8;
export const MIN_ACCESS_CODE_LENGTH = 12;
export const MIN_SESSION_SECRET_LENGTH = 32;
const SESSION_MS = SESSION_HOURS * 3600 * 1000;
const MAX_TRACKED_KEYS = 10_000;

const hmac = (secret: string, value: string) => createHmac("sha256", secret).update(value).digest("hex");
const digest = (value: string) => createHash("sha256").update(value).digest();

function sameText(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** New session: a random 128-bit id plus its issue time, signed so the server can enforce expiry itself. */
export function issueSession(secret: string, now: number = Date.now()): { sid: string; cookie: string } {
  const sid = randomBytes(16).toString("hex");
  const payload = `${sid}.${now}`;
  return { sid, cookie: `${payload}.${hmac(secret, payload)}` };
}

/** The session id if the cookie is untampered and younger than SESSION_HOURS; otherwise null. */
export function readSession(cookie: string | undefined, secret: string, now: number = Date.now()): string | null {
  const [sid, issued, sig, extra] = (cookie ?? "").split(".");
  if (extra !== undefined || !/^[0-9a-f]{32}$/.test(sid ?? "") || !/^\d{13}$/.test(issued ?? "") || !sig) return null;
  if (!sameText(sig, hmac(secret, `${sid}.${issued}`))) return null;
  const age = now - Number(issued);
  return age >= 0 && age <= SESSION_MS ? sid : null;
}

export function codeMatches(given: unknown, expected: string): boolean {
  return typeof given === "string" && timingSafeEqual(digest(given), digest(expected));
}

/** Rightmost X-Forwarded-For hop: the address our hosting proxy saw. Earlier hops are client-supplied. */
export function clientIp(forwardedFor: string | null): string {
  const hops = (forwardedFor ?? "").split(",").map((hop) => hop.trim()).filter(Boolean);
  return hops.at(-1) ?? "local";
}

/** True for cross-site browser POSTs. Requests without Origin (non-browser clients) still need a valid cookie. */
export function isCrossOrigin(origin: string | null, host: string | null): boolean {
  if (!origin) return false;
  try {
    return new URL(origin).host !== host;
  } catch {
    return true;
  }
}

type Window = { count: number; since: number };

/** Fixed-window login limiter: per client key AND globally, so rotating addresses cannot brute-force the code. */
export class LoginLimiter {
  private readonly windows = new Map<string, Window>();
  private readonly perKey: number;
  private readonly global: number;
  private readonly windowMs: number;

  constructor(perKey: number, global: number, windowMs: number) {
    this.perKey = perKey;
    this.global = global;
    this.windowMs = windowMs;
  }

  /** Records one attempt; true if this attempt is over a limit. */
  isLimited(key: string, now: number = Date.now()): boolean {
    if (this.windows.size > MAX_TRACKED_KEYS) this.prune(now);
    const overKey = this.hit(`ip:${key}`, now) > this.perKey;
    const overGlobal = this.hit("global", now) > this.global;
    return overKey || overGlobal;
  }

  private hit(key: string, now: number): number {
    const current = this.windows.get(key);
    const isNewWindow = !current || now - current.since > this.windowMs;
    const next = isNewWindow ? { count: 1, since: now } : { count: current.count + 1, since: current.since };
    this.windows.set(key, next);
    return next.count;
  }

  private prune(now: number): void {
    for (const [key, w] of this.windows) if (now - w.since > this.windowMs) this.windows.delete(key);
  }
}
