import { NextResponse } from "next/server";

import { SESSION_COOKIE } from "@/lib/constants";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Clear the HttpOnly session cookie. The backend issues stateless JWTs and has
 * no revocation endpoint, so the token stays valid until it expires; it is
 * simply no longer available to this browser.
 */
export async function POST(request: Request): Promise<NextResponse> {
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    return NextResponse.json({ detail: "Cross-origin request rejected." }, { status: 403 });
  }

  const response = NextResponse.json({ signedOut: true });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: "",
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
  return response;
}
