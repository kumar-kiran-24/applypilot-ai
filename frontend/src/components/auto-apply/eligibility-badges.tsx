import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils/cn";
import type { EligibilityDecision, EligibilityStatus } from "@/types/api";

type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "brand";

export const ELIGIBILITY_DECISION: Record<
  EligibilityDecision,
  { label: string; variant: BadgeVariant; description: string }
> = {
  ready_to_prepare: {
    label: "Ready to prepare",
    variant: "success",
    description:
      "Every check passed and you approved this exact report. ApplyPilot may prepare the employer's form.",
  },
  ready_for_user_review: {
    label: "Awaiting your review",
    variant: "brand",
    description:
      "No blocking issue remains, but you must review and approve this report before preparation.",
  },
  blocked: {
    label: "Blocked",
    variant: "danger",
    description:
      "A blocking issue prevents preparation. Resolve it before this application can proceed.",
  },
};

export const ELIGIBILITY_STATUS: Record<
  EligibilityStatus,
  { label: string; variant: BadgeVariant }
> = {
  eligible: { label: "Passed", variant: "success" },
  needs_information: { label: "Needs information", variant: "warning" },
  needs_confirmation: { label: "Needs confirmation", variant: "warning" },
  needs_user_approval: { label: "Needs your approval", variant: "brand" },
  ineligible: { label: "Ineligible", variant: "danger" },
  duplicate: { label: "Duplicate", variant: "danger" },
  already_applied: { label: "Already applied", variant: "neutral" },
  unsupported_platform: { label: "Unsupported platform", variant: "danger" },
  failed: { label: "Failed", variant: "danger" },
};

export function DecisionBadge({
  decision,
}: {
  decision: EligibilityDecision;
}) {
  const meta = ELIGIBILITY_DECISION[decision];
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

export function StatusBadge({ status }: { status: EligibilityStatus }) {
  const meta = ELIGIBILITY_STATUS[status];
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

export function ReadinessBar({
  percent,
  label = "Eligibility readiness",
}: {
  percent: number;
  label?: string;
}) {
  return (
    <div
      className="h-2 overflow-hidden rounded-full bg-white/[0.06]"
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div
        className={cn(
          "h-full rounded-full transition-all",
          percent >= 80
            ? "bg-gradient-to-r from-emerald-500 to-emerald-300"
            : percent >= 50
              ? "bg-gradient-to-r from-amber-500 to-amber-300"
              : "bg-gradient-to-r from-red-500 to-red-300",
        )}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
