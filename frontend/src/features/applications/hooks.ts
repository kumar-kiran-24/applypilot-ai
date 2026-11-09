"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  analyzeApplication,
  analyzeApplicationForm,
  applicationKeys,
  approveApplication,
  browserSessionKeys,
  cancelBrowserSession,
  cancelFieldFill,
  cancelIntervention,
  createBrowserViewTicket,
  fetchBrowserCapabilities,
  fetchBrowserSession,
  confirmManualSubmission,
  fetchApplication,
  fetchApplications,
  fetchFillExecution,
  fetchFillPlan,
  fetchFinalReview,
  fetchInterventions,
  fetchPlatforms,
  fillKeys,
  humanLoopKeys,
  inspectApplication,
  markInterventionWorking,
  pauseBrowserSession,
  pauseFieldFill,
  reanalyzeHumanLoop,
  resumeBrowserSession,
  resolveIntervention,
  resumeFieldFill,
  resumeHumanLoop,
  selectApplicationResume,
  startBrowserSession,
  startFieldFill,
  submitApplication,
  updateApplicationQuestion,
  updateApplicationStage,
} from "@/features/applications/api";
import { resumeKeys } from "@/features/resumes/api";
import type {
  ApplicationListResponse,
  ApplicationPreview,
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

export function useFillPlan(id: string, enabled = true) {
  return useQuery<FillPlanResponse>({
    queryKey: fillKeys.plan(id),
    queryFn: () => fetchFillPlan(id),
    enabled,
    retry: false,
    staleTime: 0,
  });
}

export function useFillExecution(id: string, enabled = true) {
  return useQuery<FillExecutionResponse>({
    queryKey: fillKeys.execution(id),
    queryFn: () => fetchFillExecution(id),
    enabled,
    retry: false,
  });
}

export function useInterventions(id: string) {
  return useQuery<InterventionListResponse>({
    queryKey: humanLoopKeys.interventions(id),
    queryFn: () => fetchInterventions(id),
    retry: false,
  });
}

export function useFinalReview(id: string) {
  return useQuery<FinalReviewResponse>({
    queryKey: humanLoopKeys.finalReview(id),
    queryFn: () => fetchFinalReview(id),
    retry: false,
  });
}

function useHumanLoopMutation<TData, TVariables>(
  id: string,
  mutationFn: (variables: TVariables) => Promise<TData>,
) {
  const queryClient = useQueryClient();
  return useMutation<TData, Error, TVariables>({
    mutationFn,
    onSuccess: async (data) => {
      if (
        typeof data === "object" &&
        data !== null &&
        "execution_id" in data &&
        "run_number" in data
      ) {
        queryClient.setQueryData(
          fillKeys.execution(id),
          data as unknown as FillExecutionResponse,
        );
      }
      await queryClient.invalidateQueries({ queryKey: humanLoopKeys.all });
      await queryClient.invalidateQueries({ queryKey: fillKeys.all });
      await queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    },
  });
}

export function useMarkInterventionWorking(id: string) {
  return useHumanLoopMutation<Intervention, string>(id, (interventionId) =>
    markInterventionWorking(id, interventionId),
  );
}

export function useResolveIntervention(id: string) {
  return useHumanLoopMutation<Intervention, string>(id, (interventionId) =>
    resolveIntervention(id, interventionId),
  );
}

export function useCancelIntervention(id: string) {
  return useHumanLoopMutation<Intervention, string>(id, (interventionId) =>
    cancelIntervention(id, interventionId),
  );
}

export function useReanalyzeHumanLoop(id: string) {
  return useHumanLoopMutation<HumanLoopReconcileResponse, void>(id, () =>
    reanalyzeHumanLoop(id),
  );
}

export function useResumeHumanLoop(id: string) {
  return useHumanLoopMutation<FillExecutionResponse, void>(id, () =>
    resumeHumanLoop(id),
  );
}

export function useBrowserSession(id: string) {
  return useQuery<BrowserSession>({
    queryKey: browserSessionKeys.detail(id),
    queryFn: () => fetchBrowserSession(id),
    retry: false,
    staleTime: 5_000,
  });
}

export function useBrowserCapabilities() {
  return useQuery<BrowserSessionCapabilitiesResponse>({
    queryKey: browserSessionKeys.capabilities,
    queryFn: fetchBrowserCapabilities,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useStartBrowserSession(id: string) {
  const queryClient = useQueryClient();
  return useMutation<BrowserSession, Error, void>({
    mutationFn: () => startBrowserSession(id),
    onSuccess: async (data) => {
      queryClient.setQueryData(browserSessionKeys.detail(id), data);
      await queryClient.invalidateQueries({ queryKey: fillKeys.all });
    },
  });
}

export function useCancelBrowserSession(id: string) {
  const queryClient = useQueryClient();
  return useMutation<BrowserSession, Error, void>({
    mutationFn: () => cancelBrowserSession(id),
    onSuccess: async (data) => {
      queryClient.setQueryData(browserSessionKeys.detail(id), data);
      await queryClient.invalidateQueries({ queryKey: fillKeys.all });
      await queryClient.invalidateQueries({ queryKey: humanLoopKeys.all });
      await queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    },
  });
}

export function usePauseBrowserSession(id: string) {
  const queryClient = useQueryClient();
  return useMutation<BrowserSession, Error, void>({
    mutationFn: () => pauseBrowserSession(id),
    onSuccess: async (data) => {
      queryClient.setQueryData(browserSessionKeys.detail(id), data);
    },
  });
}

export function useResumeBrowserSession(id: string) {
  const queryClient = useQueryClient();
  return useMutation<BrowserSession, Error, void>({
    mutationFn: () => resumeBrowserSession(id),
    onSuccess: async (data) => {
      queryClient.setQueryData(browserSessionKeys.detail(id), data);
    },
  });
}

export function useBrowserViewTicket(id: string) {
  return useMutation<BrowserViewTicketResponse, Error, void>({
    mutationFn: () => createBrowserViewTicket(id),
  });
}

export function useAnalyzeLiveForm(id: string) {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, void>({
    mutationFn: () => analyzeApplicationForm(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: fillKeys.all });
    },
  });
}

function useFillMutation<TVariables>(
  id: string,
  mutationFn: (variables: TVariables) => Promise<FillExecutionResponse>,
) {
  const queryClient = useQueryClient();
  return useMutation<FillExecutionResponse, Error, TVariables>({
    mutationFn,
    onSuccess: async (data) => {
      queryClient.setQueryData(fillKeys.execution(id), data);
      await queryClient.invalidateQueries({ queryKey: fillKeys.plan(id) });
      // Start/pause/resume/cancel change what needs human attention, so the
      // intervention queue must be re-read rather than left stale.
      await queryClient.invalidateQueries({ queryKey: humanLoopKeys.all });
      await queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    },
  });
}

export function useStartFieldFill(id: string) {
  return useFillMutation<boolean>(id, (restart) => startFieldFill(id, restart));
}

export function usePauseFieldFill(id: string) {
  return useFillMutation<void>(id, () => pauseFieldFill(id, "Paused by you."));
}

export function useResumeFieldFill(id: string) {
  return useFillMutation<void>(id, () => resumeFieldFill(id));
}

export function useCancelFieldFill(id: string) {
  return useFillMutation<void>(id, () => cancelFieldFill(id));
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
