import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils/cn";

export function Spinner({
  className,
  label,
}: {
  className?: string;
  label?: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-2"
      role={label ? "status" : undefined}
    >
      <Loader2
        aria-hidden="true"
        className={cn("h-4 w-4 animate-spin", className)}
      />
      {label ? <span className="text-sm text-ink-muted">{label}</span> : null}
    </span>
  );
}
