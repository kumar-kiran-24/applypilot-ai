"use client";

import { useEffect } from "react";

import { Spinner } from "@/components/ui/spinner";
import { RETURN_TO_COOKIE } from "@/lib/utils/url";

/**
 * Holds the intended `/apply?url=...` path across Google sign-in.
 *
 * The cookie is a navigation hint only (no token, code or profile data), is
 * short-lived, and is re-validated by the callback before it is used.
 */
export function ApplyReturnGate({ returnTo }: { returnTo: string | null }) {
  useEffect(() => {
    if (returnTo) {
      document.cookie = `${RETURN_TO_COOKIE}=${encodeURIComponent(
        returnTo,
      )}; Path=/; Max-Age=600; SameSite=Lax`;
    }
    window.location.replace("/login?next=apply");
  }, [returnTo]);

  return (
    <div className="glass-panel w-full max-w-md p-6 text-center sm:p-8">
      <Spinner className="mx-auto h-5 w-5 text-sky-300" />
      <h1 className="mt-3 text-lg font-semibold tracking-tight text-ink">
        Sign in to import this job
      </h1>
      <p className="mt-2 text-[12px] leading-relaxed text-ink-muted">
        Your job link is kept in this browser only, and you will return here
        right after Google sign-in.
      </p>
    </div>
  );
}
