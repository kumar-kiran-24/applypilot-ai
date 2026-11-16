import { apiFetch } from "@/lib/api/client";
import type {
  AnswerCategory,
  AnswerCategoryOverview,
  AnswerTrustLevel,
  CandidateKnowledge,
  ReusableAnswerSuggestions,
  SavedAnswer,
  SavedAnswerDetail,
} from "@/types/api";

export const answerKeys = {
  all: ["answers"] as const,
  list: (filters: SavedAnswerFilters) => ["answers", "list", filters] as const,
  categories: ["answers", "categories"] as const,
  knowledge: ["answers", "knowledge"] as const,
  detail: (key: string) => ["answers", "detail", key] as const,
};

export interface SavedAnswerFilters {
  q?: string;
  category?: AnswerCategory | "";
  trust?: AnswerTrustLevel | "";
}

/** Saved answers with the backend-derived category, trust and freshness. */
export function fetchSavedAnswers(
  filters: SavedAnswerFilters = {},
): Promise<SavedAnswer[]> {
  const params = new URLSearchParams();
  if (filters.q?.trim()) params.set("q", filters.q.trim());
  if (filters.category) params.set("category", filters.category);
  if (filters.trust) params.set("trust", filters.trust);
  const query = params.toString();
  return apiFetch<SavedAnswer[]>(
    `profile/answers${query ? `?${query}` : ""}`,
  );
}

export function fetchAnswerCategories(): Promise<AnswerCategoryOverview> {
  return apiFetch<AnswerCategoryOverview>("profile/answers/categories");
}

export function fetchAnswerKnowledge(): Promise<CandidateKnowledge> {
  return apiFetch<CandidateKnowledge>("profile/answers/knowledge");
}

export function fetchAnswerDetail(key: string): Promise<SavedAnswerDetail> {
  return apiFetch<SavedAnswerDetail>(`profile/answers/${key}`);
}

export interface AnswerResolutionInput {
  resolution: "keep_stored" | "use_profile" | "edited";
  answer?: string;
}

/** Re-confirm, adopt the profile value, or store a corrected answer. */
export function resolveSavedAnswer(
  key: string,
  input: AnswerResolutionInput,
): Promise<SavedAnswer> {
  return apiFetch<SavedAnswer>(`profile/answers/${key}/resolve`, {
    method: "POST",
    body: input,
  });
}

/** Saved answers that relate to one question in an application. */
export function fetchReusableAnswerSuggestions(
  applicationId: string,
  questionId: string,
): Promise<ReusableAnswerSuggestions> {
  return apiFetch<ReusableAnswerSuggestions>(
    `applications/${applicationId}/questions/${questionId}/reusable-answers`,
  );
}
