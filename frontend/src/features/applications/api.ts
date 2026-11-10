import { apiFetch } from "@/lib/api/client";
import type {
  ApplicationListResponse,
  ApplicationPreview,
  ApplicationWorkflow,
  ApplicationStage,
  BrowserSession,
  BrowserSessionCapabilitiesResponse,
  BrowserViewTicketResponse,
  FillExecutionResponse,
  FillPlanResponse,
  FinalReviewResponse,
  HumanLoopReconcileResponse,
  Intervention,
  InterventionListResponse,
  PlatformCatalogResponse,
  SubmissionResponse,
} from "@/types/api";

export const applicationKeys = {
  all: ["applications"] as const,
  list: ["applications", "list"] as const,
  detail: (id: string) => ["applications", "detail", id] as const,
  workflow: (id: string) => ["applications", "workflow", id] as const,
  platforms: ["applications", "platforms"] as const,
};

export const fillKeys = {
  all: ["application-fill"] as const,
  plan: (id: string) => ["application-fill", "plan", id] as const,
  execution: (id: string) => ["application-fill", "execution", id] as const,
};

export const browserSessionKeys = {
  all: ["browser-session"] as const,
  detail: (id: string) => ["browser-session", "detail", id] as const,
  capabilities: ["browser-session", "capabilities"] as const,
};

export const humanLoopKeys = {
  all: ["application-human-loop"] as const,
  interventions: (id: string) =>
    ["application-human-loop", "interventions", id] as const,
  finalReview: (id: string) =>
    ["application-human-loop", "final-review", id] as const,
};

export function fetchApplications(): Promise<ApplicationListResponse> {
  return apiFetch<ApplicationListResponse>("applications");
}

export function fetchApplication(id: string): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}`);
}

export function fetchApplicationWorkflow(
  id: string,
): Promise<ApplicationWorkflow> {
  return apiFetch<ApplicationWorkflow>(`applications/${id}/workflow`);
}

export function fetchPlatforms(): Promise<PlatformCatalogResponse> {
  return apiFetch<PlatformCatalogResponse>("applications/platforms");
}

/** Platform detection and the first questionnaire can take a few seconds. */
export function analyzeApplication(input: {
  job_id: string;
  application_url?: string;
  resume_id?: string;
}): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>("applications/analyze", {
    method: "POST",
    body: input,
    timeoutMs: 90_000,
  });
}

export function inspectApplication(
  id: string,
  input: { applicationUrl: string; employerCareersUrl?: string | null },
): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}/inspect`, {
    method: "POST",
    body: {
      application_url: input.applicationUrl,
      employer_careers_url: input.employerCareersUrl ?? null,
    },
    timeoutMs: 90_000,
  });
}

export function updateApplicationQuestion(
  id: string,
  questionId: string,
  input: { answer: string | null; confirmed: boolean; note?: string | null },
): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(
    `applications/${id}/questions/${questionId}`,
    { method: "PATCH", body: input },
  );
}

export function selectApplicationResume(
  id: string,
  resumeId: string,
): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}/resume`, {
    method: "PATCH",
    body: { resume_id: resumeId },
  });
}

export function approveApplication(
  id: string,
  approved: boolean,
): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}/approve`, {
    method: "POST",
    body: { approved },
  });
}

export function submitApplication(id: string): Promise<SubmissionResponse> {
  return apiFetch<SubmissionResponse>(`applications/${id}/submit`, {
    method: "POST",
    timeoutMs: 120_000,
  });
}

export function confirmManualSubmission(
  id: string,
  reference: string | null,
): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}/mark-submitted`, {
    method: "POST",
    // The backend refuses to record a submission without this explicit flag.
    body: { reference, confirmed: true },
  });
}

export function startBrowserSession(id: string): Promise<BrowserSession> {
  return apiFetch<BrowserSession>("browser-sessions", {
    method: "POST",
    body: { application_id: id },
    timeoutMs: 90_000,
  });
}

export function fetchBrowserSession(id: string): Promise<BrowserSession> {
  return apiFetch<BrowserSession>(
    `browser-sessions/by-application/${id}`,
  );
}

export function fetchBrowserCapabilities(): Promise<BrowserSessionCapabilitiesResponse> {
  return apiFetch<BrowserSessionCapabilitiesResponse>(
    "browser-sessions/capabilities",
  );
}

export function createBrowserViewTicket(
  id: string,
): Promise<BrowserViewTicketResponse> {
  return apiFetch<BrowserViewTicketResponse>(
    `browser-sessions/by-application/${id}/view-ticket`,
    { method: "POST" },
  );
}

export function pauseBrowserSession(id: string): Promise<BrowserSession> {
  return apiFetch<BrowserSession>(
    `browser-sessions/by-application/${id}/pause`,
    { method: "POST", body: { reason: "Paused from the live browser workspace." } },
  );
}

export function resumeBrowserSession(id: string): Promise<BrowserSession> {
  return apiFetch<BrowserSession>(
    `browser-sessions/by-application/${id}/resume`,
    { method: "POST" },
  );
}

export function cancelBrowserSession(id: string): Promise<BrowserSession> {
  return apiFetch<BrowserSession>(
    `browser-sessions/by-application/${id}/cancel`,
    { method: "POST" },
  );
}

export function analyzeApplicationForm(id: string): Promise<unknown> {
  return apiFetch<unknown>(
    `browser-sessions/by-application/${id}/form-analysis`,
    { method: "POST", timeoutMs: 90_000 },
  );
}

export function fetchFillPlan(id: string): Promise<FillPlanResponse> {
  return apiFetch<FillPlanResponse>(
    `browser-sessions/by-application/${id}/fill-plan`,
  );
}

export function fetchFillExecution(id: string): Promise<FillExecutionResponse> {
  return apiFetch<FillExecutionResponse>(
    `browser-sessions/by-application/${id}/fill`,
  );
}

export function startFieldFill(
  id: string,
  restart = false,
): Promise<FillExecutionResponse> {
  return apiFetch<FillExecutionResponse>(
    `browser-sessions/by-application/${id}/fill`,
    { method: "POST", body: { restart }, timeoutMs: 120_000 },
  );
}

export function pauseFieldFill(
  id: string,
  reason?: string,
): Promise<FillExecutionResponse> {
  return apiFetch<FillExecutionResponse>(
    `browser-sessions/by-application/${id}/fill/pause`,
    { method: "POST", body: { reason: reason ?? null } },
  );
}

export function resumeFieldFill(id: string): Promise<FillExecutionResponse> {
  return apiFetch<FillExecutionResponse>(
    `browser-sessions/by-application/${id}/fill/resume`,
    { method: "POST", timeoutMs: 120_000 },
  );
}

export function cancelFieldFill(id: string): Promise<FillExecutionResponse> {
  return apiFetch<FillExecutionResponse>(
    `browser-sessions/by-application/${id}/fill/cancel`,
    { method: "POST" },
  );
}

export function fetchInterventions(
  id: string,
): Promise<InterventionListResponse> {
  return apiFetch<InterventionListResponse>(
    `browser-sessions/by-application/${id}/interventions`,
  );
}

export function markInterventionWorking(
  applicationId: string,
  interventionId: string,
): Promise<Intervention> {
  return apiFetch<Intervention>(
    `browser-sessions/by-application/${applicationId}/interventions/${interventionId}/working`,
    { method: "POST" },
  );
}

export function resolveIntervention(
  applicationId: string,
  interventionId: string,
): Promise<Intervention> {
  return apiFetch<Intervention>(
    `browser-sessions/by-application/${applicationId}/interventions/${interventionId}/resolve`,
    { method: "POST", timeoutMs: 120_000 },
  );
}

export function cancelIntervention(
  applicationId: string,
  interventionId: string,
): Promise<Intervention> {
  return apiFetch<Intervention>(
    `browser-sessions/by-application/${applicationId}/interventions/${interventionId}/cancel`,
    { method: "POST" },
  );
}

export function reanalyzeHumanLoop(
  applicationId: string,
): Promise<HumanLoopReconcileResponse> {
  return apiFetch<HumanLoopReconcileResponse>(
    `browser-sessions/by-application/${applicationId}/human-loop/reanalyze`,
    { method: "POST", timeoutMs: 120_000 },
  );
}

export function resumeHumanLoop(
  applicationId: string,
): Promise<FillExecutionResponse> {
  return apiFetch<FillExecutionResponse>(
    `browser-sessions/by-application/${applicationId}/human-loop/resume`,
    { method: "POST", timeoutMs: 120_000 },
  );
}

export function fetchFinalReview(
  applicationId: string,
): Promise<FinalReviewResponse> {
  return apiFetch<FinalReviewResponse>(
    `browser-sessions/by-application/${applicationId}/final-review`,
  );
}

export function updateApplicationStage(
  id: string,
  stage: ApplicationStage,
): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}/stage`, {
    method: "PATCH",
    body: { stage },
  });
}
