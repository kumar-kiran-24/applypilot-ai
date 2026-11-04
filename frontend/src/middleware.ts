import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/constants";

/**
 * Cheap gate only: the cookie's presence stops an obviously signed-out browser
 * from rendering the shell. The protected layout validates it against the
 * backend before any authenticated data is requested.
 */
export function middleware(request: NextRequest) {
  const hasSession = Boolean(request.cookies.get(SESSION_COOKIE)?.value);
  if (hasSession) {
    return NextResponse.next();
  }

  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/jobs/:path*",
    "/applications/:path*",
    "/resumes/:path*",
    "/gmail/:path*",
    "/assistant/:path*",
    "/settings/:path*",
  ],
};
