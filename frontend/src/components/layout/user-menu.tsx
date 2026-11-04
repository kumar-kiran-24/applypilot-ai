"use client";

import Link from "next/link";
import { ChevronDown, LogOut, Settings } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Avatar } from "@/components/ui/avatar";
import { Spinner } from "@/components/ui/spinner";
import { useLogout } from "@/features/auth/use-logout";
import type { AuthUser } from "@/types/api";

export function UserMenu({ user }: { user: AuthUser }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const logout = useLogout();

  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] py-1.5 pl-1.5 pr-2.5 text-left transition-colors hover:border-white/20 hover:bg-white/[0.06] focus-visible:ring-2 focus-visible:ring-brand-blue/60"
      >
        <Avatar name={user.full_name} email={user.email} src={user.profile_picture} size={28} />
        <span className="hidden min-w-0 sm:block">
          <span className="block max-w-[160px] truncate text-[13px] font-medium leading-tight text-ink">
            {user.full_name}
          </span>
          <span className="block max-w-[160px] truncate text-[11px] leading-tight text-ink-subtle">
            {user.email}
          </span>
        </span>
        <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 text-ink-subtle" />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="Account menu"
          className="absolute right-0 z-50 mt-2 w-64 overflow-hidden rounded-2xl border border-white/10 bg-navy-850/95 shadow-card backdrop-blur-xl animate-fade-in"
        >
          <div className="border-b border-white/[0.06] px-4 py-3">
            <p className="truncate text-sm font-medium text-ink">{user.full_name}</p>
            <p className="truncate text-xs text-ink-muted">{user.email}</p>
          </div>
          <div className="p-1.5">
            <Link
              href="/settings"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] text-ink-muted transition-colors hover:bg-white/[0.06] hover:text-ink"
            >
              <Settings aria-hidden="true" className="h-4 w-4" />
              Settings
            </Link>
            <button
              type="button"
              role="menuitem"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] text-ink-muted transition-colors hover:bg-red-500/10 hover:text-red-300 disabled:opacity-60"
            >
              {logout.isPending ? (
                <Spinner className="h-4 w-4" />
              ) : (
                <LogOut aria-hidden="true" className="h-4 w-4" />
              )}
              Sign out
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
