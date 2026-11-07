import { apiFetch } from "@/lib/api/client";
import type {
  ConflictResolution,
  MissingInformationDetail,
  MissingInformationQueueResponse,
} from "@/types/api";

export const missingInformationKeys = {
  all: ["missing-information"] as const,
  list: ["missing-information", "list"] as const,
  detail: (applicationId: string) =>
    ["missing-information", "detail", applicationId] as const,
};

export function fetchMissingInformationQueue(): Promise<MissingInformationQueueResponse> {
  return apiFetch<MissingInformationQueueResponse>("missing-information");
}

export function fetchMissingInformation(
  applicationId: string,
): Promise<MissingInformationDetail> {
  return apiFetch<MissingInformationDetail>(
    `missing-information/${applicationId}`,
  );
}

export function refreshMissingInformation(
  applicationId: string,
): Promise<MissingInformationDetail> {
  return apiFetch<MissingInformationDetail>(
    `missing-information/${applicationId}/refresh`,
    { method: "POST" },
  );
}

export function submitMissingInformationAnswer(
  applicationId: string,
  questionKey: string,
  input: { answer: string; save_for_reuse: boolean },
): Promise<MissingInformationDetail> {
  return apiFetch<MissingInformationDetail>(
    `missing-information/${applicationId}/questions/${questionKey}/answer`,
    { method: "POST", body: input },
  );
}

export function confirmMissingInformationSuggestion(
  applicationId: string,
  questionKey: string,
  input: { answer: string | null; save_for_reuse: boolean },
): Promise<MissingInformationDetail> {
  return apiFetch<MissingInformationDetail>(
    `missing-information/${applicationId}/questions/${questionKey}/confirm`,
    { method: "POST", body: input },
  );
}

export function dismissMissingInformationQuestion(
  applicationId: string,
  questionKey: string,
): Promise<MissingInformationDetail> {
  return apiFetch<MissingInformationDetail>(
    `missing-information/${applicationId}/questions/${questionKey}/dismiss`,
    { method: "POST" },
  );
}

export function resolveMissingInformationConflict(
  applicationId: string,
  questionKey: string,
  input: { resolution: ConflictResolution; answer?: string | null },
): Promise<MissingInformationDetail> {
  return apiFetch<MissingInformationDetail>(
    `missing-information/${applicationId}/conflicts/${questionKey}/resolve`,
    { method: "POST", body: input },
  );
}
