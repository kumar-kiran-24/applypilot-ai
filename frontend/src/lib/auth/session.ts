import "server-only";

import { cookies } from "next/headers";

import { backendFetch } from "@/lib/api/server";
import { DEFAULT_SESSION_MAX_AGE_SECONDS, SESSION_COOKIE } from "@/lib/constants";
import type { AuthUser } from "@/types/api";

/** The JWT is stored only in this HttpOnly cookie, never in browser storage. */
export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  const value = store.get(SESSION_COOKIE)?.value;
  return value && value.trim().length > 0 ? value : null;
}

export function sessionCookieValue(token: string): string {
  return token;
}

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

/**
 * Resolve the signed-in user from the backend. Returns `null` for a missing or
 * rejected token so pages can render a clean signed-out state instead of a 500.
 */
export async function getCurrentUser(token: string): Promise<AuthUser | null> {
  try {
    const response = await backendFetch("auth/me", { token });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as AuthUser;
  } catch {
    return null;
  }
}
