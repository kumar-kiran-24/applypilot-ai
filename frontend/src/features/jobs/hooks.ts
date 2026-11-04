"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchGlassdoorJobs, gmailKeys } from "@/features/gmail/api";
import type { GlassdoorJobsResponse } from "@/types/api";

export interface UseGlassdoorJobsOptions {
  days: number;
  maxMessages: number;
  enabled: boolean;
}

/**
 * Read-only Glassdoor discovery. Each scan is a live Gmail query, so the query
 * only runs when the caller explicitly enables it (or for the dashboard preview
 * once Gmail is connected).
 */
export function useGlassdoorJobs({ days, maxMessages, enabled }: UseGlassdoorJobsOptions) {
  return useQuery<GlassdoorJobsResponse>({
    queryKey: gmailKeys.jobs(days, maxMessages),
    queryFn: () => fetchGlassdoorJobs(days, maxMessages),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}
