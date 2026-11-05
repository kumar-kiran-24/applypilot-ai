import { ApiError, describeError } from "@/lib/api/errors";

export interface DiscoveryErrorCopy {
  message: string;
  reconnect: boolean;
}

/**
 * Map the backend's documented discovery failures to copy that tells the user
 * what to do next. Unknown errors fall back to the backend's safe `detail`.
 */
export function discoveryErrorCopy(error: unknown): DiscoveryErrorCopy {
  if (error instanceof ApiError) {
    const reconnect =
      error.code === "gmail_credentials_invalid" ||
      error.code === "gmail_scope_missing" ||
      error.code === "gmail_not_connected" ||
      error.status === 409 ||
      error.status === 404 ||
      error.status === 403;

    switch (error.code) {
      case "gmail_not_connected":
        return {
          message:
            "Gmail is not connected for this account. Connect it to scan job alerts.",
          reconnect: true,
        };
      case "gmail_credentials_invalid":
        return {
          message:
            "Gmail authorization expired or was revoked at Google. Reconnect Gmail to keep scanning.",
          reconnect: true,
        };
      case "gmail_scope_missing":
        return {
          message:
            "Read-only Gmail access is missing. Reconnect and allow gmail.readonly.",
          reconnect: true,
        };
      case "gmail_rate_limited":
        return {
          message:
            "Gmail is rate limiting requests right now. Wait a minute, then scan again.",
          reconnect: false,
        };
      case "gmail_api_error":
        return {
          message:
            "Gmail could not be reached. Check the backend logs, then try again.",
          reconnect: false,
        };
      case "validation_error":
        return {
          message: "Those search options are outside the supported range.",
          reconnect: false,
        };
      default:
        break;
    }

    if (error.status === 429) {
      return {
        message:
          "Gmail is rate limiting requests right now. Wait a minute, then scan again.",
        reconnect: false,
      };
    }

    return { message: describeError(error), reconnect };
  }

  return { message: describeError(error), reconnect: false };
}
