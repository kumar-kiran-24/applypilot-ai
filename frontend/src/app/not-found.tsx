import Link from "next/link";
import { Compass } from "lucide-react";

import { buttonClasses } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-5 py-12">
      <div className="glass-panel w-full max-w-md p-7 text-center">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.04] text-sky-300">
          <Compass aria-hidden="true" className="h-5 w-5" />
        </span>
        <p className="mt-4 text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-subtle">
          404
        </p>
        <h1 className="mt-1 text-lg font-semibold text-ink">
          This page does not exist
        </h1>
        <p className="mt-1.5 text-[13px] leading-relaxed text-ink-muted">
          The link may be outdated, or the feature is not part of Phase 1.
        </p>
        <Link
          href="/dashboard"
          className={buttonClasses({ size: "sm", className: "mt-6" })}
        >
          Go to dashboard
        </Link>
      </div>
    </main>
  );
}
