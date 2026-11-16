import type {
  ApplicationRedirectResponse,
  BrowserSession,
  RedirectStatus,
} from "@/types/api";

export type EasyApplyStageState = "done" | "current" | "pending" | "blocked";

export interface EasyApplyStage {
  id: EasyApplyStageId;
  label: string;
  state: EasyApplyStageState;
}

export type EasyApplyStageId =
  | "opening_listing"
  | "awaiting_easy_apply"
  | "detecting_redirect"
  | "validating_destination"
  | "platform_detected"
  | "analyzing_form"
  | "filling_fields"
  | "awaiting_review"
  | "ready_for_submission";

export interface EasyApplyStageInput {
  session?: BrowserSession | null;
  destination?: ApplicationRedirectResponse | null;
  /** True once a form analysis version exists for the current session. */
  hasAnalysis?: boolean;
  fillStatus?: string | null;
  readyForReview?: boolean;
}

export interface RedirectNotice {
  tone: "info" | "success" | "warning" | "danger";
  title: string;
  detail: string;
}

export const EASY_APPLY_STAGE_LABELS: Record<EasyApplyStageId, string> = {
  opening_listing: "Opening the job listing",
  awaiting_easy_apply: "Waiting for you to click Easy Apply",
  detecting_redirect: "Detecting the application redirect",
  validating_destination: "Validating the application destination",
  platform_detected: "Indeed SmartApply detected",
  analyzing_form: "Analyzing the application form",
  filling_fields: "Filling verified information",
  awaiting_review: "Waiting for your review",
  ready_for_submission: "Ready for manual submission",
};

const ORDER: EasyApplyStageId[] = [
  "opening_listing",
  "awaiting_easy_apply",
  "detecting_redirect",
  "validating_destination",
  "platform_detected",
  "analyzing_form",
  "filling_fields",
  "awaiting_review",
  "ready_for_submission",
];

function redirectStatus(input: EasyApplyStageInput): RedirectStatus {
  return (
    input.destination?.status ??
    input.session?.redirect_status ??
    "not_started"
  );
}

export function platformLabel(input: EasyApplyStageInput): string | null {
  const name =
    input.destination?.platform_name || input.session?.destination_platform;
  if (!name) return null;
  if (name === "indeed_smartapply") return "Indeed SmartApply";
  return name
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** Resolve the nine Easy Apply stages from recorded state, without guessing. */
export function resolveEasyApplyStages(
  input: EasyApplyStageInput,
): EasyApplyStage[] {
  const states: EasyApplyStageState[] = ORDER.map(() => "pending");
  const labelFor = (id: EasyApplyStageId): string => {
    if (id === "platform_detected") {
      const platform = platformLabel(input);
      return platform ? `${platform} detected` : EASY_APPLY_STAGE_LABELS[id];
    }
    return EASY_APPLY_STAGE_LABELS[id];
  };
  const session = input.session;
  const status = redirectStatus(input);
  const validated = Boolean(
    input.destination?.destination_validated ??
      session?.destination_validated,
  );

  if (!session) {
    states[0] = "current";
  } else if (session.browser_status === "expired") {
    states[0] = "blocked";
  } else {
    states[0] = session.browser_status === "open" ? "done" : "current";
    if (session.browser_status === "open") {
      if (status === "unsupported" || status === "rejected") {
        states[1] = "done";
        states[2] = "done";
        states[3] = "blocked";
      } else if (!validated) {
        // The listing is open; only the user can start Easy Apply.
        states[1] = "current";
      } else {
        states[1] = "done";
        states[2] = "done";
        states[3] = "done";
        states[4] = "done";
        if (!input.hasAnalysis) {
          states[5] = "current";
        } else {
          states[5] = "done";
          const fill = input.fillStatus ?? null;
          if (fill === "running") {
            states[6] = "current";
          } else if (fill === "needs_user_input" || fill === "paused") {
            states[6] = "done";
            states[7] = "current";
          } else if (fill === "waiting_for_confirmation") {
            states[6] = "done";
            states[7] = "current";
          } else if (fill === "failed" || fill === "session_expired") {
            states[6] = "blocked";
          } else if (
            fill === "ready_for_final_review" ||
            fill === "completed" ||
            input.readyForReview
          ) {
            states[6] = "done";
            states[7] = "done";
            states[8] = "current";
          } else {
            states[6] = "current";
          }
        }
      }
    }
  }

  return ORDER.map((id, index) => ({
    id,
    label: labelFor(id),
    state: states[index] ?? "pending",
  }));
}

/** Precise message for the current hand-off state, never a generic failure. */
export function describeRedirect(input: EasyApplyStageInput): RedirectNotice {
  const session = input.session;
  if (!session) {
    return {
      tone: "info",
      title: "No browser session is open",
      detail:
        "Start the live browser to open the job listing. ApplyPilot does not scrape the listing.",
    };
  }
  if (session.browser_status === "expired") {
    return {
      tone: "warning",
      title: "The browser session expired",
      detail:
        "ApplyPilot did not preserve the page. Start a new run; saved answers stay stored.",
    };
  }
  if (session.failure_code === "browser_automation_unavailable") {
    return {
      tone: "warning",
      title: "Live browser automation is unavailable",
      detail:
        session.failure_reason ??
        "Playwright or Chromium is not available on this deployment. Complete the application manually.",
    };
  }
  if (session.failure_code === "navigation_blocked") {
    return {
      tone: "danger",
      title: "The page left the approved site",
      detail:
        session.failure_reason ??
        "ApplyPilot stopped the session because it tried to navigate outside the approved destination.",
    };
  }

  if (input.destination?.authentication_required) {
    return {
      tone: "warning",
      title: "Sign in to Indeed required",
      detail:
        input.destination.message ?
          `${input.destination.message} ApplyPilot never enters passwords, one-time codes or MFA details.`
        : "Complete the Indeed sign-in yourself in the live browser. ApplyPilot never enters passwords, one-time codes or MFA details.",
    };
  }

  const status = redirectStatus(input);
  if (status === "rejected") {
    return {
      tone: "danger",
      title: "Unsafe application destination blocked",
      detail:
        input.destination?.message ??
        "The destination resolved to a private or internal address. Nothing was stored or opened.",
    };
  }
  if (status === "unsupported") {
    return {
      tone: "warning",
      title: "Unsupported application destination",
      detail:
        input.destination?.message ??
        "The page went somewhere ApplyPilot does not support. Continue in the live browser or paste the employer's form URL manually.",
    };
  }
  if (status === "validated") {
    if (input.destination?.manual_destination) {
      return {
        tone: "success",
        title: "Application destination saved",
        detail:
          input.destination.message ??
          "The destination came from your input, not from watching Easy Apply. It was validated and the original listing URL is preserved.",
      };
    }
    const platform = platformLabel(input) ?? "Application platform";
    const step = input.destination?.application_step;
    const stepLabel = step ? ` (${step.replaceAll("_", " ")})` : "";
    return {
      tone: "success",
      title: `${platform} detected${stepLabel}`,
      detail:
        input.destination?.message ??
        "The destination was validated and the original listing URL was preserved. Analyze the form to review its controls.",
    };
  }
  if (status === "awaiting_user_action") {
    return {
      tone: "info",
      title: "Waiting for you to click Easy Apply",
      detail:
        input.destination?.message ??
        "Click Easy Apply yourself in the live browser. ApplyPilot watches the navigation and validates the destination; it never clicks the button for you.",
    };
  }
  return {
    tone: "info",
    title: "Opening the job listing",
    detail:
      "The listing will open in your owned browser session. No application is submitted.",
  };
}

/**
 * True only for a validated Indeed SmartApply destination whose step ApplyPilot
 * cannot drive. Other platforms have no SmartApply step, so they are never
 * blocked here: their validated form is analysable like any other.
 */
export function smartApplyStepUnsupported(input: EasyApplyStageInput): boolean {
  const platform =
    input.destination?.application_platform ?? input.session?.destination_platform;
  if (platform !== "indeed_smartapply") return false;
  return input.destination?.step_supported === false;
}

/** True when the validated destination's form can be analysed. */
export function canAnalyzeDestination(input: EasyApplyStageInput): boolean {
  const validated = Boolean(
    input.destination?.destination_validated ??
      input.session?.destination_validated,
  );
  return validated && !smartApplyStepUnsupported(input);
}

export function redirectStatusLabel(status: RedirectStatus): string {
  switch (status) {
    case "awaiting_user_action":
      return "Waiting for Easy Apply";
    case "validated":
      return "Destination validated";
    case "unsupported":
      return "Unsupported destination";
    case "rejected":
      return "Destination blocked";
    default:
      return "Not started";
  }
}
