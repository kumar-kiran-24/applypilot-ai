import { describe, expect, it } from "vitest";

import {
  EASY_APPLY_STAGE_LABELS,
  canAnalyzeDestination,
  describeRedirect,
  platformLabel,
  redirectStatusLabel,
  resolveEasyApplyStages,
  smartApplyStepUnsupported,
} from "@/features/applications/redirect-workflow";
import type {
  ApplicationRedirectResponse,
  BrowserSession,
  RedirectStatus,
} from "@/types/api";

function session(overrides: Partial<BrowserSession> = {}): BrowserSession {
  return {
    session_id: "22222222-2222-4222-8222-222222222222",
    application_id: "11111111-1111-4111-8111-111111111111",
    job_id: null,
    job_title: "Associate AI Engineer",
    company: "Digital Green",
    application_url:
      "https://www.glassdoor.com/job-listing/associate-ai-engineer-JV_1.htm",
    listing_url:
      "https://www.glassdoor.com/job-listing/associate-ai-engineer-JV_1.htm",
    application_destination_url: null,
    destination_platform: null,
    destination_validated: false,
    redirect_status: "awaiting_user_action",
    redirect_flow: true,
    run_number: 1,
    state: "browser_ready",
    browser_status: "open",
    current_url:
      "https://www.glassdoor.com/job-listing/associate-ai-engineer-JV_1.htm",
    allowed_origins: ["https://www.glassdoor.com:443"],
    pause_reason: null,
    failure_code: null,
    failure_reason: null,
    checkpoint: {},
    active: true,
    can_pause: true,
    can_resume: false,
    can_cancel: true,
    started_at: null,
    last_activity_at: null,
    expires_at: null,
    stopped_at: null,
    view: {
      available: true,
      mode: "cdp_relay",
      user_can_interact: true,
      reason: "Live.",
      required_component: null,
    },
    ...overrides,
  };
}

function destination(
  overrides: Partial<ApplicationRedirectResponse> = {},
): ApplicationRedirectResponse {
  return {
    application_id: "11111111-1111-4111-8111-111111111111",
    browser_session_id: "22222222-2222-4222-8222-222222222222",
    status: "validated",
    message: "Indeed SmartApply detected at the resume-selection step.",
    listing_url:
      "https://www.glassdoor.com/job-listing/associate-ai-engineer-JV_1.htm",
    destination_url:
      "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection",
    application_platform: "indeed_smartapply",
    platform_name: "Indeed SmartApply",
    application_step: "resume_selection",
    step_supported: true,
    destination_validated: true,
    awaiting_user_action: false,
    can_analyze: true,
    authentication_required: false,
    warnings: [],
    ...overrides,
  };
}

function stateOf(
  stages: ReturnType<typeof resolveEasyApplyStages>,
  id: string,
) {
  return stages.find((stage) => stage.id === id)?.state;
}

describe("resolveEasyApplyStages", () => {
  it("renders all nine stages in workflow order", () => {
    const stages = resolveEasyApplyStages({});
    expect(stages).toHaveLength(9);
    expect(stages.map((stage) => stage.label)).toEqual(
      Object.values(EASY_APPLY_STAGE_LABELS).map((label, index) =>
        index === 4 ? "Indeed SmartApply detected" : label,
      ),
    );
    expect(stateOf(stages, "opening_listing")).toBe("current");
  });

  it("waits for the user's Easy Apply click after opening the listing", () => {
    const stages = resolveEasyApplyStages({ session: session() });
    expect(stateOf(stages, "opening_listing")).toBe("done");
    expect(stateOf(stages, "awaiting_easy_apply")).toBe("current");
    expect(stateOf(stages, "platform_detected")).toBe("pending");
  });

  it("advances only after the destination is validated", () => {
    const stages = resolveEasyApplyStages({
      session: session(),
      destination: destination(),
    });
    expect(stateOf(stages, "validating_destination")).toBe("done");
    expect(stateOf(stages, "platform_detected")).toBe("done");
    expect(stateOf(stages, "analyzing_form")).toBe("current");
    expect(stages[4]?.label).toBe("Indeed SmartApply detected");
  });

  it("moves through analysis, filling and final review", () => {
    const filling = resolveEasyApplyStages({
      session: session(),
      destination: destination(),
      hasAnalysis: true,
      fillStatus: "running",
    });
    expect(stateOf(filling, "analyzing_form")).toBe("done");
    expect(stateOf(filling, "filling_fields")).toBe("current");

    const review = resolveEasyApplyStages({
      session: session(),
      destination: destination(),
      hasAnalysis: true,
      fillStatus: "ready_for_final_review",
    });
    expect(stateOf(review, "awaiting_review")).toBe("done");
    expect(stateOf(review, "ready_for_submission")).toBe("current");
  });

  it("shows a blocked validation stage for a refused destination", () => {
    const stages = resolveEasyApplyStages({
      session: session({ redirect_status: "unsupported" }),
      destination: destination({
        status: "unsupported",
        destination_validated: false,
        destination_url: null,
        application_platform: null,
        platform_name: null,
        application_step: null,
        step_supported: false,
        can_analyze: false,
      }),
    });
    expect(stateOf(stages, "validating_destination")).toBe("blocked");
    expect(stateOf(stages, "platform_detected")).toBe("pending");
  });

  it("keeps the user in control when Indeed requires sign-in", () => {
    const notice = describeRedirect({
      session: session({ redirect_status: "awaiting_user_action" }),
      destination: destination({
        status: "awaiting_user_action",
        destination_validated: false,
        awaiting_user_action: true,
        authentication_required: true,
        message: "This application platform requires you to sign in.",
      }),
    });
    expect(notice.title).toContain("Sign in");
    expect(notice.detail).toContain("never enters passwords");
  });

  it("does not claim a live session after expiry", () => {
    const stages = resolveEasyApplyStages({
      session: session({ browser_status: "expired" }),
    });
    expect(stateOf(stages, "opening_listing")).toBe("blocked");
  });
});

describe("describeRedirect", () => {
  it("distinguishes awaiting user action from unsupported destinations", () => {
    expect(describeRedirect({ session: session() }).title).toContain(
      "Waiting for you",
    );
    const unsupported = describeRedirect({
      session: session({ redirect_status: "unsupported" }),
      destination: destination({ status: "unsupported" }),
    });
    expect(unsupported.tone).toBe("warning");
    expect(unsupported.title).toBe("Unsupported application destination");
  });

  it("treats a private-address refusal as unsafe, not a generic failure", () => {
    const rejected = describeRedirect({
      session: session({ redirect_status: "rejected" }),
      destination: destination({
        status: "rejected",
        destination_validated: false,
        destination_url: null,
      }),
    });
    expect(rejected.tone).toBe("danger");
    expect(rejected.title).toContain("Unsafe");
  });

  it("reports a validated SmartApply step without claiming submission", () => {
    const notice = describeRedirect({
      session: session({ redirect_status: "validated" }),
      destination: destination(),
    });
    expect(notice.tone).toBe("success");
    expect(notice.title).toContain("Indeed SmartApply");
    expect(notice.title).toContain("resume selection");
  });

  it("reports an expired browser rather than a platform problem", () => {
    const notice = describeRedirect({
      session: session({ browser_status: "expired" }),
    });
    expect(notice.tone).toBe("warning");
    expect(notice.title).toContain("expired");
  });
});

describe("analysis availability", () => {
  it("allows analysis for a validated non-SmartApply destination", () => {
    const greenhouse = destination({
      application_platform: "greenhouse",
      platform_name: "Greenhouse",
      destination_url: "https://boards.greenhouse.io/acme/jobs/123",
      application_step: null,
      step_supported: false,
    });
    expect(smartApplyStepUnsupported({ destination: greenhouse })).toBe(false);
    expect(canAnalyzeDestination({ destination: greenhouse })).toBe(true);
  });

  it("blocks analysis only for an unrecognised SmartApply step", () => {
    const unknown = destination({
      application_step: "unknown",
      step_supported: false,
    });
    expect(smartApplyStepUnsupported({ destination: unknown })).toBe(true);
    expect(canAnalyzeDestination({ destination: unknown })).toBe(false);
    expect(canAnalyzeDestination({ destination: destination() })).toBe(true);
  });

  it("never offers analysis before the destination is validated", () => {
    expect(canAnalyzeDestination({ destination: null })).toBe(false);
    expect(
      canAnalyzeDestination({
        session: session({ redirect_status: "awaiting_user_action" }),
      }),
    ).toBe(false);
    expect(
      canAnalyzeDestination({
        session: session({
          redirect_status: "validated",
          destination_validated: true,
          destination_platform: "greenhouse",
        }),
      }),
    ).toBe(true);
  });
});

describe("labels", () => {
  it("uses the recorded platform name", () => {
    expect(platformLabel({ destination: destination() })).toBe(
      "Indeed SmartApply",
    );
    expect(platformLabel({ session: session() })).toBeNull();
  });

  it("labels every redirect status", () => {
    const statuses: RedirectStatus[] = [
      "not_started",
      "awaiting_user_action",
      "validated",
      "unsupported",
      "rejected",
    ];
    for (const status of statuses) {
      expect(redirectStatusLabel(status)).not.toBe("");
    }
  });
});
