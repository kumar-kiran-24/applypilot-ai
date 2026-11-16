/**
 * Client-side validation for a pasted job listing URL.
 *
 * The backend is authoritative: it re-validates the host, path and DNS before
 * anything is stored. This only avoids a pointless request and gives an
 * immediate, specific message. It never fetches the URL.
 */

// Explicit TLD shape, not `[a-z.]+`: a look-alike such as
// `glassdoor.com.evil.com` must never be treated as Glassdoor.
const GLASSDOOR_HOST =
  /^(?:[a-z0-9-]+\.)*glassdoor\.(?:[a-z]{2,3}|co\.[a-z]{2}|com\.[a-z]{2})$/i;
const JOB_PATH = /\/job-listing\/|\/joblisting|\/partner\/joblisting|\/job\//i;
const SUPPORTED_PORTS = new Set(["", "80", "443"]);

export type JobImportValidation =
  | { ok: true; url: string }
  | { ok: false; error: string };

function failure(error: string): JobImportValidation {
  return { ok: false, error };
}

/** Validate a pasted Glassdoor job listing URL without contacting it. */
export function validateJobImportUrl(raw: string): JobImportValidation {
  const trimmed = (raw ?? "").trim();
  if (!trimmed) {
    return failure("Paste a Glassdoor job listing URL first.");
  }
  if (trimmed.length > 1000) {
    return failure("That URL is too long. Paste the job listing link only.");
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return failure(
      "Enter a complete URL starting with https:// (for example https://www.glassdoor.com/job-listing/...).",
    );
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return failure("Only http(s) job listing URLs are supported.");
  }
  if (parsed.username || parsed.password) {
    return failure("The URL must not contain a username or password.");
  }
  if (!SUPPORTED_PORTS.has(parsed.port)) {
    return failure("Use a standard http or https port.");
  }
  if (!GLASSDOOR_HOST.test(parsed.hostname)) {
    return failure(
      "Only Glassdoor job listing URLs are supported. Paste a link from glassdoor.com or its country site.",
    );
  }
  if (!JOB_PATH.test(parsed.pathname)) {
    return failure(
      "That Glassdoor link is not a job posting. Open the listing and copy its job-listing URL.",
    );
  }

  return { ok: true, url: parsed.toString() };
}
