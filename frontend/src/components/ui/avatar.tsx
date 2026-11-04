import Image from "next/image";

import { cn } from "@/lib/utils/cn";
import { initials } from "@/lib/utils/format";

export function Avatar({
  name,
  email,
  src,
  size = 36,
  className,
}: {
  name: string;
  email: string;
  src?: string | null;
  size?: number;
  className?: string;
}) {
  const dimension = { width: size, height: size };

  if (src) {
    return (
      <Image
        src={src}
        alt=""
        {...dimension}
        unoptimized
        referrerPolicy="no-referrer"
        className={cn("rounded-full border border-white/15 object-cover", className)}
      />
    );
  }

  return (
    <span
      aria-hidden="true"
      style={dimension}
      className={cn(
        "flex items-center justify-center rounded-full border border-white/10 bg-brand-gradient text-[11px] font-semibold uppercase tracking-wide text-white",
        className,
      )}
    >
      {initials(name, email)}
    </span>
  );
}
