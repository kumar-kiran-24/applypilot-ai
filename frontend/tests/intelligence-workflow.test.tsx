// @vitest-environment jsdom
/**
 * Phase 2F.2.7 frontend workflow integration.
 *
 * One shared synthetic backend drives the real job-intelligence,
 * resume-intelligence, comparison and application-resume panels through the
 * documented user journey. A remount with a fresh QueryClient models a page
 * refresh and proves the persisted resume selection survives it.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationResumePanel } from "@/components/applications/application-resume-panel";
import { JobIntelligencePanel } from "@/components/intelligence/job-intelligence-panel";
import { ResumeComparisonPanel } from "@/components/intelligence/resume-comparison-panel";
import { ResumeIntelligencePanel } from "@/components/intelligence/resume-intelligence-panel";
import { useApplication } from "@/features/applications/hooks";
import type { ApplicationPreview, Resume, ResumeComparisonResponse } from "@/types/api";

import {
  jobIntelligence,
  resumeComparison,
  resumeIntelligence,
} from "./fixtures/intelligence";

const JOB_ID = "22222222-2222-4222-8222-222222222222";
const APPLICATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const STRONG = "11111111-1111-4111-8111-111111111111";
const WEAK = "33333333-3333-4333-8333-333333333333";
const SCANNED = "44444444-4444-4444-8444-444444444444";
const MANUAL_REASON = "Resume selected manually by you for this application.";

type Selection = {
  id: string | null;
  filename: string | null;
  reason: string;
  score: number | null;
};

const RECOMMENDED_SELECTION: Selection = {
  id: STRONG,
  filename: "backend-strong.pdf",
  reason:
    "Recommended by resume comparison: 82.5% compatibility for backend-strong.pdf.",
  score: 82.5,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
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

function preview(selection: Selection): ApplicationPreview {
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
      id: selection.id,
      filename: selection.filename,
      reason: selection.reason,
      score: selection.score,
    },
    sections: [],
    missing_required: [],
    progress: { total: 0, confirmed: 0, required_total: 0, required_confirmed: 0, percent: 0 },
    ready_to_approve: false,
    can_submit: false,
    submission_mode: "manual",
    manual_steps: [],
    warnings: [],
  };
}

/** A close call so the workflow also exercises uncertainty communication. */
function closeComparison(): ResumeComparisonResponse {
  const base = resumeComparison();
  return resumeComparison({
    close_score: true,
    recommendation: {
      ...base.recommendation!,
      close_call: true,
      confidence: "medium",
      close_call_explanation:
        "The top two resumes are within 3 points; required-skill coverage and project evidence decide the preference.",
    },
  });
}

function notComputed(): ResumeComparisonResponse {
  return resumeComparison({
    status: "not_computed",
    total_resumes: 0,
    compared_resumes: 0,
    resumes: [],
    recommendation: null,
    processing_failures: [],
    message: "Compare your resumes to build a recommendation.",
  });
}

function Workspace() {
  const application = useApplication(APPLICATION_ID);
  return (
    <div>
      <div data-testid="job-intelligence">
        <JobIntelligencePanel jobId={JOB_ID} />
      </div>
      <div data-testid="resume-strong">
        <ResumeIntelligencePanel resumeId={STRONG} parsingStatus="completed" />
      </div>
      <div data-testid="resume-scanned">
        <ResumeIntelligencePanel resumeId={SCANNED} parsingStatus="ocr_required" />
      </div>
      <div data-testid="comparison">
        <ResumeComparisonPanel jobId={JOB_ID} />
      </div>
      <div data-testid="application">
        {application.data ? (
          <ApplicationResumePanel
            applicationId={APPLICATION_ID}
            preview={application.data}
            editable
          />
        ) : (
          <p>Loading application…</p>
        )}
      </div>
    </div>
  );
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
      <Workspace />
    </QueryClientProvider>,
  );
}

const fetchMock = vi.fn<typeof fetch>();
let calls: Array<{ path: string; method: string; body: unknown }> = [];
let jobAnalyzed = false;
let resumeAnalyzed = false;
let compared = false;
let selection: Selection = { ...RECOMMENDED_SELECTION };

function installBackend({ failReads = false }: { failReads?: boolean } = {}) {
  calls = [];
  fetchMock.mockImplementation(async (input, init) => {
    const path = pathOf(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ path, method, body });

    if (failReads && method === "GET") {
      return json(
        { detail: "Intelligence service unavailable.", code: "unavailable" },
        503,
      );
    }
    if (path === `jobs/${JOB_ID}/intelligence`) {
      return json(
        jobIntelligence({
          extraction: { cached: jobAnalyzed },
        }),
      );
    }
    if (path === `jobs/${JOB_ID}/analyze`) {
      jobAnalyzed = true;
      return json(jobIntelligence({ extraction: { cached: true } }));
    }
    if (path === `resumes/${STRONG}/intelligence`) {
      return json(
        resumeIntelligence({
          extraction: resumeAnalyzed
            ? { analysis_state: "ready", cached: true }
            : { analysis_state: "missing", cached: false },
          message: resumeAnalyzed
            ? "Cached analysis reused. Send force=true to re-run it."
            : "No resume analysis has been stored yet. This reading was computed for this response only.",
        }),
      );
    }
    if (path === `resumes/${STRONG}/analyze`) {
      resumeAnalyzed = true;
      return json(
        resumeIntelligence({ extraction: { analysis_state: "ready", cached: false } }),
      );
    }
    if (path === `jobs/${JOB_ID}/recommendation`) {
      return json(compared ? closeComparison() : notComputed());
    }
    if (path === `jobs/${JOB_ID}/compare-resumes`) {
      compared = true;
      return json(closeComparison());
    }
    if (path === "resumes") {
      return json([
        resume(STRONG, "backend-strong.pdf"),
        resume(WEAK, "backend-weak.pdf"),
      ]);
    }
    if (path === `applications/${APPLICATION_ID}` && method === "GET") {
      return json(preview(selection));
    }
    if (path === `applications/${APPLICATION_ID}/resume` && method === "PATCH") {
      selection = {
        id: WEAK,
        filename: "backend-weak.pdf",
        reason: MANUAL_REASON,
        score: null,
      };
      return json(preview(selection));
    }
    return json({ detail: `Unhandled test route: ${method} ${path}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function noApprovalOrSubmissionCalls() {
  expect(calls.some((call) => /approve|submit/i.test(call.path))).toBe(false);
}

describe("Phase 2F intelligence workflow", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    jobAnalyzed = false;
    resumeAnalyzed = false;
    compared = false;
    selection = { ...RECOMMENDED_SELECTION };
    installBackend();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("runs analysis, comparison, selection and refresh without losing the choice", async () => {
    renderWorkspace();

    // 1. Job intelligence opens with an explicit unsaved state and can be saved.
    const jobPanel = within(screen.getByTestId("job-intelligence"));
    expect(await jobPanel.findByText("Required skills")).toBeTruthy();
    expect(jobPanel.getByText("Not saved yet")).toBeTruthy();
    fireEvent.click(jobPanel.getByRole("button", { name: /analyze and save/i }));
    expect(await jobPanel.findByText("Saved analysis")).toBeTruthy();
    expect(
      calls.some(
        (call) =>
          call.path === `jobs/${JOB_ID}/analyze` &&
          JSON.stringify(call.body) ===
            JSON.stringify({ force: false, use_llm: true }),
      ),
    ).toBe(true);

    // 2. Resume intelligence distinguishes missing from ready and saves on click.
    const strongPanel = within(screen.getByTestId("resume-strong"));
    expect(await strongPanel.findByText("Not analyzed yet")).toBeTruthy();
    expect(strongPanel.getByText(/not saved/i)).toBeTruthy();
    fireEvent.click(strongPanel.getByRole("button", { name: /analyze resume/i }));
    expect(await strongPanel.findByText("Analysis ready")).toBeTruthy();
    expect(
      calls.some(
        (call) =>
          call.path === `resumes/${STRONG}/analyze` &&
          JSON.stringify(call.body) === JSON.stringify({ force: false }),
      ),
    ).toBe(true);

    // 3. A scanned resume states OCR is required and infers nothing.
    const scannedPanel = within(screen.getByTestId("resume-scanned"));
    expect(scannedPanel.getByText("OCR required before analysis")).toBeTruthy();
    expect(scannedPanel.getByText(/Nothing was inferred from the scan/i)).toBeTruthy();
    expect(
      calls.some((call) => call.path === `resumes/${SCANNED}/intelligence`),
    ).toBe(false);

    // 4/5/6. Comparison renders ranking, alternatives and close-call uncertainty.
    const comparisonPanel = within(screen.getByTestId("comparison"));
    expect(await comparisonPanel.findByText("No comparison yet")).toBeTruthy();
    fireEvent.click(comparisonPanel.getByRole("button", { name: /compare resumes/i }));

    expect(await comparisonPanel.findByText("Recommended resume")).toBeTruthy();
    expect(comparisonPanel.getByText("82.5% compatibility")).toBeTruthy();
    expect(comparisonPanel.getByText("Scores are close")).toBeTruthy();
    expect(
      comparisonPanel.getByText(/within 3 points; required-skill coverage/i),
    ).toBeTruthy();
    expect(comparisonPanel.getAllByText("backend-weak.pdf").length).toBeGreaterThan(0);
    expect(comparisonPanel.getByText("71% · 11.5 points lower")).toBeTruthy();
    expect(
      comparisonPanel.getAllByText(/measure overlap with the posting, not hiring probability/i)
        .length,
    ).toBeGreaterThan(0);
    expect(
      calls.some(
        (call) =>
          call.path === `jobs/${JOB_ID}/compare-resumes` &&
          JSON.stringify(call.body) === JSON.stringify({ force: false }),
      ),
    ).toBe(true);

    // 7. The application starts on the recommendation without a write.
    const applicationPanel = within(screen.getByTestId("application"));
    expect(
      await applicationPanel.findByText("This is the recommended resume for the job."),
    ).toBeTruthy();
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
    noApprovalOrSubmissionCalls();

    // An alternative is selected only by an explicit user action.
    fireEvent.change(applicationPanel.getByLabelText("Choose a resume"), {
      target: { value: WEAK },
    });
    fireEvent.click(applicationPanel.getByRole("button", { name: /use this resume/i }));

    expect(await applicationPanel.findByText("Selected by you")).toBeTruthy();
    expect(applicationPanel.getByText(MANUAL_REASON)).toBeTruthy();
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === "PATCH" &&
            call.path === `applications/${APPLICATION_ID}/resume` &&
            JSON.stringify(call.body) === JSON.stringify({ resume_id: WEAK }),
        ),
      ).toBe(true),
    );
    noApprovalOrSubmissionCalls();

    // 8/9. A fresh page load (new QueryClient) reads the persisted selection.
    cleanup();
    const callsBeforeRefresh = calls.length;
    renderWorkspace();

    const reloadedPanel = within(screen.getByTestId("application"));
    expect(await reloadedPanel.findByText("Selected by you")).toBeTruthy();
    expect(reloadedPanel.getByText(MANUAL_REASON)).toBeTruthy();
    expect(reloadedPanel.getByText("backend-weak.pdf")).toBeTruthy();
    expect(
      reloadedPanel.queryByText("This is the recommended resume for the job."),
    ).toBeNull();
    // Reloading never silently replaces the manual choice with the recommendation.
    expect(
      calls
        .slice(callsBeforeRefresh)
        .some((call) => call.method === "PATCH"),
    ).toBe(false);
    noApprovalOrSubmissionCalls();
  });

  it("shows safe errors when the intelligence APIs fail", async () => {
    installBackend({ failReads: true });
    renderWorkspace();

    expect(
      await screen.findByText(/could not load job intelligence/i),
    ).toBeTruthy();
    expect(
      screen.getAllByText(/intelligence service unavailable/i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/could not load the comparison/i).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /retry/i }).length).toBeGreaterThan(0);

    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/traceback|storage_key|storage_backend|checksum/i);
    noApprovalOrSubmissionCalls();
  });
});
