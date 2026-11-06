"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  analyzeApplication,
  applicationKeys,
  approveApplication,
  confirmManualSubmission,
  fetchApplication,
  fetchApplications,
  fetchPlatforms,
  inspectApplication,
  selectApplicationResume,
  submitApplication,
  updateApplicationQuestion,
  updateApplicationStage,
} from "@/features/applications/api";
import { resumeKeys } from "@/features/resumes/api";
import type {
  ApplicationListResponse,
  ApplicationPreview,
  ApplicationStage,
  PlatformCatalogResponse,
  SubmissionResponse,
} from "@/types/api";

async function invalidateApplications(
  queryClient: ReturnType<typeof useQueryClient>,
  id?: string,
) {
  await queryClient.invalidateQueries({ queryKey: applicationKeys.all });
  if (id) {
    await queryClient.invalidateQueries({ queryKey: applicationKeys.detail(id) });
  }
}

export function useApplications() {
  return useQuery<ApplicationListResponse>({
    queryKey: applicationKeys.list,
    queryFn: fetchApplications,
    staleTime: 15_000,
  });
}

export function useApplication(id: string) {
  return useQuery<ApplicationPreview>({
    queryKey: applicationKeys.detail(id),
    queryFn: () => fetchApplication(id),
    retry: false,
  });
}

export function useApplicationPlatforms() {
  return useQuery<PlatformCatalogResponse>({
    queryKey: applicationKeys.platforms,
    queryFn: fetchPlatforms,
    staleTime: 5 * 60_000,
  });
}

export function useAnalyzeApplication() {
  const queryClient = useQueryClient();
  return useMutation<
    ApplicationPreview,
    Error,
    { job_id: string; application_url?: string; resume_id?: string }
  >({
    mutationFn: analyzeApplication,
    onSuccess: async (data) => {
      await invalidateApplications(queryClient, data.application_id);
    },
  });
}

export function useInspectApplication(id: string) {
  const queryClient = useQueryClient();
  return useMutation<ApplicationPreview, Error, string>({
    mutationFn: (applicationUrl) => inspectApplication(id, applicationUrl),
    onSuccess: async () => {
      await invalidateApplications(queryClient, id);
    },
  });
}

export function useUpdateApplicationQuestion(id: string) {
  const queryClient = useQueryClient();
  return useMutation<
    ApplicationPreview,
    Error,
    { questionId: string; answer: string | null; confirmed: boolean; note?: string | null }
  >({
    mutationFn: ({ questionId, answer, confirmed, note }) =>
      updateApplicationQuestion(id, questionId, { answer, confirmed, note }),
    onSuccess: async () => {
      await invalidateApplications(queryClient, id);
    },
  });
}

export function useSelectApplicationResume(id: string) {
  const queryClient = useQueryClient();
  return useMutation<ApplicationPreview, Error, string>({
    mutationFn: (resumeId) => selectApplicationResume(id, resumeId),
    onSuccess: async () => {
      await invalidateApplications(queryClient, id);
      await queryClient.invalidateQueries({ queryKey: resumeKeys.all });
    },
  });
}

export function useApproveApplication(id: string) {
  const queryClient = useQueryClient();
  return useMutation<ApplicationPreview, Error, boolean>({
    mutationFn: (approved) => approveApplication(id, approved),
    onSuccess: async () => {
      await invalidateApplications(queryClient, id);
    },
  });
}

export function useSubmitApplication(id: string) {
  const queryClient = useQueryClient();
  return useMutation<SubmissionResponse, Error, void>({
    mutationFn: () => submitApplication(id),
    onSuccess: async () => {
      await invalidateApplications(queryClient, id);
    },
  });
}

export function useConfirmManualSubmission(id: string) {
  const queryClient = useQueryClient();
  return useMutation<ApplicationPreview, Error, string | null>({
    mutationFn: (reference) => confirmManualSubmission(id, reference),
    onSuccess: async () => {
      await invalidateApplications(queryClient, id);
    },
  });
}

export function useUpdateApplicationStage(id: string) {
  const queryClient = useQueryClient();
  return useMutation<ApplicationPreview, Error, ApplicationStage>({
    mutationFn: (stage) => updateApplicationStage(id, stage),
    onSuccess: async () => {
      await invalidateApplications(queryClient, id);
    },
  });
}
