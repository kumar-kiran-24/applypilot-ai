import { NextResponse } from "next/server";

import { toApiError } from "@/lib/api/errors";
import { backendFetch } from "@/lib/api/server";
import { getSessionToken } from "@/lib/auth/session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Start Gmail authorization.
 *
 * The backend binds the OAuth state to a signed HttpOnly cookie on the API host,
 * so that cookie has to reach the browser before Google redirects it back to
 * `/api/v1/gmail/callback`. Relay the upstream `Set-Cookie` verbatim: cookies
 * ignore ports, so with the documented single-host setup (127.0.0.1 for both
 * apps) the browser sends it to the API callback. The JWT itself stays in this
 * server's HttpOnly cookie and is only sent as a header here.
 */
export async function POST(request: Request): Promise<NextResponse> {
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    return NextResponse.json({ detail: "Cross-origin request rejected." }, { status: 403 });
  }

  const token = await getSessionToken();
  if (!token) {
    return NextResponse.json({ detail: "Authentication required." }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await backendFetch("gmail/connect", { token });
  } catch {
    return NextResponse.json(
      { detail: "The ApplyPilot API could not be reached. Check that the backend is running." },
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

  const payload = (await upstream.json()) as {
    authorization_url?: unknown;
    required_scopes?: unknown;
    message?: unknown;
  };

  if (typeof payload.authorization_url !== "string" || payload.authorization_url.length === 0) {
    return NextResponse.json(
      { detail: "Gmail authorization could not be started. Please try again." },
      { status: 502 },
    );
  }

  const response = NextResponse.json({
    authorization_url: payload.authorization_url,
    required_scopes: Array.isArray(payload.required_scopes) ? payload.required_scopes : [],
    message: typeof payload.message === "string" ? payload.message : "",
  });

  for (const cookie of upstream.headers.getSetCookie()) {
    response.headers.append("set-cookie", cookie);
  }

  return response;
}
