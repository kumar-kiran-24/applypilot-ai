import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/api/server";
import { sessionCookie } from "@/lib/auth/cookies";
import { getSessionToken } from "@/lib/auth/session";
import { isSameOrigin } from "@/lib/http/origin";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Revoke the token server-side, then clear the HttpOnly session cookie.
 *
 * The ApplyPilot JWT is stateless, so clearing the cookie alone would leave a
 * leaked copy valid until it expired. The backend records the token's `jti` in
 * its revocation denylist; the cookie is cleared even if that call fails so the
 * user is always signed out of this browser.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json(
      { detail: "Cross-origin request rejected." },
      { status: 403 },
    );
  }

  const token = await getSessionToken();
  if (token) {
    try {
      await backendFetch("auth/logout", { method: "POST", token });
    } catch {
      // The backend may be unreachable; the local cookie is still cleared.
    }
  }

  const response = NextResponse.json(
    { signedOut: true },
    { headers: { "Cache-Control": "no-store" } },
  );
  response.cookies.set(sessionCookie("", 0));
  return response;
}
