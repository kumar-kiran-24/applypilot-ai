"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  analyzeJob,
  bulkMatchJob,
  compareResumes,
  discoverJobs,
  fetchJobIntelligence,
  fetchJobMatches,
  fetchJobs,
  fetchMatchingSummary,
  fetchResumeRecommendation,
  jobKeys,
  matchJob,
} from "@/features/jobs/api";
import type {
  BulkMatchRequest,
  BulkMatchResponse,
  DiscoverJobsResponse,
  JobIntelligenceResponse,
  JobMatchesResponse,
  JobListResponse,
  MatchingSummary,
  ResumeComparisonResponse,
} from "@/types/api";

export function useJobs({ limit = 50, offset = 0 } = {}) {
  return useQuery<JobListResponse>({
    queryKey: jobKeys.list(limit, offset),
    queryFn: () => fetchJobs(limit, offset),
    staleTime: 30_000,
  });
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
