import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";

import { cn } from "@/lib/utils/cn";

type AlertVariant = "info" | "success" | "warning" | "danger";

const styles: Record<AlertVariant, { container: string; icon: typeof Info }> = {
  info: { container: "border-brand-blue/25 bg-brand-blue/[0.08] text-sky-200", icon: Info },
  success: {
    container: "border-emerald-500/25 bg-emerald-500/[0.08] text-emerald-200",
    icon: CheckCircle2,
  },
  warning: {
    container: "border-amber-500/25 bg-amber-500/[0.08] text-amber-200",
    icon: AlertTriangle,
  },
  danger: { container: "border-red-500/25 bg-red-500/[0.08] text-red-200", icon: XCircle },
};

export function Alert({
  variant = "info",
  title,
  children,
  className,
  role = variant === "danger" ? "alert" : "status",
}: {
  variant?: AlertVariant;
  title?: string;
  children?: React.ReactNode;
  className?: string;
  role?: "alert" | "status";
}) {
  const { container, icon: Icon } = styles[variant];
  return (
    <div
      role={role}
      className={cn("flex gap-3 rounded-xl border px-4 py-3 text-sm", container, className)}
    >
      <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 space-y-1">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? <div className="text-[13px] leading-relaxed opacity-90">{children}</div> : null}
      </div>
    </div>
  );
}
