/**
 * Shared Playwright fixtures for Phase 2G.
 *
 * The Next.js server still authenticates against the local mock API; every
 * client data request is fulfilled at the browser boundary. Nothing here
 * contacts Google, Gmail, Groq or a real employer.
 */
import type { Page } from "@playwright/test";

import {
  jobIntelligence,
  resumeComparison,
  resumeIntelligence,
} from "../tests/fixtures/intelligence";
export { jobIntelligence, resumeComparison, resumeIntelligence };

import type {
  ApplicationPreview,
  ApplicationRedirectResponse,
  ApplicationWorkflow,
  BrowserSession,
  BrowserSessionCapabilitiesResponse,
  EligibilityReport,
  FinalReviewResponse,
  GmailStatus,
  Job,
  JobListResponse,
  MatchingSummary,
  Resume,
  ResumeComparisonResponse,
} from "../src/types/api";

export const BASE_URL =
  process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3100";

export const JOB_ID = "22222222-2222-4222-8222-222222222222";
export const APPLICATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const STRONG = "11111111-1111-4111-8111-111111111111";
export const WEAK = "33333333-3333-4333-8333-333333333333";
export const SCANNED = "44444444-4444-4444-8444-444444444444";
export const MANUAL_REASON =
  "Resume selected manually by you for this application.";

export interface ClientCall {
  method: string;
  path: string;
  body: unknown;
}

export type FixtureHandler = (body: unknown) => unknown | Promise<unknown>;
export type FixtureHandlers = Record<string, FixtureHandler>;

interface HttpFixture {
  __httpStatus: number;
  __body: unknown;
}

function isHttpFixture(value: unknown): value is HttpFixture {
  return (
    typeof value === "object" &&
    value !== null &&
    "__httpStatus" in value &&
    "__body" in value
  );
}

export function httpError(
  status: number,
  detail: string,
  code = "test_error",
): HttpFixture {
  return { __httpStatus: status, __body: { detail, code } };
}

/**
 * Fulfil all BFF data calls and return a live list of requests. Unmapped
 * routes return a deterministic 404 instead of silently reaching the network.
 */
export async function installClientFixtures(
  page: Page,
  handlers: FixtureHandlers,
): Promise<ClientCall[]> {
  const calls: ClientCall[] = [];

  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/backend\//, "");
    const method = request.method();
    let body: unknown = null;
    const rawBody = request.postData();
    if (rawBody) {
      try {
        body = JSON.parse(rawBody) as unknown;
      } catch {
        body = rawBody;
      }
    }
    calls.push({ method, path, body });

    const handler = handlers[`${method} ${path}`];
    if (!handler) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({
          detail: `No fixture for ${method} ${path}`,
          code: "not_found",
        }),
      });
      return;
    }

    const value = await handler(body);
    if (isHttpFixture(value)) {
      await route.fulfill({
        status: value.__httpStatus,
        contentType: "application/json",
        body: JSON.stringify(value.__body),
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(value),
      headers: { "Cache-Control": "no-store" },
    });
  });

  return calls;
}

export async function signIn(page: Page, token = "valid-token"): Promise<void> {
  await page.context().addCookies([
    { name: "ap_session", value: token, url: BASE_URL },
  ]);
}

export const gmailConnected: GmailStatus = {
  connected: true,
  is_active: true,
  gmail_email: "pilot.tester@example.com",
  connected_at: "2026-11-10T09:00:00Z",
  granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
};

export const gmailDisconnected: GmailStatus = {
  connected: false,
  is_active: false,
  gmail_email: null,
  connected_at: null,
  granted_scopes: [],
};

export function makeJob(): Job {
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
      resume_id: STRONG,
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
  };
}

export function jobList(): JobListResponse {
  return { total: 1, jobs: [makeJob()] };
}

export function matchingSummary(): MatchingSummary {
  return {
    jobs_total: 1,
    jobs_matched: 1,
    resumes_total: 2,
    resumes_matched: 2,
    average_best_score: 82.5,
    best_match: {
      job_id: JOB_ID,
      job_title: "Senior Backend Engineer",
      company: "Acme",
      resume_id: STRONG,
      resume_filename: "backend-strong.pdf",
      overall_score: 82.5,
    },
    resume_stats: [
      {
        resume_id: STRONG,
        resume_filename: "backend-strong.pdf",
        is_default: true,
        matches_count: 1,
        recommended_jobs: 1,
        average_score: 82.5,
      },
      {
        resume_id: WEAK,
        resume_filename: "backend-weak.pdf",
        is_default: false,
        matches_count: 1,
        recommended_jobs: 0,
        average_score: 71,
      },
    ],
    last_computed_at: "2026-11-10T09:00:00Z",
    disclaimer: "Scores measure overlap with the posting, not hiring probability.",
  };
}

export function makeResume(id: string, filename: string): Resume {
  return {
    id,
    original_filename: filename,
    file_size: 1024,
    content_type: "application/pdf",
    is_default: id === STRONG,
    parsing_status: "completed",
    parsing_error: null,
    parsed_at: "2026-11-09T09:00:00Z",
    uploaded_at: "2026-11-09T09:00:00Z",
    skills: ["Python", "FastAPI"],
    parsed_data: {
      full_name: "Ada Lovelace",
      email: "ada@example.com",
      phone: null,
      professional_summary: "Backend engineer building Python services.",
      education: [],
      experience: [],
      projects: [],
      certifications: [],
      links: [],
    },
  };
}

export function scannedResume(): Resume {
  return {
    ...makeResume(SCANNED, "scanned-resume.pdf"),
    is_default: false,
    parsing_status: "ocr_required",
    parsing_error: null,
    parsed_at: null,
    skills: [],
    parsed_data: {
      full_name: null,
      email: null,
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

export function closeComparison(): ResumeComparisonResponse {
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

export function emptyComparison(): ResumeComparisonResponse {
  return resumeComparison({
    status: "not_computed",
    total_resumes: 0,
    compared_resumes: 0,
    resumes: [],
    recommendation: null,
    processing_failures: [],
    close_score: false,
    message: "Compare your resumes to build a recommendation.",
  });
}

export function makePreview(manual = false): ApplicationPreview {
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
    resume: manual
      ? {
          id: WEAK,
          filename: "backend-weak.pdf",
          reason: MANUAL_REASON,
          score: null,
        }
      : {
          id: STRONG,
          filename: "backend-strong.pdf",
          reason:
            "Recommended by resume comparison: 82.5% compatibility for backend-strong.pdf.",
          score: 82.5,
        },
    sections: [
      {
        section: "documents",
        label: "Documents",
        questions: [],
      },
    ],
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
  };
}

export const LISTING_URL =
  "https://www.glassdoor.com/job-listing/senior-backend-engineer-acme-JV_IC123.htm";
export const LISTING_HOST_PATH =
  "www.glassdoor.com/job-listing/senior-backend-engineer-acme-JV_IC123.htm";
export const SMARTAPPLY_URL =
  "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection";
export const SMARTAPPLY_HOST_PATH =
  "smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection";

/** A Glassdoor listing whose Easy Apply button hands off to Indeed SmartApply. */
export function makeJobBoardPreview(): ApplicationPreview {
  return {
    ...makePreview(),
    platform: "glassdoor",
    platform_name: "Glassdoor",
    application_url: LISTING_URL,
    job_listing_url: LISTING_URL,
    application_url_is_job_board: true,
    application_destination_url: null,
    destination_platform: null,
    destination_validated: false,
    redirect_status: "not_started",
    redirect_flow: true,
  };
}

export function makeRedirectSession(
  overrides: Partial<BrowserSession> = {},
): BrowserSession {
  return {
    session_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    application_id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    application_url: LISTING_URL,
    listing_url: LISTING_URL,
    application_destination_url: null,
    destination_platform: null,
    destination_validated: false,
    redirect_status: "not_started",
    redirect_flow: true,
    run_number: 1,
    state: "running",
    browser_status: "open",
    current_url: LISTING_URL,
    allowed_origins: ["https://www.glassdoor.com"],
    pause_reason: null,
    failure_code: null,
    failure_reason: null,
    checkpoint: {},
    active: true,
    can_pause: true,
    can_resume: false,
    can_cancel: true,
    started_at: "2026-11-10T09:00:00Z",
    last_activity_at: "2026-11-10T09:00:05Z",
    expires_at: "2026-11-10T09:15:00Z",
    stopped_at: null,
    view: {
      available: false,
      mode: "disabled",
      user_can_interact: false,
      reason: "Browser streaming is disabled in this test environment.",
      required_component: "playwright",
    },
    ...overrides,
  };
}

export function makeRedirect(
  overrides: Partial<ApplicationRedirectResponse> = {},
): ApplicationRedirectResponse {
  return {
    application_id: APPLICATION_ID,
    browser_session_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    status: "awaiting_user_action",
    message:
      "The job listing is open. Click Easy Apply yourself in the live browser.",
    listing_url: LISTING_URL,
    destination_url: null,
    application_platform: null,
    platform_name: null,
    application_step: null,
    step_supported: false,
    destination_validated: false,
    awaiting_user_action: true,
    can_analyze: false,
    authentication_required: false,
    warnings: [],
    ...overrides,
  };
}

export function validatedSmartApplyRedirect(): ApplicationRedirectResponse {
  return makeRedirect({
    status: "validated",
    message:
      "Indeed SmartApply detected at the resume-selection step. Analyse the form to review the visible controls; choosing or uploading the resume remains your decision.",
    destination_url: SMARTAPPLY_URL,
    application_platform: "indeed_smartapply",
    platform_name: "Indeed SmartApply",
    application_step: "resume_selection",
    step_supported: true,
    destination_validated: true,
    awaiting_user_action: false,
    can_analyze: true,
  });
}

export function makeWorkflow(): ApplicationWorkflow {
  return {
    application_id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    application_url: "https://boards.greenhouse.io/acme/jobs/123",
    status: "awaiting_user_information",
    workflow_stage: "needs_information",
    submission_phase: null,
    ready_for_final_review: false,
    missing_required_count: 0,
    blocking_items: [],
    next_actions: [],
    browser: {
      session_id: null,
      run_number: null,
      state: null,
      browser_status: null,
      current_url: null,
      live: false,
      expired: false,
      view_available: false,
    },
    analysis: {
      analysis_id: null,
      version: null,
      field_count: 0,
      page_url: null,
      analyzed_at: null,
    },
    execution: {
      execution_id: null,
      execution_status: null,
      run_number: null,
      analysis_version: null,
      succeeded_count: 0,
      verified_count: 0,
      failed_count: 0,
      skipped_count: 0,
      pending_confirmation_count: 0,
      can_pause: false,
      can_resume: false,
    },
    interventions: {
      total: 0,
      pending_count: 0,
      user_working_count: 0,
      blocking_count: 0,
    },
    submission: {
      confirmed_by_user: false,
      confirmed_at: null,
      reference: null,
      confirmation_url: null,
      confirmation_message: null,
      employer_confirmation_detected: false,
    },
    recovery: [],
    as_of: "2026-11-10T09:00:00Z",
    final_submission_note:
      "ApplyPilot never submits. You submit on the employer's site.",
  };
}

export const browserCapabilities: BrowserSessionCapabilitiesResponse = {
  view: {
    available: false,
    mode: "disabled",
    user_can_interact: false,
    reason: "Browser automation is disabled in this test environment.",
    required_component: "playwright",
  },
  automation_enabled: false,
  session_ttl_seconds: 900,
  final_submission_note:
    "ApplyPilot never submits. You submit on the employer's site.",
};

export function eligibilityReport(): EligibilityReport {
  return {
    application_id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    platform: "greenhouse",
    platform_name: "Greenhouse",
    application_url: "https://boards.greenhouse.io/acme/jobs/123",
    application_status: "awaiting_user_information",
    resume_selected: true,
    resume_id: STRONG,
    resume_filename: "backend-strong.pdf",
    resume_match_score: 82.5,
    overall_decision: "ready_to_prepare",
    readiness_percentage: 100,
    message: "This application meets the pilot preparation checks.",
    blocking_issues: [],
    warnings: [],
    required_user_actions: [],
    passed_checks: [],
    failed_checks: [],
    checks: [],
    automation_enabled: false,
    require_user_approval: true,
    require_user_review: true,
    approval: {
      approved: false,
      current: false,
      approved_at: null,
      approved_by_user_id: null,
      report_version: null,
      report_decision: null,
    },
    evaluation_timestamp: "2026-11-10T09:00:00Z",
    report_version: "eligibility-v1",
  };
}

export function emptyInterventions() {
  return {
    application_id: APPLICATION_ID,
    application_url: "https://boards.greenhouse.io/acme/jobs/123",
    browser_session_id: null,
    view: browserCapabilities.view,
    total: 0,
    pending_count: 0,
    user_working_count: 0,
    blocking_count: 0,
    interventions: [],
    final_submission_note:
      "ApplyPilot never submits. You submit on the employer's site.",
  };
}

export function emptyFinalReview(): FinalReviewResponse {
  return {
    application_id: APPLICATION_ID,
    execution_id: null,
    execution_status: "not_started",
    analysis_version: null,
    ready_for_final_review: false,
    generated_at: "2026-11-10T09:00:00Z",
    resume: {
      resume_id: null,
      filename: null,
      will_upload: false,
      file_field_ids: [],
      reason: "No fill plan exists yet.",
    },
    sections: [],
    filled_field_labels: [],
    verified_field_labels: [],
    skipped_field_labels: [],
    confirmation_fields: [],
    blocking_interventions: [],
    missing_optional_fields: [],
    filled_count: 0,
    verified_count: 0,
    skipped_count: 0,
    blocker_count: 0,
    view: browserCapabilities.view,
    manual_submission_required: true,
    final_submission_note:
      "ApplyPilot never submits. You submit on the employer's site.",
  };
}

export function makeHandlers(overrides: FixtureHandlers = {}): FixtureHandlers {
  return {
    "GET gmail/status": () => gmailConnected,
    "GET jobs": () => jobList(),
    "GET matching/summary": () => matchingSummary(),
    ...overrides,
  };
}

