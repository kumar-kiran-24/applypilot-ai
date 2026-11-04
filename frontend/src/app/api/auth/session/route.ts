import { NextResponse } from "next/server";

import { toApiError } from "@/lib/api/errors";
import { backendFetch } from "@/lib/api/server";
import {
  getCurrentUser,
  getSessionToken,
  sessionCookieSecure,
  sessionMaxAgeSeconds,
} from "@/lib/auth/session";
import { SESSION_COOKIE } from "@/lib/constants";
import type { AuthUser, SessionResponse } from "@/types/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const JWT_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

/** GET: report whether this browser has a valid session (used by the client). */
export async function GET(): Promise<NextResponse<SessionResponse>> {
  const token = await getSessionToken();
  const user = token ? await getCurrentUser(token) : null;
  return NextResponse.json({ authenticated: user !== null, user });
}

/**
 * POST: interim sign-in bridge.
 *
 * The backend's Google callback currently renders the ApplyPilot JWT as JSON in
 * the browser instead of redirecting to this app, so there is no way for a
 * browser client to receive it securely yet. Until that handoff is agreed, this
 * route lets a developer exchange that token for an HttpOnly cookie. It is
 * disabled in production builds and documented as a temporary measure in the
 * README; the token is never logged, stored in localStorage or put in a URL.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.json(
      { detail: "Interim sign-in is disabled in production builds." },
      { status: 403 },
    );
  }

  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    return NextResponse.json({ detail: "Cross-origin request rejected." }, { status: 403 });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request body." }, { status: 400 });
  }

  const token =
    payload && typeof payload === "object" && typeof (payload as { token?: unknown }).token === "string"
      ? (payload as { token: string }).token.trim()
      : "";

  if (!JWT_PATTERN.test(token)) {
    return NextResponse.json(
      { detail: "That value does not look like an ApplyPilot access token." },
      { status: 400 },
    );
  }

  // Only the backend can tell whether the token is genuine and unexpired.
  let response: Response;
  try {
    response = await backendFetch("auth/me", { token });
  } catch {
    return NextResponse.json(
      { detail: "The ApplyPilot API could not be reached. Check that the backend is running." },
      { status: 502 },
    );
  }

  if (!response.ok) {
    const error = await toApiError(response);
    return NextResponse.json({ detail: error.message, code: error.code }, { status: error.status });
  }

  const user = (await response.json()) as AuthUser;
  const next = NextResponse.json({ authenticated: true, user } satisfies SessionResponse);
  next.cookies.set({
    name: SESSION_COOKIE,
    value: token,
    httpOnly: true,
    sameSite: "lax",
    secure: sessionCookieSecure(),
    path: "/",
    maxAge: sessionMaxAgeSeconds(),
  });
  return next;
}
