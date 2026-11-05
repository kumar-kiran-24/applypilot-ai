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
