import { NextResponse } from "next/server";

import { toApiError } from "@/lib/api/errors";
import { backendFetch, type BackendFetchOptions } from "@/lib/api/server";
import { getSessionToken } from "@/lib/auth/session";
import {
  GLASSDOOR_TIMEOUT_MS,
  matchProxyRoute,
} from "@/lib/constants";
import { isSameOrigin } from "@/lib/http/origin";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };

const RESUME_UPLOAD_PATH = "resumes/upload";

/**
 * Attach the HttpOnly session token server-side and forward the request.
 *
 * Only the routes this frontend actually uses are forwarded: an open proxy
 * would let any authenticated browser reach arbitrary backend endpoints.
 */
async function handle(
  request: Request,
  context: RouteContext,
): Promise<NextResponse> {
  const segments = (await context.params).path ?? [];
  const path = segments.join("/");
  const method = request.method.toUpperCase();

  const route = matchProxyRoute(method, path);
  if (!route) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }

  if (method !== "GET" && !isSameOrigin(request)) {
    return NextResponse.json(
      { detail: "Cross-origin request rejected." },
      { status: 403 },
    );
  }

  const token = await getSessionToken();
  if (!token) {
    return NextResponse.json(
      { detail: "Authentication required." },
      { status: 401 },
    );
  }

  const query = new URLSearchParams();
  const searchParams = new URL(request.url).searchParams;
  for (const key of route.queryKeys) {
    const value = searchParams.get(key);
    if (value !== null) {
      query.set(key, value);
    }
  }

  const isUpload = path === RESUME_UPLOAD_PATH;
  const isGmailScan =
    path === "gmail/glassdoor/jobs" || path === "jobs/discover";
  const options: BackendFetchOptions = {
    token,
    method: method as NonNullable<BackendFetchOptions["method"]>,
    query: query.size > 0 ? query : undefined,
    timeoutMs: isUpload ? 60_000 : isGmailScan ? GLASSDOOR_TIMEOUT_MS : 30_000,
  };

  if (isUpload) {
    try {
      options.formData = await request.formData();
    } catch {
      return NextResponse.json(
        { detail: "The upload could not be read.", code: "invalid_upload" },
        { status: 400 },
      );
    }
  } else if (method !== "GET") {
    // Matching endpoints accept small JSON bodies (refresh flags, job id
    // lists). The body is forwarded as-is and never logged.
    try {
      const text = await request.text();
      if (text.trim()) {
        options.body = JSON.parse(text) as unknown;
      }
    } catch {
      return NextResponse.json(
        { detail: "The request body could not be read.", code: "invalid_body" },
        { status: 400 },
      );
    }
  }

  let upstream: Response;
  try {
    upstream = await backendFetch(path, options);
  } catch {
    return NextResponse.json(
      {
        detail: isGmailScan
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

  if (route.response === "file") {
    const headers = new Headers({
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/pdf",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    const disposition = upstream.headers.get("content-disposition");
    if (disposition) {
      headers.set("Content-Disposition", disposition);
    }
    const contentLength = upstream.headers.get("content-length");
    if (contentLength) {
      headers.set("Content-Length", contentLength);
    }
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers,
    });
  }

  const body: unknown = await upstream.json();
  return NextResponse.json(body, {
    status: upstream.status,
    headers: { "Cache-Control": "no-store" },
  });
}

export {
  handle as GET,
  handle as POST,
  handle as PUT,
  handle as PATCH,
  handle as DELETE,
};
