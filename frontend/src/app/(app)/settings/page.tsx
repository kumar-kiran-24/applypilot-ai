import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Mail, ShieldCheck, UserRound } from "lucide-react";

import { SessionPanel } from "@/components/settings/session-panel";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { buttonClasses } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { getCurrentUser, getSessionToken } from "@/lib/auth/session";

export const metadata: Metadata = {
  title: "Settings",
};

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const token = await getSessionToken();
  const user = token ? await getCurrentUser(token) : null;
  if (!user) {
    redirect("/login?expired=1");
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Settings"
        description="Your account, session and connected services."
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Account</CardTitle>
              <CardDescription>
                Created from your Google identity
              </CardDescription>
            </div>
            <Badge variant="brand">
              <UserRound aria-hidden="true" className="h-3 w-3" />
              Google
            </Badge>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-3">
              <Avatar
                name={user.full_name}
                email={user.email}
                src={user.profile_picture}
                size={48}
              />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">
                  {user.full_name}
                </p>
                <p className="truncate text-[12px] text-ink-muted">
                  {user.email}
                </p>
              </div>
            </div>

            <dl className="space-y-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5 text-[12px]">
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-ink-subtle">User ID</dt>
                <dd className="truncate font-mono text-[11px] text-ink-muted">
                  {user.id}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-ink-subtle">Email verified</dt>
                <dd className="text-emerald-300">Yes (Google)</dd>
              </div>
            </dl>

            <p className="text-[11px] leading-relaxed text-ink-subtle">
              Profile editing is not part of Phase 1. Name and photo come from
              your Google account, so update them there.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Session &amp; security</CardTitle>
              <CardDescription>How ApplyPilot keeps your token</CardDescription>
            </div>
            <Badge variant="neutral">
              <ShieldCheck aria-hidden="true" className="h-3 w-3" />
              HttpOnly
            </Badge>
          </CardHeader>
          <CardContent>
            <SessionPanel />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Connected services</CardTitle>
              <CardDescription>
                Read-only integrations authorized by you
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-sky-300">
                  <Mail aria-hidden="true" className="h-4 w-4" />
                </span>
                <div>
                  <p className="text-[13px] font-medium text-ink">Gmail</p>
                  <p className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">
                    Read-only job alert scanning. You can revoke access at any
                    time.
                  </p>
                </div>
              </div>
              <Link
                href="/gmail"
                className={buttonClasses({ variant: "secondary", size: "sm" })}
              >
                Manage Gmail
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
