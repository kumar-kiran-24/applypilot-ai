import { NextResponse } from "next/server";

import { toApiError } from "@/lib/api/errors";
import { backendFetch } from "@/lib/api/server";
import { sessionCookie, sessionMaxAgeSeconds } from "@/lib/auth/cookies";
import { isSameOrigin } from "@/lib/http/origin";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Shape produced by the backend's 256-bit `secrets.token_urlsafe(32)`. */
const AUTH_CODE_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;
const JWT_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

const INVALID_CODE_DETAIL =
  "This sign-in link is not valid. Start again from the login page.";
const GENERIC_FAILURE_DETAIL =
  "Sign-in could not be completed. Start again from the login page.";
const BACKEND_UNREACHABLE_DETAIL =
  "The ApplyPilot API could not be reached. Check that the backend is running.";

/**
 * Redeem a one-time login code.
 *
 * The browser sends the opaque code it received from the backend's Google
 * callback. This route handler (never client JavaScript) talks to the backend,
 * receives the ApplyPilot JWT and stores it in the HttpOnly `ap_session`
 * cookie. The token is never returned in the response body, a URL, localStorage
 * or sessionStorage.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json(
      { detail: "Cross-origin request rejected." },
      { status: 403 },
    );
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request body." }, { status: 400 });
  }

  const code =
    payload &&
    typeof payload === "object" &&
    typeof (payload as { code?: unknown }).code === "string"
      ? (payload as { code: string }).code.trim()
      : "";

  if (!AUTH_CODE_PATTERN.test(code)) {
    return NextResponse.json({ detail: INVALID_CODE_DETAIL }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await backendFetch("auth/login/exchange", {
      method: "POST",
      body: { code },
    });
  } catch {
    return NextResponse.json(
      { detail: BACKEND_UNREACHABLE_DETAIL, code: "backend_unreachable" },
      { status: 504 },
    );
  }

  if (!upstream.ok) {
    const error = await toApiError(upstream);
    return NextResponse.json(
      { detail: error.message, code: error.code },
      { status: error.status },
    );
  }

  let token: unknown;
  let expiresIn: unknown;
  try {
    const body = (await upstream.json()) as {
      access_token?: unknown;
      expires_in?: unknown;
    };
    token = body.access_token;
    expiresIn = body.expires_in;
  } catch {
    return NextResponse.json({ detail: GENERIC_FAILURE_DETAIL }, { status: 502 });
  }

  if (typeof token !== "string" || !JWT_PATTERN.test(token)) {
    return NextResponse.json({ detail: GENERIC_FAILURE_DETAIL }, { status: 502 });
  }

  // Never let the cookie outlive the JWT it carries.
  const seconds = Number(expiresIn);
  const maxAge =
    Number.isFinite(seconds) && seconds > 0
      ? Math.min(sessionMaxAgeSeconds(), seconds)
      : sessionMaxAgeSeconds();

  const response = NextResponse.json(
    { authenticated: true },
    { headers: { "Cache-Control": "no-store" } },
  );
  response.cookies.set(sessionCookie(token, maxAge));
  return response;
}
