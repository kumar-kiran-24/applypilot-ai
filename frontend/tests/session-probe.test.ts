import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/server", () => ({ backendFetch: vi.fn() }));

import { backendFetch } from "@/lib/api/server";
import { probeSession } from "@/lib/auth/session";

const fetchMock = vi.mocked(backendFetch);

const USER = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "candidate@example.com",
} as never;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("probeSession", () => {
  it("reports a rejected token as unauthenticated", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Invalid token" }, 401));

    await expect(probeSession("token")).resolves.toEqual({
      status: "unauthenticated",
    });
  });

  it("reports an accepted token as authenticated", async () => {
    fetchMock.mockResolvedValue(json(USER));

    await expect(probeSession("token")).resolves.toEqual({
      status: "authenticated",
      user: USER,
    });
  });

  it("reports a backend outage as unavailable, never as an expired sign-in", async () => {
    // Regression: this used to be indistinguishable from a 401, so a restart
    // redirected a signed-in user to /login?expired=1 and discarded the page.
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));

    await expect(probeSession("token")).resolves.toEqual({
      status: "unavailable",
    });
  });

  it("reports a failing backend as unavailable", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 502 }));

    await expect(probeSession("token")).resolves.toEqual({
      status: "unavailable",
    });
  });
});
