import { GLASSDOOR_TIMEOUT_MS } from "@/lib/constants";
import { apiFetch } from "@/lib/api/client";
import type {
  BulkMatchRequest,
  BulkMatchResponse,
  DiscoverJobsResponse,
  JobMatchesResponse,
  JobListResponse,
  MatchingSummary,
} from "@/types/api";

export const jobKeys = {
  all: ["jobs"] as const,
  list: (limit: number, offset: number) =>
    ["jobs", "list", limit, offset] as const,
  matches: (jobId: string) => ["jobs", "matches", jobId] as const,
  summary: ["matching", "summary"] as const,
};

export function fetchJobs(
  limit = 50,
  offset = 0,
): Promise<JobListResponse> {
  return apiFetch<JobListResponse>(`jobs?limit=${limit}&offset=${offset}`);
}

/** Runs the read-only Gmail scan and stores the postings for matching. */
export function discoverJobs(
  days: number,
  maxMessages: number,
): Promise<DiscoverJobsResponse> {
  return apiFetch<DiscoverJobsResponse>(
    `jobs/discover?days=${days}&max_messages=${maxMessages}`,
    { method: "POST", timeoutMs: GLASSDOOR_TIMEOUT_MS },
  );
}

export function fetchJobMatches(jobId: string): Promise<JobMatchesResponse> {
  return apiFetch<JobMatchesResponse>(`jobs/${jobId}/matches`);
}

export function matchJob(
  jobId: string,
  force = false,
): Promise<JobMatchesResponse> {
  return apiFetch<JobMatchesResponse>(`jobs/${jobId}/match`, {
    method: "POST",
    body: { force },
    timeoutMs: 60_000,
  });
}

export function bulkMatchJob(
  request: BulkMatchRequest,
): Promise<BulkMatchResponse> {
  return apiFetch<BulkMatchResponse>("matching/bulk", {
    method: "POST",
    body: request,
    timeoutMs: 120_000,
  });
}

export function fetchMatchingSummary(): Promise<MatchingSummary> {
  return apiFetch<MatchingSummary>("matching/summary");
}
