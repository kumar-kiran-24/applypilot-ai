import { NextResponse } from "next/server";

import { sessionCookie } from "@/lib/auth/cookies";
import { isSameOrigin } from "@/lib/http/origin";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Clear the HttpOnly session cookie. The backend issues stateless JWTs and has
 * no revocation endpoint, so the token stays valid until it expires; it is
 * simply no longer available to this browser.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return NextResponse.json(
      { detail: "Cross-origin request rejected." },
      { status: 403 },
    );
  }

  const response = NextResponse.json({ signedOut: true });
  response.cookies.set(sessionCookie("", 0));
  return response;
}
