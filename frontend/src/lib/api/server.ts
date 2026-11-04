import "server-only";

import { apiBaseUrl } from "@/lib/constants";

interface BackendFetchOptions {
  token: string;
  method?: "GET" | "POST" | "DELETE";
  query?: URLSearchParams;
  timeoutMs?: number;
  cookies?: string;
}

/**
 * Server-only call to the FastAPI backend with the user's ApplyPilot token.
 * The token is only ever placed in the Authorization header, never in a URL or
 * a log line.
 */
export async function backendFetch(
  path: string,
  { token, method = "GET", query, timeoutMs = 30_000, cookies }: BackendFetchOptions,
): Promise<Response> {
  const url = new URL(`${apiBaseUrl()}/${path.replace(/^\//, "")}`);
  if (query) {
    query.forEach((value, key) => url.searchParams.set(key, value));
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
  };
  if (cookies) {
    headers.Cookie = cookies;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      method,
      headers,
      cache: "no-store",
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}

/** Abort signal timeout used by discovery routes. */
export function discoveryTimeout(ms: number): AbortSignal {
  return AbortSignal.timeout(ms);
}
