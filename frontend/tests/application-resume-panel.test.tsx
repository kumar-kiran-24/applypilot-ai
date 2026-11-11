// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationResumePanel } from "@/components/applications/application-resume-panel";
import { resumeComparison } from "./fixtures/intelligence";
import type { ApplicationPreview, Resume } from "@/types/api";

const APPLICATION_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const STRONG = "11111111-1111-4111-8111-111111111111";
const WEAK = "33333333-3333-4333-8333-333333333333";
const MANUAL_REASON = "Resume selected manually by you for this application.";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function resume(id: string, filename: string): Resume {
  return {
    id,
    original_filename: filename,
    file_size: 1024,
    content_type: "application/pdf",
    is_default: id === STRONG,
    parsing_status: "completed",
    parsing_error: null,
    parsed_at: "2026-11-01T00:00:00Z",
    uploaded_at: "2026-11-01T00:00:00Z",
    skills: ["Python"],
    parsed_data: {
      full_name: "Ada Lovelace",
      email: "ada@example.com",
      phone: null,
      professional_summary: null,
      education: [],
      experience: [],
      projects: [],
      certifications: [],
      links: [],
    },
  };
}

function preview(overrides: Partial<ApplicationPreview> = {}): ApplicationPreview {
  return {
    application_id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    platform: "greenhouse",
    platform_name: "Greenhouse",
    application_url: "https://boards.greenhouse.io/acme/jobs/123",
    job_listing_url: null,
    employer_careers_url: null,
    application_url_is_job_board: false,
    suggested_application_url: null,
    automation_mode: "assisted",
    inspection_mode: "automated",
    status: "awaiting_user_information",
    stage: null,
    approved_at: null,
    submitted_at: null,
    submission_reference: null,
    failure_reason: null,
    requires_account: false,
    notes: [],
    resume: {
      id: STRONG,
      filename: "backend-strong.pdf",
      reason:
        "Recommended by resume comparison: 82.5% compatibility for backend-strong.pdf.",
      score: 82.5,
    },
    sections: [],
    missing_required: [],
    progress: {
      total: 0,
      confirmed: 0,
      required_total: 0,
      required_confirmed: 0,
      percent: 0,
    },
    ready_to_approve: false,
    can_submit: false,
    submission_mode: "manual",
    manual_steps: [],
    warnings: [],
    ...overrides,
  };
}

function recommendationFor(id: string, filename: string) {
  const base = resumeComparison();
  return resumeComparison({
    recommendation: {
      ...base.recommendation!,
      resume_id: id,
      resume_title: filename,
      overall_score: 88,
      confidence: "medium",
    },
  });
}

const fetchMock = vi.fn<typeof fetch>();
let calls: Array<{ path: string; method: string }> = [];

function installFetch(
  recommendation: () => Response,
  options: { compare?: () => Response } = {},
) {
  calls = [];
  fetchMock.mockImplementation(async (input, init) => {
    const path = String(input).replace(/^.*\/api\/backend\//, "");
    const method = init?.method ?? "GET";
    calls.push({ path, method });

    if (path === "resumes") {
      return json([
        resume(STRONG, "backend-strong.pdf"),
        resume(WEAK, "backend-weak.pdf"),
      ]);
    }
    if (path === `jobs/${JOB_ID}/recommendation`) {
      return recommendation();
    }
    if (path === `jobs/${JOB_ID}/compare-resumes`) {
      if (options.compare) return options.compare();
      return json(recommendationFor(WEAK, "backend-weak.pdf"));
    }
    if (path === `applications/${APPLICATION_ID}/resume`) {
      return json(
        preview({
          resume: {
            id: WEAK,
            filename: "backend-weak.pdf",
            reason: MANUAL_REASON,
            score: null,
          },
        }),
      );
    }
    return json({ detail: `Unhandled test route: ${path}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function renderPanel(
  applicationPreview: ApplicationPreview = preview(),
  editable = true,
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApplicationResumePanel
        applicationId={APPLICATION_ID}
        preview={applicationPreview}
        editable={editable}
      />
    </QueryClientProvider>,
  );
}

describe("ApplicationResumePanel", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the recommendation but changes the resume only on explicit click", async () => {
    installFetch(() => json(recommendationFor(WEAK, "backend-weak.pdf")));
    renderPanel();

    expect(
      await screen.findByRole("button", { name: /use recommended resume/i }),
    ).toBeTruthy();
    expect(screen.getByText("88% compatibility")).toBeTruthy();
    expect(screen.getByText("Medium confidence")).toBeTruthy();

    // Loading the recommendation never writes a selection by itself.
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
    expect(calls.some((call) => /approve|submit/i.test(call.path))).toBe(false);

    fireEvent.click(
      screen.getByRole("button", { name: /use recommended resume/i }),
    );

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === "PATCH" &&
            call.path === `applications/${APPLICATION_ID}/resume`,
        ),
      ).toBe(true),
    );
    expect(calls.some((call) => /approve|submit/i.test(call.path))).toBe(false);
  });

  it("keeps a manually selected resume and says so", async () => {
    installFetch(() => json(recommendationFor(STRONG, "backend-strong.pdf")));
    renderPanel(
      preview({
        resume: {
          id: WEAK,
          filename: "backend-weak.pdf",
          reason: MANUAL_REASON,
          score: null,
        },
      }),
    );

    await screen.findByText("Your selection is kept");
    expect(screen.getByText("Selected by you")).toBeTruthy();
    expect(screen.getByText(MANUAL_REASON)).toBeTruthy();
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);

    // An explicit choice of the recommended resume is still available.
    expect(
      await screen.findByRole("button", { name: /use recommended resume/i }),
    ).toBeTruthy();
  });

  it("marks the current resume when it is the recommendation", async () => {
    installFetch(() => json(resumeComparison()));
    renderPanel();

    expect(
      await screen.findByText(/This is the recommended resume for the job/i),
    ).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Recommended match")).toBeTruthy());
    expect(
      screen.queryByRole("button", { name: /use recommended resume/i }),
    ).toBeNull();
  });

  it("runs a comparison when none is stored", async () => {
    let compared = false;
    installFetch(
      () =>
        json(
          compared
            ? recommendationFor(WEAK, "backend-weak.pdf")
            : resumeComparison({
                status: "not_computed",
                total_resumes: 0,
                compared_resumes: 0,
                resumes: [],
                recommendation: null,
                processing_failures: [],
                message:
                  "No comparison has been stored for this job yet. Compare your resumes to see the recommended one.",
              }),
        ),
      {
        compare: () => {
          compared = true;
          return json(recommendationFor(WEAK, "backend-weak.pdf"));
        },
      },
    );
    renderPanel();

    expect(
      await screen.findByText(/No comparison has been stored for this job yet/i),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^compare resumes$/i }));

    expect(
      await screen.findByRole("button", { name: /use recommended resume/i }),
    ).toBeTruthy();
  });

  it("reports when no resume has a confident recommendation", async () => {
    installFetch(() =>
      json(
        resumeComparison({
          status: "no_suitable_resume",
          recommendation: null,
          message:
            "No resume had enough usable evidence for a recommendation. Review or analyze your resumes, then compare again.",
        }),
      ),
    );
    renderPanel();

    expect(
      await screen.findByText(/No resume had enough usable evidence/i),
    ).toBeTruthy();
    expect(screen.queryByText("This is the recommended resume for the job.")).toBeNull();
    expect(screen.queryByRole("button", { name: /use recommended resume/i })).toBeNull();
  });

  it("tells the user when the selected resume needs analysis", async () => {
    installFetch(() => json(recommendationFor(STRONG, "backend-strong.pdf")));
    renderPanel(
      preview({
        resume: {
          id: WEAK,
          filename: "backend-weak.pdf",
          reason: "Selected as the highest stored compatibility result.",
          score: 71,
        },
      }),
    );

    expect(
      await screen.findByText(/Resume analysis is not current/i),
    ).toBeTruthy();
    expect(
      screen.getByText(/No resume analysis is stored/i),
    ).toBeTruthy();
  });

  it("keeps resume selection usable when the recommendation is unavailable", async () => {
    installFetch(() =>
      json({ detail: "Recommendation service unavailable." }, 503),
    );
    renderPanel();

    expect(await screen.findByText("Recommendation unavailable")).toBeTruthy();
    expect(screen.getByLabelText("Choose a resume")).toBeTruthy();
    expect(screen.getByRole("button", { name: /use this resume/i })).toBeTruthy();
    expect(calls.some((call) => /approve|submit/i.test(call.path))).toBe(false);
  });

  it("does not offer a change once the application is no longer editable", async () => {
    installFetch(() => json(recommendationFor(WEAK, "backend-weak.pdf")));
    renderPanel(preview({ status: "submitted" }), false);

    await screen.findByText("88% compatibility");
    expect(screen.queryByLabelText("Choose a resume")).toBeNull();
    expect(
      screen.getByRole("button", { name: /use recommended resume/i }),
    ).toHaveProperty("disabled", true);
  });
});
