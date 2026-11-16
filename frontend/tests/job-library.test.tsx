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

import { JobLibrary } from "@/components/jobs/job-library";
import type { Job } from "@/types/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const JOB_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_ID = "33333333-3333-4333-8333-333333333333";

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
      required_skills: ["Python", "FastAPI"],
      preferred_skills: ["Docker"],
      mentioned_skills: ["Python", "FastAPI", "Docker"],
      experience_years: 5,
      education_level: "bachelor",
      education_field: "Computer Science",
      responsibilities: ["Design and operate Python services"],
      has_description: true,
      skills_source: "deterministic",
      confidence: "high",
    },
    best_match: null,
    matches_count: 0,
    user_state: {
      saved: false,
      dismissed: false,
      saved_at: null,
      dismissed_at: null,
    },
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
let stored: Job[] = [];
let listTotalOverride: number | null = null;
let failList = false;

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
}

function paramsOf(input: RequestInfo | URL): URLSearchParams {
  const [, query] = pathOf(input).split("?");
  return new URLSearchParams(query ?? "");
}

function matchesFilters(job: Job, params: URLSearchParams): boolean {
  const state = params.get("state") ?? "active";
  if (state === "saved" && !job.user_state.saved) return false;
  if (state === "dismissed" && !job.user_state.dismissed) return false;
  if (state === "active" && job.user_state.dismissed) return false;

  const q = params.get("q")?.toLowerCase();
  if (q) {
    const haystack = [
      job.job_title,
      job.company ?? "",
      job.location ?? "",
      job.job_description ?? "",
    ]
      .join(" ")
      .toLowerCase();
    if (!haystack.includes(q)) return false;
  }
  const company = params.get("company")?.toLowerCase();
  if (company && !(job.company ?? "").toLowerCase().includes(company)) {
    return false;
  }
  return true;
}

function installFetch() {
  fetchMock.mockImplementation(async (input, init) => {
    const path = pathOf(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (path.startsWith("jobs?")) {
      if (failList) {
        return json({ detail: "Jobs are unavailable." }, 503);
      }
      const params = paramsOf(input);
      const visible = stored.filter((job) => matchesFilters(job, params));
      const offset = Number(params.get("offset") ?? 0);
      const limit = Number(params.get("limit") ?? 10);
      return json({
        total: listTotalOverride ?? visible.length,
        jobs: visible.slice(offset, offset + limit),
      });
    }

    const stateAction = path.match(/^jobs\/([^/]+)\/(save|dismiss|restore)$/);
    if (stateAction) {
      const [, jobId, action] = stateAction;
      const index = stored.findIndex((job) => job.id === jobId);
      if (index < 0) return json({ detail: "Job not found." }, 404);
      const job = stored[index]!;
      const next: Job = {
        ...job,
        user_state: {
          ...job.user_state,
          ...(action === "save"
            ? { saved: method === "POST", dismissed: false }
            : action === "dismiss"
              ? { saved: false, dismissed: true }
              : { dismissed: false }),
        },
      };
      if (method === "DELETE") {
        next.user_state.saved = false;
      }
      stored[index] = next;
      return json(next);
    }

    return json({ detail: "Not mocked." }, 404);
  });
}

function renderLibrary() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <JobLibrary />
    </QueryClientProvider>,
  );
}

describe("JobLibrary", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    stored = [makeJob()];
    listTotalOverride = null;
    failList = false;
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("lists stored jobs with the default active query", async () => {
    renderLibrary();

    expect(await screen.findByText("Senior Backend Engineer")).toBeTruthy();
    // The count is split across spans around the total, so read its text.
    expect(screen.getByText(/Showing/).textContent).toContain("1–1 of 1");

    const listed = fetchMock.mock.calls
      .map(([input]) => pathOf(input))
      .find((path) => path.startsWith("jobs?"));
    expect(listed).toBe(
      "jobs?limit=10&offset=0&state=active&sort=received_desc",
    );
  });

  it("sends search and company filters to the backend and resets paging", async () => {
    renderLibrary();
    await screen.findByText("Senior Backend Engineer");

    fireEvent.change(screen.getByLabelText("Search jobs"), {
      target: { value: "backend" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input]) => paramsOf(input).get("q") === "backend",
        ),
      ).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    fireEvent.change(screen.getByLabelText("Company"), {
      target: { value: "Acme" },
    });

    await waitFor(() => {
      const last = fetchMock.mock.calls
        .map(([input]) => input)
        .filter((input) => pathOf(input).startsWith("jobs?"))
        .at(-1);
      expect(paramsOf(last!).get("company")).toBe("Acme");
      expect(paramsOf(last!).get("q")).toBe("backend");
      expect(paramsOf(last!).get("offset")).toBe("0");
    });
  });

  it("filters by saved and dismissed state through the state query", async () => {
    stored = [
      makeJob(),
      makeJob({
        id: OTHER_ID,
        job_title: "Saved Engineer",
        user_state: {
          saved: true,
          dismissed: false,
          saved_at: "2026-11-10T09:00:00Z",
          dismissed_at: null,
        },
      }),
    ];
    renderLibrary();

    await screen.findByText("Senior Backend Engineer");
    fireEvent.click(screen.getByRole("tab", { name: "Saved" }));

    expect(await screen.findByText("Saved Engineer")).toBeTruthy();
    expect(screen.queryByText("Senior Backend Engineer")).toBeNull();

    const last = fetchMock.mock.calls
      .map(([input]) => input)
      .filter((input) => pathOf(input).startsWith("jobs?"))
      .at(-1);
    expect(paramsOf(last!).get("state")).toBe("saved");
  });

  it("saves and dismisses a recommendation through the state endpoints", async () => {
    renderLibrary();
    await screen.findByText("Senior Backend Engineer");

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) === `jobs/${JOB_ID}/save` &&
          (init?.method ?? "GET") === "POST",
      );
      expect(call).toBeTruthy();
    });
    // The returned state is written into the detail cache and the list refetched.
    expect(await screen.findByRole("button", { name: /saved/i })).toBeTruthy();
    // The count is split across spans around the total, so read its text.
    expect(screen.getByText(/Showing/).textContent).toContain("1–1 of 1");

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) === `jobs/${JOB_ID}/dismiss` &&
          (init?.method ?? "GET") === "POST",
      );
      expect(call).toBeTruthy();
    });
  });

  it("pages with limit and offset from the backend total", async () => {
    listTotalOverride = 25;
    stored = [makeJob()];
    renderLibrary();

    await screen.findByText("Senior Backend Engineer");
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();

    const previous = screen.getByRole("button", { name: /previous/i });
    expect(previous).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => {
      const last = fetchMock.mock.calls
        .map(([input]) => input)
        .filter((input) => pathOf(input).startsWith("jobs?"))
        .at(-1);
      expect(paramsOf(last!).get("offset")).toBe("10");
    });
  });

  it("shows an honest empty state and a retryable error state", async () => {
    stored = [];
    const { unmount } = renderLibrary();
    expect(await screen.findByText("No stored jobs yet")).toBeTruthy();
    unmount();

    failList = true;
    renderLibrary();
    expect(await screen.findByText("Could not load jobs")).toBeTruthy();
    expect(screen.getByText("Jobs are unavailable.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });
});
