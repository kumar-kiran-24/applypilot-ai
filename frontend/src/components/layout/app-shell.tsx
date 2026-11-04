"use client";

import { Menu, ShieldCheck, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/layout/logo";
import { SidebarNav } from "@/components/layout/sidebar-nav";
import { UserMenu } from "@/components/layout/user-menu";
import { Badge } from "@/components/ui/badge";
import type { AuthUser } from "@/types/api";

export function AppShell({
  user,
  children,
}: {
  user: AuthUser;
  children: React.ReactNode;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  return (
    <div className="min-h-screen">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded-lg focus:bg-navy-800 focus:px-4 focus:py-2 focus:text-sm focus:text-ink"
      >
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[272px] flex-col border-r border-white/[0.06] bg-navy-950/70 px-4 py-5 backdrop-blur-xl lg:flex">
        <div className="px-2">
          <Logo />
        </div>
        <div className="mt-8 flex-1 overflow-y-auto">
          <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-subtle">
            Workspace
          </p>
          <SidebarNav />
        </div>
        <div className="mt-4 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="h-4 w-4 text-sky-300" />
            <p className="text-[12px] font-medium text-ink">Phase 1</p>
            <Badge variant="brand" className="ml-auto">
              Read-only
            </Badge>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-subtle">
            ApplyPilot never applies to a job without your explicit confirmation.
          </p>
        </div>
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
            className="absolute inset-0 bg-navy-950/80 backdrop-blur-sm"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 flex w-[280px] flex-col border-r border-white/10 bg-navy-900 px-4 py-5 animate-fade-in"
          >
            <div className="flex items-center justify-between px-2">
              <Logo />
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation"
                className="rounded-lg p-1.5 text-ink-muted transition-colors hover:bg-white/[0.06] hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-blue/60"
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
            <div className="mt-7 flex-1 overflow-y-auto">
              <SidebarNav onNavigate={() => setMobileOpen(false)} />
            </div>
          </div>
        </div>
      ) : null}

      <div className="lg:pl-[272px]">
        <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-navy-900/75 backdrop-blur-xl">
          <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                aria-label="Open navigation"
                aria-expanded={mobileOpen}
                className="rounded-lg border border-white/10 bg-white/[0.03] p-2 text-ink-muted transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-brand-blue/60 lg:hidden"
              >
                <Menu aria-hidden="true" className="h-4 w-4" />
              </button>
              <span className="lg:hidden">
                <Logo compact />
              </span>
              <p className="hidden text-[13px] text-ink-muted lg:block">
                Your Intelligent Job Application Agent
              </p>
            </div>
            <UserMenu user={user} />
          </div>
        </header>

        <main id="main-content" className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
          {children}
        </main>

        <footer className="mx-auto max-w-6xl px-4 pb-8 pt-2 text-[11px] text-ink-subtle sm:px-6 lg:px-8">
          <p>
            ApplyPilot AI · Phase 1 dashboard. Gmail access is read-only and tokens are stored
            encrypted by the backend.
          </p>
        </footer>
      </div>
    </div>
  );
}
