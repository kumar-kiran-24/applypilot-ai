import type { ValidationDetail } from "@/types/api";

/**
 * A failed backend request. `message` is always safe to display: the backend
 * returns user-facing copy in `detail` and never a stack trace or secret.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly details: ValidationDetail[] | undefined;

  constructor(
    status: number,
    message: string,
    code?: string,
    details?: ValidationDetail[],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const FALLBACK_MESSAGE = "Something went wrong. Please try again.";

/** Parse the backend error envelope (`detail`, optional `code`/`details`). */
export async function toApiError(response: Response): Promise<ApiError> {
  let detail: unknown;
  let code: string | undefined;
  let details: ValidationDetail[] | undefined;

  try {
    const payload: unknown = await response.json();
    if (payload && typeof payload === "object") {
      const record = payload as Record<string, unknown>;
      detail = record.detail;
      if (typeof record.code === "string") {
        code = record.code;
      }
      if (Array.isArray(record.details)) {
        details = record.details.filter(
          (item): item is ValidationDetail =>
            !!item &&
            typeof item === "object" &&
            Array.isArray((item as ValidationDetail).loc) &&
            typeof (item as ValidationDetail).msg === "string",
        );
      }
    }
  } catch {
    // Non-JSON error body: keep the generic message.
  }

  const message =
    typeof detail === "string" && detail.trim().length > 0
      ? detail
      : response.status === 401
        ? "Your session has expired. Sign in again."
        : FALLBACK_MESSAGE;

  return new ApiError(response.status, message, code, details);
}

/** Map a thrown error to display copy, including network and timeout failures. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.details && error.details.length > 0) {
      return error.details.map((detail) => detail.msg).join(" ");
    }
    return error.message;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return "The request timed out. Please try again.";
  }
  if (error instanceof TypeError) {
    return "The ApplyPilot API could not be reached. Check that the backend is running.";
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return FALLBACK_MESSAGE;
}
