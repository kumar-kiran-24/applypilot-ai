import "server-only";

import { apiBaseUrl } from "@/lib/constants";

export interface BackendFetchOptions {
  /** Omit for unauthenticated calls (for example the login-code exchange). */
  token?: string;
  method?: "GET" | "POST" | "DELETE" | "PATCH";
  query?: URLSearchParams;
  body?: unknown;
  formData?: FormData;
  timeoutMs?: number;
  cookies?: string;
}

/**
 * Server-only call to the FastAPI backend. The ApplyPilot token, when present,
 * is only ever placed in the Authorization header - never in a URL or a log
 * line. Bodies are sent as JSON and are never logged.
 */
export async function backendFetch(
  path: string,
  {
    token,
    method = "GET",
    query,
    body,
    formData,
    timeoutMs = 30_000,
    cookies,
  }: BackendFetchOptions,
): Promise<Response> {
  const url = new URL(`${apiBaseUrl()}/${path.replace(/^\//, "")}`);
  if (query) {
    query.forEach((value, key) => url.searchParams.set(key, value));
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (cookies) {
    headers.Cookie = cookies;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      method,
      headers,
      // FormData is passed through untouched so fetch sets the multipart
      // boundary; JSON bodies are serialised.
      body: formData ?? (body === undefined ? undefined : JSON.stringify(body)),
      cache: "no-store",
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}
