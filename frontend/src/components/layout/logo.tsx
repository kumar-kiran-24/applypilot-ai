import { Send } from "lucide-react";

import { cn } from "@/lib/utils/cn";

export function Logo({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand-gradient shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)]">
        <Send aria-hidden="true" className="h-4 w-4 text-white" />
      </span>
      {compact ? null : (
        <span className="flex flex-col leading-none">
          <span className="text-[15px] font-semibold tracking-tight text-ink">
            ApplyPilot <span className="brand-gradient-text">AI</span>
          </span>
          <span className="mt-1 text-[10px] uppercase tracking-[0.16em] text-ink-subtle">
            Job application agent
          </span>
        </span>
      )}
    </span>
  );
}
