import Link from "next/link";
import { ArrowUpRight, Sparkles } from "lucide-react";

import { buttonClasses } from "@/components/ui/button";

export function WelcomeBanner({ name }: { name: string }) {
  return (
    <section className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-navy-850/60 p-6 shadow-card backdrop-blur-md sm:p-8">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-surface-sheen"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-brand-violet/20 blur-[90px]"
      />

      <div className="relative flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div className="max-w-xl">
          <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[11px] font-medium text-ink-muted">
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5 text-sky-300" />
            Welcome back
          </span>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
            Let&apos;s find your next role,{" "}
            <span className="brand-gradient-text">{name}</span>
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-ink-muted">
            Your dashboard pulls job recommendations from the Glassdoor alerts
            already in your inbox. Connect Gmail to start; nothing is sent or
            applied to automatically.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <Link href="/jobs" className={buttonClasses({ variant: "primary" })}>
            Explore job discovery
            <ArrowUpRight aria-hidden="true" className="h-4 w-4" />
          </Link>
          <Link
            href="/gmail"
            className={buttonClasses({ variant: "secondary" })}
          >
            Gmail integration
          </Link>
        </div>
      </div>
    </section>
  );
}
