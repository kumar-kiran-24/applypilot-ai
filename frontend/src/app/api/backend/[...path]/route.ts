import { NextResponse } from "next/server";

import { toApiError } from "@/lib/api/errors";
import { backendFetch, type BackendFetchOptions } from "@/lib/api/server";
import { sessionCookie } from "@/lib/auth/cookies";
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
 * Routes that drive Playwright against the employer's live page.
 *
 * Navigating, evaluating and typing into a real ATS form is far slower than a
 * database read: a plan on a small page already takes ~8s, and a multi-field
 * fill easily exceeds the 30s default. Timing those out reported a healthy
 * backend as "could not be reached" and threw away completed work.
 */
const LIVE_BROWSER_PATH =
  /^browser-sessions\/by-application\/[0-9a-fA-F-]{36}\/(detect-destination|destination|recover|form-analysis|fill-plan|fill)$/;

const LIVE_BROWSER_TIMEOUT_MS = 150_000;

function isTimeout(error: unknown): boolean {
  // `fetch` aborts with an AbortError (DOMException) in both the Node and Edge
  // runtimes; undici may surface it as a TimeoutError.
  return (
    error instanceof Error &&
    (error.name === "AbortError" ||
      error.name === "TimeoutError" ||
      error.message === "This operation was aborted")
  );
}

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
    // Distinct from a backend 404: this request never left the app. The message
    // says so, without disclosing the allow-list, so a stale client build or a
    // missing proxy entry is not mistaken for a deleted resource.
    return NextResponse.json(
      {
        detail:
          "This action is not available through the ApplyPilot API proxy. "
          + "Reload the app, and update it if the problem continues.",
        code: "proxy_route_not_allowed",
      },
      { status: 404 },
    );
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
  const isLiveBrowser = LIVE_BROWSER_PATH.test(path);
  const options: BackendFetchOptions = {
    token,
    method: method as NonNullable<BackendFetchOptions["method"]>,
    query: query.size > 0 ? query : undefined,
    timeoutMs: isUpload
      ? 60_000
      : isGmailScan
        ? GLASSDOOR_TIMEOUT_MS
        : isLiveBrowser
          ? LIVE_BROWSER_TIMEOUT_MS
          : 30_000,
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

  const startedAt = Date.now();
  let upstream: Response;
  try {
    upstream = await backendFetch(path, options);
  } catch (error) {
    // A timeout means the API answered too slowly; an unreachable API means the
    // connection never worked. Reporting both as "could not be reached" sent
    // users to restart a backend that was healthy all along.
    const reason = isTimeout(error)
      ? "timeout"
      : error instanceof Error
        ? error.name
        : "unknown";
    // Server-side evidence for the exact request that failed. Never the token,
    // a body or employer page content.
    console.warn(
      `[bff] ${method} ${path} failed after ${Date.now() - startedAt}ms (${reason})`,
    );
    if (isTimeout(error)) {
      return NextResponse.json(
        {
          detail: isLiveBrowser
            ? "The ApplyPilot API did not respond in time while working on the "
              + "employer's page. The action may have partially completed: check "
              + "the form and the session state before retrying."
            : "The ApplyPilot API did not respond in time. Please try again.",
          code: "backend_timeout",
        },
        { status: 504 },
      );
    }
    return NextResponse.json(
      {
        detail: isGmailScan
          ? "Job discovery timed out while contacting Gmail. Please try again."
          : "The ApplyPilot API could not be reached. Start the backend "
            + "(python -m scripts.run_server from backend/) and try again.",
        code: "backend_unreachable",
      },
      { status: 504 },
    );
  }

  if (!upstream.ok) {
    const error = await toApiError(upstream);
    const response = NextResponse.json(
      { detail: error.message, code: error.code, details: error.details },
      { status: error.status },
    );
    // The token was rejected (expired, revoked by logout, or account removed).
    // Drop the stale cookie so the next request starts from a clean state.
    if (error.status === 401) {
      response.cookies.set(sessionCookie("", 0));
    }
    return response;
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
