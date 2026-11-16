// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApplyJobCard } from "@/components/dashboard/apply-job-card";
import type { ImportJobResponse, Job } from "@/types/api";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: push }),
}));

const JOB_ID = "55555555-5555-4555-8555-555555555555";
const fetchMock = vi.fn<typeof fetch>();

function makeJob(): Job {
  return {
    id: JOB_ID,
    job_title: "Senior AI Engineer",
    company: null,
    location: null,
    job_url:
      "https://www.glassdoor.com/job-listing/senior-ai-engineer-JV_IC1.htm?jl=100",
    job_description: null,
    email_subject: "Imported from a pasted Glassdoor job link",
    received_at: null,
    source: "glassdoor",
    discovered_at: "2026-11-13T10:00:00Z",
    requirements: {
      required_skills: [],
      preferred_skills: [],
      mentioned_skills: [],
      experience_years: null,
      education_level: null,
      education_field: null,
      responsibilities: [],
      has_description: false,
      skills_source: "none",
      confidence: "low",
    },
    best_match: null,
    matches_count: 0,
    user_state: {
      saved: false,
      dismissed: false,
      saved_at: null,
      dismissed_at: null,
    },
  };
}

function importResponse(reused = false): ImportJobResponse {
  return {
    job: makeJob(),
    created: !reused,
    reused,
    extraction: {
      source: "glassdoor",
      title_source: "url_slug",
      description_available: false,
      missing_fields: ["company", "location", "description"],
      notes: [
        "Glassdoor was not scraped. The stored title was derived from the listing URL.",
      ],
    },
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApplyJobCard />
    </QueryClientProvider>,
  );
}

describe("ApplyJobCard", () => {
  beforeEach(() => {
    push.mockReset();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("validates the URL before calling the backend", async () => {
    renderCard();

    fireEvent.change(screen.getByLabelText("Glassdoor job listing URL"), {
      target: { value: "https://www.linkedin.com/jobs/view/1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /import job/i }));

    expect(
      await screen.findByText(/only glassdoor job listing urls are supported/i),
    ).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("imports a valid listing and opens its workspace", async () => {
    fetchMock.mockImplementation(async (input) => {
      const path = String(input).replace(/^.*\/api\/backend\//, "");
      if (path === "jobs/import") return json(importResponse());
      return json({ detail: `Unhandled ${path}` }, 404);
    });

    renderCard();
    fireEvent.change(screen.getByLabelText("Glassdoor job listing URL"), {
      target: { value: "https://www.glassdoor.com/job-listing/x-JV_1.htm?jl=1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /import job/i }));

    await waitFor(() => expect(push).toHaveBeenCalled(), { timeout: 2500 });
    expect(push).toHaveBeenCalledWith(`/jobs/${JOB_ID}`);

    const call = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/backend/jobs/import"),
    );
    expect(call).toBeTruthy();
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      url: "https://www.glassdoor.com/job-listing/x-JV_1.htm?jl=1",
    });
  });

  it("opens the same workspace when the posting was already stored", async () => {
    fetchMock.mockImplementation(async (input) => {
      const path = String(input).replace(/^.*\/api\/backend\//, "");
      if (path === "jobs/import") return json(importResponse(true));
      return json({ detail: `Unhandled ${path}` }, 404);
    });

    renderCard();
    fireEvent.change(screen.getByLabelText("Glassdoor job listing URL"), {
      target: { value: "https://www.glassdoor.com/job-listing/x-JV_1.htm?jl=1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /import job/i }));

    expect(
      await screen.findByText(/posting already in your library/i),
    ).toBeTruthy();
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/jobs/${JOB_ID}`), {
      timeout: 2500,
    });
  });

  it("shows the backend rejection instead of navigating", async () => {
    fetchMock.mockImplementation(async (input) => {
      const path = String(input).replace(/^.*\/api\/backend\//, "");
      if (path === "jobs/import") {
        return json(
          {
            detail:
              "The application host resolved to a private or internal address.",
            code: "unsafe_application_host",
          },
          400,
        );
      }
      return json({ detail: `Unhandled ${path}` }, 404);
    });

    renderCard();
    fireEvent.change(screen.getByLabelText("Glassdoor job listing URL"), {
      target: { value: "https://www.glassdoor.com/job-listing/x-JV_1.htm?jl=1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /import job/i }));

    expect(
      await screen.findByText(/resolved to a private or internal address/i),
    ).toBeTruthy();
    expect(push).not.toHaveBeenCalled();
  });
});
