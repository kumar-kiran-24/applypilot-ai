import { apiFetch, bffFetch } from "@/lib/api/client";
import { GLASSDOOR_TIMEOUT_MS } from "@/lib/constants";
import type {
  GmailConnectResponse,
  GmailDisconnectResponse,
  GmailStatus,
  GlassdoorJobsResponse,
} from "@/types/api";

export const gmailKeys = {
  status: ["gmail", "status"] as const,
  jobs: (days: number, maxMessages: number) =>
    ["gmail", "glassdoor", "jobs", days, maxMessages] as const,
};

export function fetchGmailStatus(): Promise<GmailStatus> {
  return apiFetch<GmailStatus>("gmail/status");
}

/** Starts the OAuth flow; the API state cookie is relayed by the BFF. */
export function startGmailConnection(): Promise<GmailConnectResponse> {
  return bffFetch<GmailConnectResponse>("/api/gmail/connect", { method: "POST" });
}

export function disconnectGmail(): Promise<GmailDisconnectResponse> {
  return apiFetch<GmailDisconnectResponse>("gmail/disconnect", { method: "DELETE" });
}

export function fetchGlassdoorJobs(
  days: number,
  maxMessages: number,
): Promise<GlassdoorJobsResponse> {
  return apiFetch<GlassdoorJobsResponse>(
    `gmail/glassdoor/jobs?days=${days}&max_messages=${maxMessages}`,
    { timeoutMs: GLASSDOOR_TIMEOUT_MS },
  );
}
