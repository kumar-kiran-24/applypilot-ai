import { describe, expect, it, vi, beforeEach } from "vitest";

const { getSessionTokenMock } = vi.hoisted(() => ({
  getSessionTokenMock: vi.fn(),
}));

vi.mock("@/lib/auth/session", () => ({
  getSessionToken: getSessionTokenMock,
}));

import { GET, POST } from "@/app/api/backend/[...path]/route";
import { matchProxyRoute } from "@/lib/constants";

const RESUME_ID = "11111111-1111-1111-1111-111111111111";
const JOB_ID = "22222222-2222-2222-2222-222222222222";

function context(...path: string[]) {
  return { params: Promise.resolve({ path }) };
}

describe("BFF proxy allow-list", () => {
  it("allows the new resume intelligence and comparison routes", () => {
    expect(matchProxyRoute("POST", `resumes/${RESUME_ID}/analyze`)).not.toBeNull();
    expect(
      matchProxyRoute("GET", `resumes/${RESUME_ID}/intelligence`),
    ).not.toBeNull();
    expect(
      matchProxyRoute("POST", `jobs/${JOB_ID}/compare-resumes`),
    ).not.toBeNull();
    expect(
      matchProxyRoute("GET", `jobs/${JOB_ID}/recommendation`),
    ).not.toBeNull();
  });

  it("still rejects unlisted backend routes and wrong methods", () => {
    expect(matchProxyRoute("GET", "resumes")).not.toBeNull();
    expect(matchProxyRoute("POST", "resumes")).toBeNull();
    expect(matchProxyRoute("GET", `resumes/${RESUME_ID}/file`)).toBeNull();
    expect(matchProxyRoute("POST", `jobs/${JOB_ID}/intelligence`)).toBeNull();
    expect(matchProxyRoute("GET", "jobs/not-a-uuid")).toBeNull();
    expect(matchProxyRoute("GET", `resumes/${RESUME_ID}/analyze`)).toBeNull();
  });
});

describe("BFF authentication", () => {
  beforeEach(() => {
    getSessionTokenMock.mockReset();
  });

  it("requires the HttpOnly session before proxying resume intelligence", async () => {
    getSessionTokenMock.mockResolvedValue(null);

    const response = await GET(
      new Request(
        `http://localhost/api/backend/resumes/${RESUME_ID}/intelligence`,
      ),
      context("resumes", RESUME_ID, "intelligence"),
    );

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toMatchObject({
      detail: "Authentication required.",
    });
  });

  it("returns 404 for an unlisted path before any authentication work", async () => {
    const response = await POST(
      new Request("http://localhost/api/backend/resumes", { method: "POST" }),
      context("resumes"),
    );

    expect(response.status).toBe(404);
    expect(getSessionTokenMock).not.toHaveBeenCalled();
  });
});
