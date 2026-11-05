"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  bulkMatchJob,
  discoverJobs,
  fetchJobMatches,
  fetchJobs,
  fetchMatchingSummary,
  jobKeys,
  matchJob,
} from "@/features/jobs/api";
import type {
  BulkMatchRequest,
  BulkMatchResponse,
  DiscoverJobsResponse,
  JobMatchesResponse,
  JobListResponse,
  MatchingSummary,
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

export function useMatchingSummary() {
  return useQuery<MatchingSummary>({
    queryKey: jobKeys.summary,
    queryFn: fetchMatchingSummary,
    staleTime: 30_000,
  });
}
