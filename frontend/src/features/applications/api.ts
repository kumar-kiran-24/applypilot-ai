import { apiFetch } from "@/lib/api/client";
import type {
  ApplicationListResponse,
  ApplicationPreview,
  ApplicationStage,
  PlatformCatalogResponse,
  SubmissionResponse,
} from "@/types/api";

export const applicationKeys = {
  all: ["applications"] as const,
  list: ["applications", "list"] as const,
  detail: (id: string) => ["applications", "detail", id] as const,
  platforms: ["applications", "platforms"] as const,
};

export function fetchApplications(): Promise<ApplicationListResponse> {
  return apiFetch<ApplicationListResponse>("applications");
}

export function fetchApplication(id: string): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}`);
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
  applicationUrl: string,
): Promise<ApplicationPreview> {
  return apiFetch<ApplicationPreview>(`applications/${id}/inspect`, {
    method: "POST",
    body: { application_url: applicationUrl },
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
    body: { reference },
  });
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
