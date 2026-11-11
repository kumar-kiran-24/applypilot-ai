// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobIntelligencePanel } from "@/components/intelligence/job-intelligence-panel";
import { jobIntelligence } from "./fixtures/intelligence";

const JOB_ID = "22222222-2222-4222-8222-222222222222";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <JobIntelligencePanel jobId={JOB_ID} />
    </QueryClientProvider>,
  );
}

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
}

describe("JobIntelligencePanel", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the extracted posting without inventing missing values", async () => {
    fetchMock.mockResolvedValue(
      json(
        jobIntelligence({
          fields: { company: null, salary_range: null },
        }),
      ),
    );

    renderPanel();

    expect(await screen.findByText("Required skills")).toBeTruthy();
    expect(screen.getByText("Python")).toBeTruthy();
    expect(screen.getByText("Docker")).toBeTruthy();
    expect(screen.getByText("Full time")).toBeTruthy();
    expect(screen.getByText("Remote")).toBeTruthy();
    expect(screen.getByText("Senior")).toBeTruthy();
    expect(screen.getByText("5+ years")).toBeTruthy();
    expect(
      screen.getByText("Bachelor's degree in Computer Science"),
    ).toBeTruthy();
    expect(screen.getByText("Design and operate Python services")).toBeTruthy();
    expect(screen.getByText("High confidence")).toBeTruthy();
    expect(screen.getByText("Not saved yet")).toBeTruthy();
    expect(screen.getByText(/only information stated in the posting/i)).toBeTruthy();

    // Company and salary were not stated: they are missing, not negative facts.
    expect(screen.getByText(/Not stated in the posting/i)).toBeTruthy();
    expect(screen.getByText(/^company, salary range\./i)).toBeTruthy();
    expect(screen.queryByText("Acme")).toBeNull();
  });

  it("reveals provenance and field confidence on request", async () => {
    fetchMock.mockResolvedValue(json(jobIntelligence()));
    renderPanel();

    await screen.findByText("Required skills");
    fireEvent.click(screen.getByRole("button", { name: /show provenance/i }));

    expect(screen.getByText("job record")).toBeTruthy();
    expect(screen.getByText("deterministic")).toBeTruthy();
  });

  it("analyzes and saves, then reflects the stored analysis", async () => {
    let stored = false;
    fetchMock.mockImplementation(async (input, init) => {
      const path = pathOf(input);
      if (path === `jobs/${JOB_ID}/analyze`) {
        stored = true;
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          force: false,
          use_llm: true,
        });
        return json(jobIntelligence({ extraction: { cached: true } }));
      }
      return json(jobIntelligence({ extraction: { cached: stored } }));
    });

    renderPanel();

    // The action stays disabled until the reading is available.
    await screen.findByText("Required skills");
    const analyze = screen.getByRole("button", { name: /analyze and save/i });
    fireEvent.click(analyze);

    expect(await screen.findByText("Saved analysis")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /refresh analysis/i }),
    ).toBeTruthy();
  });

  it("states when the posting lists no skills", async () => {
    fetchMock.mockResolvedValue(
      json(jobIntelligence({ fields: { required_skills: [], preferred_skills: [] } })),
    );
    renderPanel();

    expect(
      await screen.findByText(/does not list required or preferred skills/i),
    ).toBeTruthy();
  });

  it("surfaces a safe error instead of showing unextracted content", async () => {
    fetchMock.mockResolvedValue(
      json({ detail: "Job analysis is unavailable.", code: "unavailable" }, 503),
    );
    renderPanel();

    expect(
      await screen.findByText(/could not load job intelligence/i),
    ).toBeTruthy();
    expect(screen.getByText(/job analysis is unavailable/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
    expect(screen.queryByText("Required skills")).toBeNull();
  });

  it("shows extraction warnings and contradictions from the backend", async () => {
    fetchMock.mockResolvedValue(
      json(
        jobIntelligence({
          extraction: {
            warnings: ["Salary could not be read."],
            contradictions: ["The posting says both remote and on-site."],
          },
        }),
      ),
    );
    renderPanel();

    expect(await screen.findByText(/salary could not be read/i)).toBeTruthy();
    expect(
      screen.getByText(/both remote and on-site/i),
    ).toBeTruthy();
  });

  it("shows a loading skeleton while the reading is pending", async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockImplementation(
      () => new Promise<Response>((done) => (resolve = done)),
    );
    const { container } = renderPanel();

    await waitFor(() =>
      expect(container.querySelector(".animate-pulse")).not.toBeNull(),
    );
    expect(screen.queryByText("Required skills")).toBeNull();

    resolve(json(jobIntelligence()));
    expect(await screen.findByText("Required skills")).toBeTruthy();
  });
});
