/**
 * Integration coverage for the real Next.js BFF route handler
 * (`src/app/api/backend/[...path]/route.ts`).
 *
 * Phase 2J.1 shipped client calls whose paths were absent from the proxy
 * allow-list. Every existing test passed because Vitest mocks the API layer and
 * Playwright fulfils `**\/api/backend/**` at the browser boundary — the route
 * handler never ran. These tests call the handler directly, so a missing
 * allow-list entry fails here instead of in a user's browser.
 *
 * Only the outbound `backendFetch` and the cookie reader are mocked; the
 * allow-list, origin check, auth branch, body handling and error translation
 * are the real implementations.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/server", () => ({ backendFetch: vi.fn() }));
vi.mock("@/lib/auth/session", () => ({ getSessionToken: vi.fn() }));

import { backendFetch } from "@/lib/api/server";
import type { BackendFetchOptions } from "@/lib/api/server";
import { getSessionToken } from "@/lib/auth/session";
import { GET, POST } from "@/app/api/backend/[...path]/route";

const fetchMock = vi.mocked(backendFetch);
const tokenMock = vi.mocked(getSessionToken);

const APPLICATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ORIGIN = "http://applypilot.local";

type RouteHandler = (
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) => Promise<Response>;

function context(...segments: string[]): { params: Promise<{ path: string[] }> } {
  return { params: Promise.resolve({ path: segments }) };
}

function request(path: string, init: RequestInit = {}): Request {
  return new Request(`${ORIGIN}/api/backend/${path}`, init);
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function call(
  handler: RouteHandler,
  path: string,
  segments: string[],
  init: RequestInit = {},
): Promise<Response> {
  return handler(request(path, init), context(...segments));
}

function postJson(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** The single backend call, or a failure that names the missing call. */
function onlyCall(): [string, BackendFetchOptions | undefined] {
  const call = fetchMock.mock.calls[0];
  if (!call) {
    throw new Error("backendFetch was not called");
  }
  return call;
}

beforeEach(() => {
  fetchMock.mockReset();
  tokenMock.mockReset();
  tokenMock.mockResolvedValue("session-token");
});

describe("BFF route handler — repaired routes", () => {
  it("forwards POST jobs/import with the token and an unchanged body", async () => {
    const payload = {
      url: "https://www.glassdoor.com/job-listing/senior-ai-engineer-JV_IC1.htm?jl=100",
    };
    const upstreamBody = {
      job: { id: "22222222-2222-4222-8222-222222222222" },
      created: true,
      reused: false,
    };
    fetchMock.mockResolvedValue(json(upstreamBody));

    const response = await call(POST, "jobs/import", ["jobs", "import"], postJson(payload));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(upstreamBody);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = onlyCall();
    expect(path).toBe("jobs/import");
    expect(options?.method).toBe("POST");
    expect(options?.body).toEqual(payload);
    expect(options?.token).toBe("session-token");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it("forwards POST .../destination for an application", async () => {
    fetchMock.mockResolvedValue(json({ status: "validated", manual_destination: true }));

    const response = await call(
      POST,
      `browser-sessions/by-application/${APPLICATION_ID}/destination`,
      ["browser-sessions", "by-application", APPLICATION_ID, "destination"],
      postJson({ destination_url: "https://boards.greenhouse.io/acme/jobs/123" }),
    );

    expect(response.status).toBe(200);
    const [path, options] = onlyCall();
    expect(path).toBe(`browser-sessions/by-application/${APPLICATION_ID}/destination`);
    expect(options?.method).toBe("POST");
    expect(options?.body).toEqual({
      destination_url: "https://boards.greenhouse.io/acme/jobs/123",
    });
  });

  it("forwards POST .../recover, including the force flag", async () => {
    fetchMock.mockResolvedValue(json({ recovered: true, blocked_by: [] }));

    const response = await call(
      POST,
      `browser-sessions/by-application/${APPLICATION_ID}/recover`,
      ["browser-sessions", "by-application", APPLICATION_ID, "recover"],
      postJson({ force: true }),
    );

    expect(response.status).toBe(200);
    const [path, options] = onlyCall();
    expect(path).toBe(`browser-sessions/by-application/${APPLICATION_ID}/recover`);
    expect(options?.body).toEqual({ force: true });
  });

  it("forwards GET browser-sessions/active", async () => {
    fetchMock.mockResolvedValue(json({ total: 0, sessions: [] }));

    const response = await call(GET, "browser-sessions/active", [
      "browser-sessions",
      "active",
    ]);

    expect(response.status).toBe(200);
    const [path, options] = onlyCall();
    expect(path).toBe("browser-sessions/active");
    expect(options?.method).toBe("GET");
  });

  it("forwards GET .../form-analysis/fields", async () => {
    fetchMock.mockResolvedValue(json({ analysis_id: null, fields: [] }));

    const response = await call(
      GET,
      `browser-sessions/by-application/${APPLICATION_ID}/form-analysis/fields`,
      ["browser-sessions", "by-application", APPLICATION_ID, "form-analysis", "fields"],
    );

    expect(response.status).toBe(200);
    expect(onlyCall()[0]).toBe(
      `browser-sessions/by-application/${APPLICATION_ID}/form-analysis/fields`,
    );
  });

  it("forwards only the allow-listed query keys", async () => {
    fetchMock.mockResolvedValue(json({ total: 0, jobs: [] }));

    await call(GET, "jobs?q=python&limit=5&evil=1", ["jobs"]);

    const [, options] = onlyCall();
    expect(options?.query?.get("q")).toBe("python");
    expect(options?.query?.get("limit")).toBe("5");
    expect(options?.query?.has("evil")).toBe(false);
  });
});

describe("BFF route handler — authentication", () => {
  it("returns 401 without calling the backend when there is no session", async () => {
    tokenMock.mockResolvedValue(null);

    const response = await call(POST, "jobs/import", ["jobs", "import"], postJson({ url: "x" }));

    expect(response.status).toBe(401);
    expect((await response.json()).detail).toBe("Authentication required.");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("drops the stale cookie when the backend rejects the token", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Invalid or expired access token." }, 401));

    const response = await call(GET, "jobs", ["jobs"]);

    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")).toContain("ap_session=");
  });
});

describe("BFF route handler — allow-list enforcement", () => {
  it("rejects an unapproved path with a distinguishable error", async () => {
    const response = await call(GET, "metrics", ["metrics"]);

    expect(response.status).toBe(404);
    const body = await response.json();
    expect(body.code).toBe("proxy_route_not_allowed");
    expect(body.detail).toMatch(/not available through the ApplyPilot API proxy/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a valid path with the wrong method", async () => {
    const response = await call(GET, "jobs/import", ["jobs", "import"]);

    expect(response.status).toBe(404);
    expect((await response.json()).code).toBe("proxy_route_not_allowed");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a near-miss path that a broad pattern would have allowed", async () => {
    const response = await call(POST, "jobs/import/extra", ["jobs", "import", "extra"], postJson({}));

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a cross-origin mutation before forwarding it", async () => {
    const response = await call(POST, "jobs/import", ["jobs", "import"], {
      ...postJson({ url: "x" }),
      headers: { "Content-Type": "application/json", Origin: "https://evil.example" },
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("BFF route handler — backend responses", () => {
  it("translates a backend error envelope without leaking anything else", async () => {
    fetchMock.mockResolvedValue(
      json(
        {
          detail: "That link is not a Glassdoor job posting.",
          code: "unsupported_job_url_shape",
        },
        400,
      ),
    );

    const response = await call(
      POST,
      "jobs/import",
      ["jobs", "import"],
      postJson({ url: "https://www.glassdoor.com/overview" }),
    );

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({
      detail: "That link is not a Glassdoor job posting.",
      code: "unsupported_job_url_shape",
      details: undefined,
    });
  });

  it("reports an unreachable backend as a gateway error, not a 404", async () => {
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));

    const response = await call(
      POST,
      "jobs/import",
      ["jobs", "import"],
      postJson({ url: "https://www.glassdoor.com/job-listing/x-JV_1.htm" }),
    );

    expect(response.status).toBe(504);
    expect((await response.json()).code).toBe("backend_unreachable");
  });

  it("reports a slow live-browser action as a timeout, not an unreachable API", async () => {
    // Regression: a form analysis / fill on a real page exceeds 30s, and the
    // abort was reported as "the API could not be reached" against a backend
    // that was working. The two failures must stay distinguishable.
    const abort = new Error("This operation was aborted");
    abort.name = "AbortError";
    fetchMock.mockRejectedValue(abort);

    const response = await call(
      POST,
      `browser-sessions/by-application/${APPLICATION_ID}/fill`,
      ["browser-sessions", "by-application", APPLICATION_ID, "fill"],
      postJson({ approval_fingerprint: "a".repeat(64) }),
    );

    expect(response.status).toBe(504);
    const body = await response.json();
    expect(body.code).toBe("backend_timeout");
    expect(body.detail).toContain("did not respond in time");
    // Live-page routes must be given room to finish, not the 30s default.
    const [, options] = onlyCall();
    expect(options?.timeoutMs).toBe(150_000);
  });

  it("forwards a 409 from a repaired route unchanged", async () => {
    fetchMock.mockResolvedValue(
      json(
        {
          detail: "A live browser session is already open for this application.",
          code: "browser_session_already_open",
        },
        409,
      ),
    );

    const response = await call(
      POST,
      `browser-sessions/by-application/${APPLICATION_ID}/recover`,
      ["browser-sessions", "by-application", APPLICATION_ID, "recover"],
      postJson({ force: false }),
    );

    expect(response.status).toBe(409);
    expect((await response.json()).code).toBe("browser_session_already_open");
  });
});
