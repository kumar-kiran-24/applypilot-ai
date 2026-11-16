// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationPipelineCard } from "@/components/dashboard/application-pipeline";
import type { ApplicationPipeline, ApplicationSummary } from "@/types/api";

const APP_ID = "11111111-1111-4111-8111-111111111111";

function makeApplication(
  overrides: Partial<ApplicationSummary> = {},
): ApplicationSummary {
  return {
    id: APP_ID,
    job_id: "22222222-2222-4222-8222-222222222222",
    job_title: "Senior Backend Engineer",
    company: "Acme",
    platform: "greenhouse",
    platform_name: "Greenhouse",
    application_url: "https://boards.greenhouse.io/acme/jobs/1",
    status: "submitted",
    stage: "interview",
    automation_mode: "assisted",
    requires_account: false,
    resume_filename: "backend-strong.pdf",
    questions_total: 4,
    questions_confirmed: 4,
    missing_required_count: 0,
    created_at: "2026-11-10T09:00:00Z",
    updated_at: "2026-11-11T09:00:00Z",
    approved_at: "2026-11-11T08:00:00Z",
    submitted_at: "2026-11-11T09:00:00Z",
    ...overrides,
  };
}

function pipeline(overrides: Partial<ApplicationPipeline> = {}): ApplicationPipeline {
  return {
    total: 7,
    in_progress: 2,
    submitted: 3,
    failed: 1,
    cancelled: 1,
    by_stage: {
      applied: 1,
      screening: 0,
      interview: 1,
      offer: 0,
      rejected: 1,
      withdrawn: 0,
    },
    recent: [makeApplication()],
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

function renderPipeline() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApplicationPipelineCard />
    </QueryClientProvider>,
  );
}

describe("ApplicationPipelineCard", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the backend counts, every stage and the recent applications", async () => {
    fetchMock.mockResolvedValue(json(pipeline()));

    renderPipeline();

    expect(await screen.findByText("7 total")).toBeTruthy();

    const inProgress = screen.getByText("In progress").closest("div");
    expect(inProgress?.textContent).toContain("2");
    // "Submitted" is both a tile label and a recent-application status badge.
    const submitted = screen.getAllByText("Submitted")[0]!.closest("div");
    expect(submitted?.textContent).toContain("3");

    // All six stored stages are displayed, including zero counts.
    expect(screen.getByText("Applied · 1")).toBeTruthy();
    expect(screen.getByText("Screening · 0")).toBeTruthy();
    expect(screen.getByText("Interview · 1")).toBeTruthy();
    expect(screen.getByText("Offer · 0")).toBeTruthy();
    expect(screen.getByText("Rejected · 1")).toBeTruthy();
    expect(screen.getByText("Withdrawn · 0")).toBeTruthy();

    const link = screen.getByRole("link", {
      name: /Senior Backend Engineer/i,
    });
    expect(link.getAttribute("href")).toBe(`/applications/${APP_ID}`);
    expect(screen.getByText(/Greenhouse/)).toBeTruthy();
  });

  it("shows an empty pipeline without fabricating counts", async () => {
    fetchMock.mockResolvedValue(
      json(pipeline({ total: 0, in_progress: 0, submitted: 0, failed: 0, cancelled: 0, recent: [] })),
    );

    renderPipeline();

    expect(await screen.findByText("No applications yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: /browse job workspace/i })).toBeTruthy();
    expect(screen.queryByText("0 total")).toBeNull();
  });

  it("reports a pipeline error and offers a retry", async () => {
    fetchMock.mockResolvedValue(
      json({ detail: "Pipeline is unavailable." }, 503),
    );

    renderPipeline();

    expect(await screen.findByText("Could not load the pipeline")).toBeTruthy();
    expect(screen.getByText("Pipeline is unavailable.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });
});
