import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  confirmManualSubmission,
  submitApplication,
} from "@/features/applications/api";

const APPLICATION_ID = "11111111-1111-4111-8111-111111111111";
const fetchMock = vi.fn<typeof fetch>();

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("manual submission safeguards", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => json({}));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the manual handoff endpoint and never a submit endpoint", async () => {
    await submitApplication(APPLICATION_ID);

    const [input, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(input)).toContain(
      `/api/backend/applications/${APPLICATION_ID}/submit`,
    );
    expect(init?.method).toBe("POST");
    // The UI sends no approval proof of its own; the backend re-checks it.
    expect(init?.body).toBeUndefined();
    expect(
      fetchMock.mock.calls.some(([call]) =>
        String(call).includes("/mark-submitted"),
      ),
    ).toBe(false);
  });

  it("records a manual submission only with an explicit confirmation flag", async () => {
    await confirmManualSubmission(APPLICATION_ID, "REF-123");

    const [input, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(input)).toContain(
      `/api/backend/applications/${APPLICATION_ID}/mark-submitted`,
    );
    expect(JSON.parse(String(init?.body))).toEqual({
      reference: "REF-123",
      confirmed: true,
    });
  });
});
