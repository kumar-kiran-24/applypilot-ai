"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS } from "@/components/layout/nav";
import { cn } from "@/lib/utils/cn";

export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav aria-label="Main navigation" className="space-y-1">
      {NAV_ITEMS.map((item) => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;

        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
              active
                ? "border border-white/10 bg-white/[0.07] text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
                : "border border-transparent text-ink-muted hover:bg-white/[0.04] hover:text-ink",
            )}
          >
            <Icon
              aria-hidden="true"
              className={cn(
                "h-4 w-4 shrink-0 transition-colors",
                active ? "text-sky-300" : "text-ink-subtle group-hover:text-ink-muted",
              )}
            />
            <span className="truncate">{item.label}</span>
            {item.available ? null : (
              <span className="ml-auto rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-ink-subtle">
                Soon
              </span>
            )}
            {active ? (
              <span
                aria-hidden="true"
                className="ml-auto h-1.5 w-1.5 rounded-full bg-brand-gradient"
              />
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}
