import { cn } from "@/lib/utils/cn";

type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "brand";

const variants: Record<BadgeVariant, string> = {
  neutral: "border-white/10 bg-white/[0.04] text-ink-muted",
  success: "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
  warning: "border-amber-500/25 bg-amber-500/10 text-amber-300",
  danger: "border-red-500/25 bg-red-500/10 text-red-300",
  brand: "border-brand-blue/30 bg-brand-blue/10 text-sky-300",
};

export function Badge({
  variant = "neutral",
  className,
  children,
}: {
  variant?: BadgeVariant;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium leading-5",
        variants[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
