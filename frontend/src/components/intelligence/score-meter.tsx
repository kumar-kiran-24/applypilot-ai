import { cn } from "@/lib/utils/cn";

/** Scores are returned as floats; show one decimal only when meaningful. */
export function formatScore(score: number): string {
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

export function scoreVariant(
  score: number,
): "success" | "warning" | "danger" | "brand" {
  if (score >= 75) return "success";
  if (score >= 50) return "warning";
  return "danger";
}

/**
 * One labelled 0-100 score. `null` means the posting gave no evidence for the
 * dimension, so it is reported as "Not evaluated" instead of a zero.
 */
export function ScoreMeter({
  label,
  value,
  className,
}: {
  label: string;
  value: number | null;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12px] text-ink-muted">{label}</span>
        <span className="text-[12px] font-medium text-ink">
          {value === null ? "Not evaluated" : `${value}%`}
        </span>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]"
        role="presentation"
      >
        <div
          className={cn(
            "h-full rounded-full bg-gradient-to-r",
            value === null ? "from-white/10 to-white/10" : "from-brand-indigo to-brand-blue",
          )}
          style={{ width: `${value ?? 0}%` }}
        />
      </div>
    </div>
  );
}
