import { DEFAULT_SESSION_MAX_AGE_SECONDS, SESSION_COOKIE } from "@/lib/constants";

/** The JWT lives only in this cookie; browser JavaScript can never read it. */
export const SESSION_COOKIE_NAME = SESSION_COOKIE;

export function sessionMaxAgeSeconds(): number {
  const configured = Number(process.env.SESSION_MAX_AGE_SECONDS);
  return Number.isFinite(configured) && configured > 0
    ? configured
    : DEFAULT_SESSION_MAX_AGE_SECONDS;
}

export function sessionCookieSecure(): boolean {
  if (process.env.SESSION_COOKIE_SECURE === "true") {
    return true;
  }
  return process.env.NODE_ENV === "production";
}

export interface SessionCookieSettings {
  name: string;
  value: string;
  httpOnly: true;
  sameSite: "lax";
  secure: boolean;
  path: "/";
  maxAge: number;
}

/**
 * Central definition of the session cookie so every route handler sets exactly
 * the same flags. `httpOnly` keeps the JWT away from frontend JavaScript,
 * `SameSite=Lax` blocks cross-site subrequests, and `Secure` is forced in
 * production builds.
 */
export function sessionCookie(
  value: string,
  maxAge: number = sessionMaxAgeSeconds(),
): SessionCookieSettings {
  return {
    name: SESSION_COOKIE_NAME,
    value,
    httpOnly: true,
    sameSite: "lax",
    secure: sessionCookieSecure(),
    path: "/",
    maxAge: Math.max(0, Math.floor(maxAge)),
  };
}
