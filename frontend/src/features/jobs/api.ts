import { GLASSDOOR_TIMEOUT_MS } from "@/lib/constants";
import { apiFetch } from "@/lib/api/client";
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

export const jobKeys = {
  all: ["jobs"] as const,
  list: (query: JobListQuery = {}) => ["jobs", "list", query] as const,
  saved: (limit: number, offset: number) =>
    ["jobs", "saved", limit, offset] as const,
  detail: (jobId: string) => ["jobs", "detail", jobId] as const,
  matches: (jobId: string) => ["jobs", "matches", jobId] as const,
  intelligence: (jobId: string) => ["jobs", "intelligence", jobId] as const,
  comparison: (jobId: string) => ["jobs", "comparison", jobId] as const,
  recommendation: (jobId: string) =>
    ["jobs", "recommendation", jobId] as const,
  summary: ["matching", "summary"] as const,
};

/** Serialise only the filters the backend accepts, dropping empty values. */
function queryString(query: JobListQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const serialised = params.toString();
  return serialised ? `?${serialised}` : "";
}

/**
 * Imports a Glassdoor job URL the user pasted. The backend validates the host,
 * checks DNS and either creates or reuses the posting; it never fetches it.
 */
export function importJob(url: string): Promise<ImportJobResponse> {
  return apiFetch<ImportJobResponse>("jobs/import", {
    method: "POST",
    body: { url },
    timeoutMs: 60_000,
  });
}

/** Lists the user's stored jobs with search, filters, sorting and paging. */
export function fetchJobs(query: JobListQuery = {}): Promise<JobListResponse> {
  return apiFetch<JobListResponse>(`jobs${queryString(query)}`);
}

/** Reads the jobs the user explicitly saved, most recently saved first. */
export function fetchSavedJobs(
  limit = 50,
  offset = 0,
): Promise<JobListResponse> {
  return apiFetch<JobListResponse>(`jobs/saved?limit=${limit}&offset=${offset}`);
}

/** One owned posting, including its per-user saved/dismissed state. */
export function fetchJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`jobs/${jobId}`);
}

/** Persists the saved flag; saving also clears a dismissal. Idempotent. */
export function setJobSaved(jobId: string, saved: boolean): Promise<Job> {
  return apiFetch<Job>(`jobs/${jobId}/save`, {
    method: saved ? "POST" : "DELETE",
  });
}

/** Hides a posting from the default list; nothing is deleted. Idempotent. */
export function dismissJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`jobs/${jobId}/dismiss`, { method: "POST" });
}

/** Returns a dismissed posting to the default list without saving it. */
export function restoreJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`jobs/${jobId}/restore`, { method: "POST" });
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
