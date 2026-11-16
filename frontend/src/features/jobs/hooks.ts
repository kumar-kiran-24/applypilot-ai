"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  analyzeJob,
  bulkMatchJob,
  compareResumes,
  dismissJob,
  discoverJobs,
  fetchJob,
  fetchJobIntelligence,
  fetchJobMatches,
  fetchJobs,
  fetchMatchingSummary,
  fetchResumeRecommendation,
  fetchSavedJobs,
  importJob,
  jobKeys,
  matchJob,
  restoreJob,
  setJobSaved,
} from "@/features/jobs/api";
import type {
  BulkMatchRequest,
  BulkMatchResponse,
  DiscoverJobsResponse,
  ImportJobResponse,
  Job,
  JobIntelligenceResponse,
  JobListQuery,
  JobListResponse,
  JobMatchesResponse,
  MatchingSummary,
  ResumeComparisonResponse,
} from "@/types/api";

/**
 * Imports a pasted Glassdoor listing. A duplicate returns the same job id with
 * `reused: true`, so navigation cannot fork the job library.
 */
export function useImportJob() {
  const queryClient = useQueryClient();
  return useMutation<ImportJobResponse, Error, string>({
    mutationFn: (url) => importJob(url),
    onSuccess: async (data) => {
      queryClient.setQueryData(jobKeys.detail(data.job.id), data.job);
      await queryClient.invalidateQueries({ queryKey: jobKeys.all });
      await queryClient.invalidateQueries({ queryKey: jobKeys.summary });
    },
  });
}

export function useJobs(query: JobListQuery = {}) {
  return useQuery<JobListResponse>({
    queryKey: jobKeys.list(query),
    queryFn: () => fetchJobs(query),
    staleTime: 30_000,
  });
}

export function useSavedJobs({ limit = 50, offset = 0 } = {}) {
  return useQuery<JobListResponse>({
    queryKey: jobKeys.saved(limit, offset),
    queryFn: () => fetchSavedJobs(limit, offset),
    staleTime: 30_000,
  });
}

/** One owned posting. `enabled` lets a route wait for a real id. */
export function useJob(jobId: string, { enabled = true } = {}) {
  return useQuery<Job>({
    queryKey: jobKeys.detail(jobId),
    queryFn: () => fetchJob(jobId),
    enabled: enabled && Boolean(jobId),
    retry: false,
  });
}

/**
 * Saving, dismissing and restoring all change which list a posting appears in,
 * so every cached job query is invalidated after one succeeds.
 */
function useJobStateMutation(
  mutationFn: (jobId: string) => Promise<Job>,
) {
  const queryClient = useQueryClient();
  return useMutation<Job, Error, string>({
    mutationFn,
    onSuccess: async (job) => {
      queryClient.setQueryData(jobKeys.detail(job.id), job);
      await queryClient.invalidateQueries({ queryKey: jobKeys.all });
    },
  });
}

export function useSaveJob() {
  return useJobStateMutation((jobId) => setJobSaved(jobId, true));
}

export function useUnsaveJob() {
  return useJobStateMutation((jobId) => setJobSaved(jobId, false));
}

export function useDismissJob() {
  return useJobStateMutation(dismissJob);
}

export function useRestoreJob() {
  return useJobStateMutation(restoreJob);
}

/**
 * Discovery stores the extracted postings so matching has stable job ids. A
 * 404/409 means Gmail is not connected or the grant is dead; callers surface
 * the reconnect action.
 */
export function useDiscoverJobs() {
  const queryClient = useQueryClient();
  return useMutation<DiscoverJobsResponse, Error, { days: number; maxMessages: number }>({
    mutationFn: ({ days, maxMessages }) => discoverJobs(days, maxMessages),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: jobKeys.all });
      await queryClient.invalidateQueries({ queryKey: jobKeys.summary });
    },
  });
}

export function useJobMatches(jobId: string, { enabled = true } = {}) {
  return useQuery<JobMatchesResponse>({
    queryKey: jobKeys.matches(jobId),
    queryFn: () => fetchJobMatches(jobId),
    enabled,
    retry: false,
  });
}

export function useMatchJob(jobId: string) {
  const queryClient = useQueryClient();
  return useMutation<JobMatchesResponse, Error, { force?: boolean } | void>({
    mutationFn: (variables) => matchJob(jobId, variables?.force ?? false),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: jobKeys.matches(jobId) });
      await queryClient.invalidateQueries({ queryKey: jobKeys.all });
      await queryClient.invalidateQueries({ queryKey: jobKeys.summary });
    },
  });
}

export function useBulkMatchJobs() {
  const queryClient = useQueryClient();
  return useMutation<BulkMatchResponse, Error, BulkMatchRequest>({
    mutationFn: (request) => bulkMatchJob(request),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: jobKeys.all });
      await queryClient.invalidateQueries({
        queryKey: ["jobs", "matches"],
      });
      await queryClient.invalidateQueries({ queryKey: jobKeys.summary });
    },
  });
}

export function useJobIntelligence(jobId: string, { enabled = true } = {}) {
  return useQuery<JobIntelligenceResponse>({
    queryKey: jobKeys.intelligence(jobId),
    queryFn: () => fetchJobIntelligence(jobId),
    enabled,
    retry: false,
  });
}

/**
 * Persisting an analysis changes what matching and comparison can use, so the
 * derived caches are invalidated rather than left showing stale evidence.
 */
export function useAnalyzeJob(jobId: string) {
  const queryClient = useQueryClient();
  return useMutation<
    JobIntelligenceResponse,
    Error,
    { force?: boolean; useLlm?: boolean } | void
  >({
    mutationFn: (variables) =>
      analyzeJob(jobId, {
        force: variables?.force ?? false,
        useLlm: variables?.useLlm ?? true,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: jobKeys.intelligence(jobId),
      });
      await queryClient.invalidateQueries({ queryKey: jobKeys.matches(jobId) });
      await queryClient.invalidateQueries({
        queryKey: jobKeys.comparison(jobId),
      });
      await queryClient.invalidateQueries({
        queryKey: jobKeys.recommendation(jobId),
      });
      await queryClient.invalidateQueries({ queryKey: jobKeys.all });
      await queryClient.invalidateQueries({ queryKey: jobKeys.summary });
    },
  });
}

/** Reads the stored recommendation; `not_computed` means compare first. */
export function useResumeRecommendation(
  jobId: string,
  { enabled = true } = {},
) {
  return useQuery<ResumeComparisonResponse>({
    queryKey: jobKeys.recommendation(jobId),
    queryFn: () => fetchResumeRecommendation(jobId),
    enabled,
    retry: false,
  });
}

/** Runs the backend comparison; ranking and recommendation stay server-side. */
export function useCompareResumes(jobId: string) {
  const queryClient = useQueryClient();
  return useMutation<ResumeComparisonResponse, Error, { force?: boolean } | void>({
    mutationFn: (variables) => compareResumes(jobId, variables?.force ?? false),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: jobKeys.recommendation(jobId),
      });
      await queryClient.invalidateQueries({ queryKey: jobKeys.matches(jobId) });
      await queryClient.invalidateQueries({ queryKey: jobKeys.all });
      await queryClient.invalidateQueries({ queryKey: jobKeys.summary });
    },
  });
}

export function useMatchingSummary() {
  return useQuery<MatchingSummary>({
    queryKey: jobKeys.summary,
    queryFn: fetchMatchingSummary,
    staleTime: 30_000,
  });
}
