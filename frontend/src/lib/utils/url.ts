/**
 * Short-lived navigation hint used to resume `/apply?url=...` after Google
 * sign-in. It contains no token, code or profile data - only a path.
 */
export const RETURN_TO_COOKIE = "ap_return_to";

/**
 * Accept only an internal `/apply` path.
 *
 * The callback redirects to this value, so an absolute URL, a protocol-relative
 * path or a backslash trick must be rejected to avoid an open redirect.
 */
export function safeReturnPath(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed.startsWith("/apply")) return null;
  if (trimmed.startsWith("//") || trimmed.includes("\\")) return null;
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return null;

  try {
    const parsed = new URL(trimmed, "https://applypilot.invalid");
    if (parsed.origin !== "https://applypilot.invalid") return null;
    if (parsed.pathname !== "/apply") return null;
    const keys = [...parsed.searchParams.keys()];
    if (keys.some((key) => key !== "url")) return null;
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

/**
 * Only http(s) links coming from the backend are rendered as anchors, so a
 * malformed or hostile value can never become a `javascript:` URL.
 */
export function safeExternalUrl(
  value: string | null | undefined,
): string | null {
  if (!value) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}
