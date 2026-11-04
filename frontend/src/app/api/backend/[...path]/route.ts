import { NextResponse } from "next/server";

import { toApiError } from "@/lib/api/errors";
import { backendFetch } from "@/lib/api/server";
import { getSessionToken } from "@/lib/auth/session";
import { GLASSDOOR_QUERY_KEYS, GLASSDOOR_TIMEOUT_MS, PROXY_ROUTES } from "@/lib/constants";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };

/**
 * Attach the HttpOnly session token server-side and forward the request.
 *
 * Only the routes this frontend actually uses are forwarded: an open proxy
 * would let any authenticated browser reach arbitrary backend endpoints.
 */
async function handle(request: Request, context: RouteContext): Promise<NextResponse> {
  const segments = (await context.params).path ?? [];
  const path = segments.join("/");
  const method = request.method.toUpperCase();

  const allowed = PROXY_ROUTES.some((route) => route.method === method && route.path === path);
  if (!allowed) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }

  if (method !== "GET") {
    const origin = request.headers.get("origin");
    if (origin && origin !== new URL(request.url).origin) {
      return NextResponse.json({ detail: "Cross-origin request rejected." }, { status: 403 });
    }
  }

  const token = await getSessionToken();
  if (!token) {
    return NextResponse.json({ detail: "Authentication required." }, { status: 401 });
  }

  const query = new URLSearchParams();
  for (const key of GLASSDOOR_QUERY_KEYS) {
    const value = new URL(request.url).searchParams.get(key);
    if (value !== null) {
      query.set(key, value);
    }
  }

  let upstream: Response;
  try {
    upstream = await backendFetch(path, {
      token,
      method: method as "GET" | "DELETE",
      query: query.size > 0 ? query : undefined,
      timeoutMs: path === "gmail/glassdoor/jobs" ? GLASSDOOR_TIMEOUT_MS : 30_000,
    });
  } catch {
    return NextResponse.json(
      {
        detail:
          path === "gmail/glassdoor/jobs"
            ? "Job discovery timed out while contacting Gmail. Please try again."
            : "The ApplyPilot API could not be reached.",
        code: "backend_unreachable",
      },
      { status: 504 },
    );
  }

  if (!upstream.ok) {
    const error = await toApiError(upstream);
    return NextResponse.json(
      { detail: error.message, code: error.code, details: error.details },
      { status: error.status },
    );
  }

  const body: unknown = await upstream.json();
  return NextResponse.json(body, {
    status: upstream.status,
    headers: { "Cache-Control": "no-store" },
  });
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
