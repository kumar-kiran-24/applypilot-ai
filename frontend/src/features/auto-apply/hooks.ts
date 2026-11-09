"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { applicationKeys } from "@/features/applications/api";
import {
  approveEligibility,
  autoApplyKeys,
  evaluateEligibility,
  fetchAutoApplySettings,
  fetchEligibilityList,
  fetchEligibilityReport,
  updateAutoApplySettings,
  withdrawEligibilityApproval,
} from "@/features/auto-apply/api";
import type {
  AutoApplySettings,
  AutoApplySettingsInput,
  EligibilityListResponse,
  EligibilityReport,
} from "@/types/api";

async function invalidateAutoApply(
  queryClient: ReturnType<typeof useQueryClient>,
  applicationId?: string,
) {
  await queryClient.invalidateQueries({ queryKey: autoApplyKeys.all });
  await queryClient.invalidateQueries({ queryKey: applicationKeys.all });
  if (applicationId) {
    await queryClient.invalidateQueries({
      queryKey: autoApplyKeys.report(applicationId),
    });
    await queryClient.invalidateQueries({
      queryKey: applicationKeys.detail(applicationId),
    });
  }
}

export function useAutoApplySettings() {
  return useQuery<AutoApplySettings>({
    queryKey: autoApplyKeys.settings,
    queryFn: fetchAutoApplySettings,
    staleTime: 30_000,
  });
}

export function useUpdateAutoApplySettings() {
  const queryClient = useQueryClient();
  return useMutation<AutoApplySettings, Error, AutoApplySettingsInput>({
    mutationFn: updateAutoApplySettings,
    onSuccess: async () => {
      await invalidateAutoApply(queryClient);
    },
  });
}

export function useEligibilityList() {
  return useQuery<EligibilityListResponse>({
    queryKey: autoApplyKeys.eligibility,
    queryFn: fetchEligibilityList,
    staleTime: 15_000,
  });
}

export function useEligibilityReport(applicationId: string) {
  return useQuery<EligibilityReport>({
    queryKey: autoApplyKeys.report(applicationId),
    queryFn: () => fetchEligibilityReport(applicationId),
    retry: false,
  });
}

export function useEvaluateEligibility(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<EligibilityReport, Error, void>({
    mutationFn: () => evaluateEligibility(applicationId),
    onSuccess: async () => {
      await invalidateAutoApply(queryClient, applicationId);
    },
  });
}

export function useApproveEligibility(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<EligibilityReport, Error, string>({
    mutationFn: (reportVersion) =>
      approveEligibility(applicationId, reportVersion),
    onSuccess: async () => {
      await invalidateAutoApply(queryClient, applicationId);
    },
  });
}

export function useWithdrawEligibilityApproval(applicationId: string) {
  const queryClient = useQueryClient();
  return useMutation<EligibilityReport, Error, string>({
    mutationFn: (reportVersion) =>
      withdrawEligibilityApproval(applicationId, reportVersion),
    onSuccess: async () => {
      await invalidateAutoApply(queryClient, applicationId);
    },
  });
}
