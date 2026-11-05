import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  sessionCookie,
  sessionCookieSecure,
  sessionMaxAgeSeconds,
} from "@/lib/auth/cookies";

describe("session cookie settings", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  it("keeps the JWT unreadable to JavaScript and scoped to this site", () => {
    const cookie = sessionCookie("a.jwt.value", 3600);

    expect(cookie.name).toBe("ap_session");
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe("lax");
    expect(cookie.path).toBe("/");
    expect(cookie.maxAge).toBe(3600);
  });

  it("marks the cookie Secure in production builds", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("SESSION_COOKIE_SECURE", "");

    expect(sessionCookieSecure()).toBe(true);
    expect(sessionCookie("token").secure).toBe(true);
  });

  it("never lets SESSION_COOKIE_SECURE=false weaken a production build", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("SESSION_COOKIE_SECURE", "false");

    expect(sessionCookie("token").secure).toBe(true);
  });

  it("allows Secure to be enabled locally over HTTPS", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("SESSION_COOKIE_SECURE", "true");

    expect(sessionCookie("token").secure).toBe(true);
  });

  it("uses a bounded, positive max age", () => {
    vi.stubEnv("SESSION_MAX_AGE_SECONDS", "1800");
    expect(sessionMaxAgeSeconds()).toBe(1800);

    vi.stubEnv("SESSION_MAX_AGE_SECONDS", "not-a-number");
    expect(sessionMaxAgeSeconds()).toBe(3600);

    expect(sessionCookie("token", -5).maxAge).toBe(0);
  });
});
