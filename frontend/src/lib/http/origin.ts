/**
 * CSRF guard for cookie-authenticated mutations: when the browser sends an
 * Origin header it must match the host serving this request. Requests without
 * an Origin header (server-to-server tooling) are allowed through; the session
 * cookie's SameSite=Lax is the primary defence.
 *
 * The Host header is used rather than the parsed request URL because Next can
 * normalise a dev server's loopback origin to `localhost`.
 */
export function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    return true;
  }
  try {
    return new URL(origin).host === request.headers.get("host");
  } catch {
    return false;
  }
}
