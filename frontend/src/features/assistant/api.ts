import { apiFetch } from "@/lib/api/client";
import type { AnswerDraft, AnswerType } from "@/types/api";

export const assistantKeys = {
  all: ["assistant"] as const,
  draft: ["assistant", "draft"] as const,
};

export interface DraftAnswerInput {
  application_id?: string;
  job_id?: string;
  question_id?: string;
  question_key?: string;
  question_text?: string;
  answer_type?: AnswerType;
  allow_llm?: boolean;
}

/** Evidence retrieval is deterministic; generation may call the LLM. */
export function draftAnswer(input: DraftAnswerInput): Promise<AnswerDraft> {
  return apiFetch<AnswerDraft>("assistant/answers/draft", {
    method: "POST",
    body: input,
    timeoutMs: 90_000,
  });
}
