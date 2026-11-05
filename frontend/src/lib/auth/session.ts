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
