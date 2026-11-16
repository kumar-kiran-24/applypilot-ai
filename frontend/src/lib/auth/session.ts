import "server-only";

import { cookies } from "next/headers";

import { backendFetch } from "@/lib/api/server";
import { SESSION_COOKIE } from "@/lib/constants";
import type { AuthUser } from "@/types/api";

/** The JWT is stored only in this HttpOnly cookie, never in browser storage. */
export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  const value = store.get(SESSION_COOKIE)?.value;
  return value && value.trim().length > 0 ? value : null;
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

/**
 * What an authenticated page needs to know before loading data.
 *
 * `getCurrentUser` deliberately flattens every failure to `null`, which is fine
 * for a public page that just shows a signed-out state. A protected page must
 * not: while the API was down or restarting, that flattening redirected an
 * actively signed-in user to `/login?expired=1`, which claims their session
 * expired and throws away the workspace. These three outcomes are kept apart.
 */
export type SessionProbe =
  | { status: "authenticated"; user: AuthUser }
  | { status: "unauthenticated" }
  | { status: "unavailable" };

export async function probeSession(token: string): Promise<SessionProbe> {
  let response: Response;
  try {
    response = await backendFetch("auth/me", { token, timeoutMs: 10_000 });
  } catch {
    // Connection refused, DNS failure or timeout: the token was never checked.
    return { status: "unavailable" };
  }
  if (response.status === 401 || response.status === 403) {
    return { status: "unauthenticated" };
  }
  if (!response.ok) {
    return { status: "unavailable" };
  }
  try {
    return { status: "authenticated", user: (await response.json()) as AuthUser };
  } catch {
    return { status: "unavailable" };
  }
}
