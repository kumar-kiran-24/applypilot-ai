"use client";

import Link from "next/link";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { useEffect } from "react";

import { Button, buttonClasses } from "@/components/ui/button";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Keep server-side detail server-side: log only the digest and name.
    if (error.digest) {
      console.error("ApplyPilot UI error", error.digest, error.name);
    }
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center px-5 py-12">
      <div className="glass-panel w-full max-w-md p-7 text-center">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-red-500/25 bg-red-500/10 text-red-300">
          <AlertTriangle aria-hidden="true" className="h-5 w-5" />
        </span>
        <h1 className="mt-4 text-lg font-semibold text-ink">
          Something went wrong
        </h1>
        <p className="mt-1.5 text-[13px] leading-relaxed text-ink-muted">
          This view failed to render. Your session is still intact; retrying
          usually fixes a transient backend error.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Button onClick={reset} size="sm">
            <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
            Try again
          </Button>
          <Link
            href="/dashboard"
            className={buttonClasses({ variant: "secondary", size: "sm" })}
          >
            Back to dashboard
          </Link>
        </div>
      </div>
    </main>
  );
}
