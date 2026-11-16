// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobWorkspace } from "@/components/jobs/job-workspace";
import type { ApplicationSummary, Job } from "@/types/api";

import { jobIntelligence, resumeComparison } from "./fixtures/intelligence";

const JOB_ID = "22222222-2222-4222-8222-222222222222";
const APP_ID = "11111111-1111-4111-8111-111111111111";

const pushMock = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    location: "Remote (US)",
    job_url: "https://www.glassdoor.com/job-listing/senior-backend-acme",
    job_description: "Build Python services with FastAPI and PostgreSQL.",
    email_subject: "Glassdoor: Senior Backend Engineer at Acme",
    received_at: "2026-11-09T08:00:00Z",
    source: "glassdoor",
    discovered_at: "2026-11-09T08:05:00Z",
    requirements: {
      required_skills: ["Python", "FastAPI", "PostgreSQL"],
      preferred_skills: ["Docker"],
      mentioned_skills: ["Python", "FastAPI", "PostgreSQL", "Docker"],
      experience_years: 5,
      education_level: "bachelor",
      education_field: "Computer Science",
      responsibilities: ["Design and operate Python services"],
      has_description: true,
      skills_source: "deterministic",
      confidence: "high",
    },
    best_match: {
      resume_id: "33333333-3333-4333-8333-333333333333",
      resume_filename: "backend-strong.pdf",
      overall_score: 82.5,
      skill_score: 90,
      rank: 1,
      explanation: "Strong overlap with the posting requirements.",
    },
    matches_count: 2,
    user_state: {
      saved: false,
      dismissed: false,
      saved_at: null,
      dismissed_at: null,
    },
    ...overrides,
  };
}

function makeApplication(
  overrides: Partial<ApplicationSummary> = {},
): ApplicationSummary {
  return {
    id: APP_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    platform: "greenhouse",
    platform_name: "Greenhouse",
    application_url: "https://boards.greenhouse.io/acme/jobs/1",
    status: "awaiting_approval",
    stage: "interview",
    automation_mode: "assisted",
    requires_account: false,
    resume_filename: "backend-strong.pdf",
    questions_total: 4,
    questions_confirmed: 4,
    missing_required_count: 0,
    created_at: "2026-11-10T09:00:00Z",
    updated_at: "2026-11-11T09:00:00Z",
    approved_at: null,
    submitted_at: null,
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
let job: Job;
let applications: ApplicationSummary[];

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
}

function installFetch() {
  fetchMock.mockImplementation(async (input, init) => {
    const path = pathOf(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (path === `jobs/${JOB_ID}`) {
      return json(job);
    }
    if (path === "applications") {
      return json({ total: applications.length, applications });
    }
    if (path === `jobs/${JOB_ID}/intelligence`) {
      return json(jobIntelligence());
    }
    if (path === `jobs/${JOB_ID}/recommendation`) {
      return json(resumeComparison());
    }
    if (path === "applications/analyze" && method === "POST") {
      return json({ application_id: APP_ID });
    }
    const action = path.match(/^jobs\/([^/]+)\/(save|dismiss|restore)$/);
    if (action) {
      const [, , kind] = action;
      const next: Job = {
        ...job,
        user_state: {
          ...job.user_state,
          ...(kind === "save"
            ? { saved: method === "POST", dismissed: false }
            : kind === "dismiss"
              ? { saved: false, dismissed: true }
              : { dismissed: false }),
        },
      };
      job = next;
      return json(next);
    }
    return json({ detail: "Not mocked." }, 404);
  });
}

function renderWorkspace() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <JobWorkspace jobId={JOB_ID} />
    </QueryClientProvider>,
  );
}

describe("JobWorkspace", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    pushMock.mockReset();
    job = makeJob();
    applications = [makeApplication()];
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the posting, requirements, intelligence and recommendation", async () => {
    renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "Senior Backend Engineer" }),
    ).toBeTruthy();
    expect(
      screen.getByText("Build Python services with FastAPI and PostgreSQL."),
    ).toBeTruthy();
    expect(screen.getByText("Requirements at a glance")).toBeTruthy();
    expect(screen.getByText("5+ years")).toBeTruthy();
    expect(screen.getByText("bachelor, Computer Science")).toBeTruthy();
    expect(screen.getAllByText("Python").length).toBeGreaterThan(0);

    // The extracted intelligence and resume comparison are the existing panels.
    expect(await screen.findByText("Job intelligence")).toBeTruthy();
    expect(await screen.findByText("Recommended resume")).toBeTruthy();
    expect(screen.getAllByText("backend-strong.pdf").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("PostgreSQL is not evidenced").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Alternatives")).toBeTruthy();
  });

  it("shows the current application status and links to its workflow", async () => {
    renderWorkspace();

    expect(await screen.findByText("Awaiting approval")).toBeTruthy();
    expect(screen.getByText("Interview")).toBeTruthy();

    const link = screen.getByRole("link", { name: /open application/i });
    expect(link.getAttribute("href")).toBe(`/applications/${APP_ID}`);
  });

  it("starts a new application through the analyze endpoint", async () => {
    applications = [];
    renderWorkspace();

    fireEvent.click(
      await screen.findByRole("button", { name: /start application/i }),
    );

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) === "applications/analyze" &&
          (init?.method ?? "GET") === "POST",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ job_id: JOB_ID });
    });
    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith(`/applications/${APP_ID}`),
    );
  });

  it("saves, dismisses and restores the recommendation", async () => {
    renderWorkspace();
    await screen.findByRole("heading", { name: "Senior Backend Engineer" });

    fireEvent.click(screen.getByRole("button", { name: /save job/i }));

    expect(
      await screen.findByRole("button", { name: /^saved$/i }),
    ).toBeTruthy();
    expect(job.user_state.saved).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(
      await screen.findByRole("button", { name: /restore/i }),
    ).toBeTruthy();
    expect(job.user_state.dismissed).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /restore/i }));
    await waitFor(() => expect(job.user_state.dismissed).toBe(false));
  });

  it("surfaces a missing job without inventing details", async () => {
    fetchMock.mockImplementation(async (input) => {
      const path = pathOf(input);
      if (path === `jobs/${JOB_ID}`) {
        return json({ detail: "Job not found.", code: "job_not_found" }, 404);
      }
      if (path === "applications") {
        return json({ total: 0, applications: [] });
      }
      return json({ detail: "Not mocked." }, 404);
    });

    renderWorkspace();

    expect(await screen.findByText("Could not open this job")).toBeTruthy();
    expect(screen.getByText("Job not found.")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: /back to the job library/i }),
    ).toBeTruthy();
    expect(screen.queryByText("Job description")).toBeNull();
  });
});
