"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { applicationKeys } from "@/features/applications/api";
import {
  confirmMissingInformationSuggestion,
  dismissMissingInformationQuestion,
  fetchMissingInformation,
  fetchMissingInformationQueue,
  missingInformationKeys,
  refreshMissingInformation,
  resolveMissingInformationConflict,
  submitMissingInformationAnswer,
} from "@/features/missing-information/api";
import { profileKeys } from "@/features/profile/api";
import type {
  ConflictResolution,
  MissingInformationDetail,
  MissingInformationQueueResponse,
} from "@/types/api";

async function invalidateQueue(
  queryClient: ReturnType<typeof useQueryClient>,
  applicationId: string,
) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: missingInformationKeys.all }),
    queryClient.invalidateQueries({ queryKey: applicationKeys.all }),
    queryClient.invalidateQueries({ queryKey: profileKeys.answers }),
  ]);
  await queryClient.invalidateQueries({
    queryKey: missingInformationKeys.detail(applicationId),
  });
}

export function useMissingInformationQueue() {
  return useQuery<MissingInformationQueueResponse>({
    queryKey: missingInformationKeys.list,
    queryFn: fetchMissingInformationQueue,
    staleTime: 15_000,
  });
}

export function useMissingInformation(applicationId: string) {
  return useQuery<MissingInformationDetail>({
    queryKey: missingInformationKeys.detail(applicationId),
    queryFn: () => fetchMissingInformation(applicationId),
    retry: false,
  });
}

export function useRefreshMissingInformation(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<MissingInformationDetail, Error, void>({
    mutationFn: () => refreshMissingInformation(applicationId),
    onSuccess: async () => {
      await invalidateQueue(queryClient, applicationId);
    },
  });
}

export function useSubmitMissingAnswer(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<
    MissingInformationDetail,
    Error,
    { questionKey: string; answer: string; saveForReuse: boolean }
  >({
    mutationFn: ({ questionKey, answer, saveForReuse }) =>
      submitMissingInformationAnswer(applicationId, questionKey, {
        answer,
        save_for_reuse: saveForReuse,
      }),
    onSuccess: async () => {
      await invalidateQueue(queryClient, applicationId);
    },
  });
}

export function useConfirmMissingSuggestion(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<
    MissingInformationDetail,
    Error,
    { questionKey: string; answer: string | null; saveForReuse: boolean }
  >({
    mutationFn: ({ questionKey, answer, saveForReuse }) =>
      confirmMissingInformationSuggestion(applicationId, questionKey, {
        answer,
        save_for_reuse: saveForReuse,
      }),
    onSuccess: async () => {
      await invalidateQueue(queryClient, applicationId);
    },
  });
}

export function useDismissMissingQuestion(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<MissingInformationDetail, Error, string>({
    mutationFn: (questionKey) =>
      dismissMissingInformationQuestion(applicationId, questionKey),
    onSuccess: async () => {
      await invalidateQueue(queryClient, applicationId);
    },
  });
}

export function useResolveMissingConflict(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<
    MissingInformationDetail,
    Error,
    {
      questionKey: string;
      resolution: ConflictResolution;
      answer?: string | null;
    }
  >({
    mutationFn: ({ questionKey, resolution, answer }) =>
      resolveMissingInformationConflict(applicationId, questionKey, {
        resolution,
        answer,
      }),
    onSuccess: async () => {
      await invalidateQueue(queryClient, applicationId);
    },
  });
}
