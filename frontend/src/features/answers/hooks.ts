"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  answerKeys,
  fetchAnswerCategories,
  fetchAnswerDetail,
  fetchAnswerKnowledge,
  fetchReusableAnswerSuggestions,
  fetchSavedAnswers,
  resolveSavedAnswer,
  type AnswerResolutionInput,
  type SavedAnswerFilters,
} from "@/features/answers/api";
import { profileKeys } from "@/features/profile/api";
import type {
  AnswerCategoryOverview,
  CandidateKnowledge,
  ReusableAnswerSuggestions,
  SavedAnswer,
  SavedAnswerDetail,
} from "@/types/api";

async function invalidateAnswers(
  queryClient: ReturnType<typeof useQueryClient>,
) {
  await queryClient.invalidateQueries({ queryKey: answerKeys.all });
  await queryClient.invalidateQueries({ queryKey: profileKeys.answers });
  await queryClient.invalidateQueries({ queryKey: profileKeys.detail });
}

export function useSavedAnswers(filters: SavedAnswerFilters) {
  return useQuery<SavedAnswer[]>({
    queryKey: answerKeys.list(filters),
    queryFn: () => fetchSavedAnswers(filters),
    retry: false,
  });
}

export function useAnswerCategories() {
  return useQuery<AnswerCategoryOverview>({
    queryKey: answerKeys.categories,
    queryFn: fetchAnswerCategories,
    retry: false,
  });
}

export function useAnswerKnowledge() {
  return useQuery<CandidateKnowledge>({
    queryKey: answerKeys.knowledge,
    queryFn: fetchAnswerKnowledge,
    retry: false,
  });
}

export function useAnswerDetail(key: string | null, enabled = true) {
  return useQuery<SavedAnswerDetail>({
    queryKey: answerKeys.detail(key ?? ""),
    queryFn: () => fetchAnswerDetail(key as string),
    enabled: Boolean(key) && enabled,
    retry: false,
  });
}

export function useResolveSavedAnswer() {
  const queryClient = useQueryClient();
  return useMutation<
    SavedAnswer,
    Error,
    { key: string; input: AnswerResolutionInput }
  >({
    mutationFn: ({ key, input }) => resolveSavedAnswer(key, input),
    onSuccess: async () => {
      await invalidateAnswers(queryClient);
    },
  });
}

export function useReusableAnswerSuggestions() {
  return useMutation<
    ReusableAnswerSuggestions,
    Error,
    { applicationId: string; questionId: string }
  >({
    mutationFn: ({ applicationId, questionId }) =>
      fetchReusableAnswerSuggestions(applicationId, questionId),
  });
}
