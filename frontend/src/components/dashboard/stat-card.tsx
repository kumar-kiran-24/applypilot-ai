import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils/cn";

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "blue",
  loading = false,
}: {
  label: string;
  value: string;
  hint: string;
  icon: LucideIcon;
  tone?: "blue" | "violet" | "emerald" | "slate";
  loading?: boolean;
}) {
  const tones = {
    blue: "text-sky-300 border-sky-400/20 bg-sky-400/10",
    violet: "text-violet-300 border-violet-400/20 bg-violet-400/10",
    emerald: "text-emerald-300 border-emerald-400/20 bg-emerald-400/10",
    slate: "text-ink-muted border-white/10 bg-white/[0.04]",
  } as const;

  return (
    <div className="glass-panel flex items-start gap-3.5 p-4 sm:p-5">
      <span
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border",
          tones[tone],
        )}
      >
        <Icon aria-hidden="true" className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink-subtle">
          {label}
        </p>
        {loading ? (
          <div className="mt-1.5 h-7 w-14 animate-pulse rounded-md bg-white/[0.08]" />
        ) : (
          <p className="mt-1 truncate text-2xl font-semibold leading-none tracking-tight text-ink">
            {value}
          </p>
        )}
        <p className="mt-1.5 text-[11px] leading-snug text-ink-subtle">
          {hint}
        </p>
      </div>
    </div>
  );
}
