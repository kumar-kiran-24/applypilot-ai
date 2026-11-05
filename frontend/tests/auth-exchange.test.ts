import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/server", () => ({ backendFetch: vi.fn() }));

import { POST } from "@/app/api/auth/exchange/route";
import { backendFetch } from "@/lib/api/server";

const mockedBackendFetch = vi.mocked(backendFetch);

const CODE = "a".repeat(43);
const ACCESS_TOKEN = "header.payload.signature";

function exchangeRequest(
  body: unknown,
  { origin = "http://127.0.0.1:3000", host = "127.0.0.1:3000" } = {},
): Request {
  return new Request("http://127.0.0.1:3000/api/auth/exchange", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: origin,
      Host: host,
    },
    body: JSON.stringify(body),
  });
}

function backendJson(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("POST /api/auth/exchange", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    mockedBackendFetch.mockReset();
  });

  it("sets an HttpOnly session cookie and never returns the JWT", async () => {
    mockedBackendFetch.mockResolvedValue(
      backendJson({
        access_token: ACCESS_TOKEN,
        token_type: "bearer",
        expires_in: 3600,
        user: { id: "u1", email: "ada@example.com" },
      }),
    );

    const response = await POST(exchangeRequest({ code: CODE }));
    const body = await response.json();
    const setCookie = response.headers.get("set-cookie") ?? "";

    expect(response.status).toBe(200);
    expect(body).toEqual({ authenticated: true });
    expect(JSON.stringify(body)).not.toContain(ACCESS_TOKEN);
    expect(setCookie).toContain("ap_session=");
    expect(setCookie).toContain("HttpOnly");
    expect(setCookie).toContain("SameSite=lax");
    expect(setCookie).toContain("Path=/");
    expect(response.headers.get("cache-control")).toBe("no-store");

    const [, options] = mockedBackendFetch.mock.calls[0] ?? [];
    expect(mockedBackendFetch).toHaveBeenCalledWith(
      "auth/login/exchange",
      expect.objectContaining({ method: "POST", body: { code: CODE } }),
    );
    // The exchange is unauthenticated: no bearer token is forwarded.
    expect(options).not.toHaveProperty("token");
  });

  it("marks the cookie Secure in production builds", async () => {
    vi.stubEnv("NODE_ENV", "production");
    mockedBackendFetch.mockResolvedValue(
      backendJson({ access_token: ACCESS_TOKEN, expires_in: 3600 }),
    );

    const response = await POST(exchangeRequest({ code: CODE }));

    expect(response.headers.get("set-cookie")).toContain("Secure");
  });

  it("never lets the cookie outlive the JWT", async () => {
    vi.stubEnv("SESSION_MAX_AGE_SECONDS", "3600");
    mockedBackendFetch.mockResolvedValue(
      backendJson({ access_token: ACCESS_TOKEN, expires_in: 120 }),
    );

    const response = await POST(exchangeRequest({ code: CODE }));

    expect(response.headers.get("set-cookie")).toContain("Max-Age=120");
  });

  it("rejects a cross-origin exchange without calling the backend", async () => {
    const response = await POST(
      exchangeRequest({ code: CODE }, { origin: "https://evil.example" }),
    );

    expect(response.status).toBe(403);
    expect(mockedBackendFetch).not.toHaveBeenCalled();
  });

  it("rejects a malformed code without calling the backend", async () => {
    const response = await POST(exchangeRequest({ code: "short" }));

    expect(response.status).toBe(400);
    expect(mockedBackendFetch).not.toHaveBeenCalled();
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("passes a backend rejection through without setting a cookie", async () => {
    mockedBackendFetch.mockResolvedValue(
      backendJson(
        { detail: "This sign-in link was already used. Start again from the login page." },
        400,
      ),
    );

    const response = await POST(exchangeRequest({ code: CODE }));
    const body = await response.json();

    expect(response.status).toBe(400);
    expect(body.detail).toContain("already used");
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("fails closed when the backend returns no usable token", async () => {
    mockedBackendFetch.mockResolvedValue(backendJson({ token_type: "bearer" }));

    const response = await POST(exchangeRequest({ code: CODE }));

    expect(response.status).toBe(502);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("reports an unreachable backend without leaking the code", async () => {
    mockedBackendFetch.mockRejectedValue(new TypeError("fetch failed"));

    const response = await POST(exchangeRequest({ code: CODE }));
    const body = await response.json();

    expect(response.status).toBe(504);
    expect(body.detail).not.toContain(CODE);
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});
