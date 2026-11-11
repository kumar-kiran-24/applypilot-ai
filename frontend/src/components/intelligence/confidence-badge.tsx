import { Badge } from "@/components/ui/badge";

export type ConfidenceLevel = "high" | "medium" | "low";

const CONFIDENCE: Record<
  ConfidenceLevel,
  { label: string; variant: "success" | "warning" | "neutral" }
> = {
  high: { label: "High confidence", variant: "success" },
  medium: { label: "Medium confidence", variant: "warning" },
  low: { label: "Low confidence", variant: "neutral" },
};

export function isConfidenceLevel(value: string | null): value is ConfidenceLevel {
  return value === "high" || value === "medium" || value === "low";
}

/**
 * Compact indicator for extraction/recommendation confidence. Anything the
 * backend labels with a word this UI does not know is shown as unknown rather
 * than being upgraded to "high".
 */
export function ConfidenceBadge({
  confidence,
  label,
}: {
  confidence: string | null;
  label?: string;
}) {
  const known = confidence && isConfidenceLevel(confidence) ? CONFIDENCE[confidence] : null;
  return (
    <Badge variant={known?.variant ?? "neutral"}>
      {label ?? known?.label ?? "Confidence not reported"}
    </Badge>
  );
}
