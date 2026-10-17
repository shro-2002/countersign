// Run with `npm test` (node --test, no extra dependencies).
import assert from "node:assert/strict";
import { test } from "node:test";

import { clientIp, codeMatches, isCrossOrigin, issueSession, LoginLimiter, readSession } from "./session.ts";

const SECRET = "s".repeat(32);
const NOW = 1_792_000_000_000;
const HOUR = 3600 * 1000;

test("a fresh, untampered session is accepted", () => {
  const { sid, cookie } = issueSession(SECRET, NOW);
  assert.equal(readSession(cookie, SECRET, NOW + HOUR), sid);
});

test("sessions expire server-side after 8 hours, even if the browser keeps the cookie", () => {
  const { cookie } = issueSession(SECRET, NOW);
  assert.equal(readSession(cookie, SECRET, NOW + 8 * HOUR + 1), null);
});

test("tampered, re-dated, foreign-secret or malformed cookies are rejected", () => {
  const { cookie } = issueSession(SECRET, NOW);
  const [sid, issued, sig] = cookie.split(".");
  for (const bad of [`${sid}.${Number(issued) + HOUR}.${sig}`, `${"0".repeat(32)}.${issued}.${sig}`, `${sid}.${issued}`,
    `${cookie}.x`, "", undefined]) {
    assert.equal(readSession(bad, SECRET, NOW), null, String(bad));
  }
  assert.equal(readSession(cookie, "t".repeat(32), NOW), null);
});

test("access code comparison", () => {
  assert.ok(codeMatches("correct-code-123", "correct-code-123"));
  assert.ok(!codeMatches("correct-code-12", "correct-code-123"));
  assert.ok(!codeMatches(undefined, "correct-code-123"));
});

test("client address is the hop our proxy appended, not a spoofable first entry", () => {
  assert.equal(clientIp("6.6.6.6, 203.0.113.9"), "203.0.113.9");
  assert.equal(clientIp(null), "local");
});

test("cross-origin browser POSTs are detected", () => {
  assert.ok(isCrossOrigin("https://evil.example", "demo.onrender.com"));
  assert.ok(!isCrossOrigin("https://demo.onrender.com", "demo.onrender.com"));
  assert.ok(!isCrossOrigin(null, "demo.onrender.com"));
  assert.ok(isCrossOrigin("not a url", "demo.onrender.com"));
});

test("login limiter: per address, and a global cap that rotating addresses cannot dodge", () => {
  const limiter = new LoginLimiter(2, 3, 1000);
  assert.deepEqual([1, 2, 3].map(() => limiter.isLimited("a", NOW)), [false, false, true]);
  assert.equal(limiter.isLimited("b", NOW), true); // 4th attempt overall exceeds the global cap of 3
  assert.equal(limiter.isLimited("b", NOW + 1001), false); // windows reset
});
