import { ApiError, toApiError } from "@/lib/api/errors";

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE" | "PATCH";
  body?: unknown;
  formData?: FormData;
  timeoutMs?: number;
}

/**
 * Browser-side fetch to one of this app's own route handlers. The ApplyPilot
 * JWT lives in an HttpOnly cookie owned by the server, so it is never readable
 * or attachable from client code. A 401 means the cookie is gone or expired.
 */
export async function bffFetch<T>(
  url: string,
  options: RequestOptions = {},
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? 30_000,
  );

  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers: options.body
        ? { "Content-Type": "application/json" }
        : undefined,
      // Let fetch set the multipart boundary for uploads.
      body: options.formData
        ? options.formData
        : options.body
          ? JSON.stringify(options.body)
          : undefined,
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(408, "The request timed out. Please try again.");
    }
    throw new ApiError(
      503,
      "The ApplyPilot API could not be reached. Check that the backend is running.",
    );
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    const error = await toApiError(response);
    if (error.status === 401 && typeof window !== "undefined") {
      const next = encodeURIComponent(window.location.pathname);
      if (!window.location.pathname.startsWith("/login")) {
        window.location.assign(`/login?expired=1&next=${next}`);
      }
    }
    throw error;
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

/** Call a backend route through the allow-listed BFF proxy. */
export function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  return bffFetch<T>(`/api/backend/${path}`, options);
}
