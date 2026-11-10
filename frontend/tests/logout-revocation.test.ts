import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/server", () => ({ backendFetch: vi.fn() }));
vi.mock("@/lib/auth/session", () => ({ getSessionToken: vi.fn() }));

import { POST } from "@/app/api/auth/logout/route";
import { backendFetch } from "@/lib/api/server";
import { getSessionToken } from "@/lib/auth/session";

const mockedBackendFetch = vi.mocked(backendFetch);
const mockedGetSessionToken = vi.mocked(getSessionToken);

function logoutRequest({
  origin = "http://127.0.0.1:3000",
  host = "127.0.0.1:3000",
} = {}): Request {
  return new Request("http://127.0.0.1:3000/api/auth/logout", {
    method: "POST",
    headers: { Origin: origin, Host: host },
  });
}

describe("POST /api/auth/logout", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    mockedBackendFetch.mockReset();
    mockedGetSessionToken.mockReset();
  });

  it("revokes the token server-side before clearing the cookie", async () => {
    mockedGetSessionToken.mockResolvedValue("header.payload.signature");
    mockedBackendFetch.mockResolvedValue(
      new Response(JSON.stringify({ message: "Signed out.", revoked: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await POST(logoutRequest());

    expect(response.status).toBe(200);
    expect(mockedBackendFetch).toHaveBeenCalledWith("auth/logout", {
      method: "POST",
      token: "header.payload.signature",
    });
    const setCookie = response.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain("ap_session=");
    expect(setCookie).toContain("Max-Age=0");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("still clears the cookie when the backend revocation call fails", async () => {
    mockedGetSessionToken.mockResolvedValue("header.payload.signature");
    mockedBackendFetch.mockRejectedValue(new TypeError("fetch failed"));

    const response = await POST(logoutRequest());

    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
  });

  it("clears the cookie even when no token is present", async () => {
    mockedGetSessionToken.mockResolvedValue(null);

    const response = await POST(logoutRequest());

    expect(mockedBackendFetch).not.toHaveBeenCalled();
    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
  });

  it("rejects a cross-origin logout without calling the backend", async () => {
    const response = await POST(
      logoutRequest({ origin: "https://evil.example" }),
    );

    expect(response.status).toBe(403);
    expect(mockedBackendFetch).not.toHaveBeenCalled();
  });
});
