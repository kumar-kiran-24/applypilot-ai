/** Shared constants safe to import from the browser and the server. */

export const SESSION_COOKIE = "ap_session";

export const DEFAULT_SESSION_MAX_AGE_SECONDS = 3600;

/** Backend routes the BFF is allowed to proxy. Anything else is rejected. */
export const PROXY_ROUTES = [
  { method: "GET", path: "auth/me" },
  { method: "GET", path: "gmail/status" },
  { method: "GET", path: "gmail/glassdoor/jobs" },
  { method: "DELETE", path: "gmail/disconnect" },
] as const;

/** Query parameters forwarded for `gmail/glassdoor/jobs`. */
export const GLASSDOOR_QUERY_KEYS = ["days", "max_messages"] as const;

/** Discovery hits Gmail sequentially, so it can take tens of seconds. */
export const GLASSDOOR_TIMEOUT_MS = 90_000;

export const APP_NAME = "ApplyPilot AI";
export const APP_TAGLINE = "Your Intelligent Job Application Agent";

export const apiBaseUrl = (): string =>
  (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1").replace(/\/$/, "");
