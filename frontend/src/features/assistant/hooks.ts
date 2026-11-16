"use client";

import { useMutation } from "@tanstack/react-query";

import { draftAnswer, type DraftAnswerInput } from "@/features/assistant/api";
import type { AnswerDraft } from "@/types/api";

export function useDraftAnswer() {
  return useMutation<AnswerDraft, Error, DraftAnswerInput>({
    mutationFn: draftAnswer,
  });
}
