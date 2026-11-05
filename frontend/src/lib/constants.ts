/** Shared constants safe to import from the browser and the server. */

export const SESSION_COOKIE = "ap_session";

export const DEFAULT_SESSION_MAX_AGE_SECONDS = 3600;

/** Query parameters forwarded for `gmail/glassdoor/jobs`. */
export const GLASSDOOR_QUERY_KEYS = ["days", "max_messages"] as const;

/** Discovery hits Gmail sequentially, so it can take tens of seconds. */
export const GLASSDOOR_TIMEOUT_MS = 90_000;

const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

export interface ProxyRoute {
  /** JSON replies are re-serialised; `file` replies are streamed through. */
  response: "json" | "file";
  /** Query parameters forwarded to the backend for this route. */
  queryKeys: readonly string[];
}

const JSON_ROUTE: ProxyRoute = { response: "json", queryKeys: [] };

/**
 * Backend routes the BFF is allowed to proxy. An open proxy would let any
 * authenticated browser reach arbitrary backend endpoints, so every route is
 * matched explicitly.
 */
const PROXY_ROUTES: ReadonlyArray<{
  method: string;
  pattern: RegExp;
  route: ProxyRoute;
}> = [
  { method: "GET", pattern: /^auth\/me$/, route: JSON_ROUTE },
  { method: "GET", pattern: /^gmail\/status$/, route: JSON_ROUTE },
  {
    method: "GET",
    pattern: /^gmail\/glassdoor\/jobs$/,
    route: { response: "json", queryKeys: GLASSDOOR_QUERY_KEYS },
  },
  { method: "DELETE", pattern: /^gmail\/disconnect$/, route: JSON_ROUTE },
  {
    method: "GET",
    pattern: /^jobs$/,
    route: { response: "json", queryKeys: ["limit", "offset"] },
  },
  {
    method: "POST",
    pattern: /^jobs\/discover$/,
    route: { response: "json", queryKeys: GLASSDOOR_QUERY_KEYS },
  },
  {
    method: "GET",
    pattern: new RegExp(`^jobs/${UUID_PATTERN}$`),
    route: JSON_ROUTE,
  },
  {
    method: "POST",
    pattern: new RegExp(`^jobs/${UUID_PATTERN}/match$`),
    route: JSON_ROUTE,
  },
  {
    method: "GET",
    pattern: new RegExp(`^jobs/${UUID_PATTERN}/matches$`),
    route: JSON_ROUTE,
  },
  { method: "POST", pattern: /^matching\/bulk$/, route: JSON_ROUTE },
  { method: "GET", pattern: /^matching\/summary$/, route: JSON_ROUTE },
  { method: "GET", pattern: /^resumes$/, route: JSON_ROUTE },
  {
    method: "POST",
    pattern: /^resumes\/upload$/,
    route: JSON_ROUTE,
  },
  {
    method: "GET",
    pattern: new RegExp(`^resumes/${UUID_PATTERN}$`),
    route: JSON_ROUTE,
  },
  {
    method: "GET",
    pattern: new RegExp(`^resumes/${UUID_PATTERN}/download$`),
    route: { response: "file", queryKeys: ["disposition"] },
  },
  {
    method: "PATCH",
    pattern: new RegExp(`^resumes/${UUID_PATTERN}/default$`),
    route: JSON_ROUTE,
  },
  {
    method: "DELETE",
    pattern: new RegExp(`^resumes/${UUID_PATTERN}$`),
    route: JSON_ROUTE,
  },
];

/** Resolve an incoming BFF path to its proxy policy, or `null` if disallowed. */
export function matchProxyRoute(method: string, path: string): ProxyRoute | null {
  const entry = PROXY_ROUTES.find(
    (route) => route.method === method && route.pattern.test(path),
  );
  return entry ? entry.route : null;
}

/** Matches the backend default: 10 MB. */
export const MAX_RESUME_BYTES = 10 * 1024 * 1024;

export const RESUME_ACCEPT = "application/pdf,.pdf";

export const APP_NAME = "ApplyPilot AI";
export const APP_TAGLINE = "Your Intelligent Job Application Agent";

export const apiBaseUrl = (): string =>
  (
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1"
  ).replace(/\/$/, "");
