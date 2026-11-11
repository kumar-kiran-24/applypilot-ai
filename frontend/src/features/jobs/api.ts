import { GLASSDOOR_TIMEOUT_MS } from "@/lib/constants";
import { apiFetch } from "@/lib/api/client";
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

export const jobKeys = {
  all: ["jobs"] as const,
  list: (limit: number, offset: number) =>
    ["jobs", "list", limit, offset] as const,
  matches: (jobId: string) => ["jobs", "matches", jobId] as const,
  intelligence: (jobId: string) => ["jobs", "intelligence", jobId] as const,
  comparison: (jobId: string) => ["jobs", "comparison", jobId] as const,
  recommendation: (jobId: string) =>
    ["jobs", "recommendation", jobId] as const,
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

/** Reads stored job intelligence; the backend labels it `cached: false` when
 *  it computed a reading for this response only. */
export function fetchJobIntelligence(
  jobId: string,
): Promise<JobIntelligenceResponse> {
  return apiFetch<JobIntelligenceResponse>(`jobs/${jobId}/intelligence`);
}

/** Persists a job analysis. `force` re-runs extraction for an unchanged posting. */
export function analyzeJob(
  jobId: string,
  { force = false, useLlm = true }: { force?: boolean; useLlm?: boolean } = {},
): Promise<JobIntelligenceResponse> {
  return apiFetch<JobIntelligenceResponse>(`jobs/${jobId}/analyze`, {
    method: "POST",
    body: { force, use_llm: useLlm },
    timeoutMs: 120_000,
  });
}

/** Compares every owned resume; the backend ranks and recommends, never the UI. */
export function compareResumes(
  jobId: string,
  force = false,
): Promise<ResumeComparisonResponse> {
  return apiFetch<ResumeComparisonResponse>(`jobs/${jobId}/compare-resumes`, {
    method: "POST",
    body: { force },
    timeoutMs: 120_000,
  });
}

/** Reads the stored recommendation without recomputing anything. */
export function fetchResumeRecommendation(
  jobId: string,
): Promise<ResumeComparisonResponse> {
  return apiFetch<ResumeComparisonResponse>(`jobs/${jobId}/recommendation`);
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
