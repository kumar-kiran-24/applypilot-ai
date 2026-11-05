"use client";

import Link from "next/link";
import { Mail, PlugZap, RefreshCw } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { buttonClasses } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useConnectGmail, useGmailStatus } from "@/features/gmail/hooks";
import { describeError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/utils/format";

export function GmailStatusCard() {
  const status = useGmailStatus();
  const connect = useConnectGmail();
  const connected = status.data?.connected === true;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Gmail connection</CardTitle>
          <CardDescription>
            Read-only access to job alert emails
          </CardDescription>
        </div>
        {status.isPending ? (
          <Skeleton className="h-5 w-20 rounded-full" />
        ) : (
          <Badge variant={connected ? "success" : "neutral"}>
            <span
              aria-hidden="true"
              className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-slate-400"}`}
            />
            {connected ? "Connected" : "Not connected"}
          </Badge>
        )}
      </CardHeader>

      <CardContent className="space-y-4">
        {status.isPending ? (
          <div className="space-y-3">
            <SkeletonText lines={2} />
            <Skeleton className="h-9 w-32" />
          </div>
        ) : status.isError ? (
          <>
            <Alert variant="danger">{describeError(status.error)}</Alert>
            <button
              type="button"
              onClick={() => void status.refetch()}
              className={buttonClasses({ variant: "secondary", size: "sm" })}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Retry
            </button>
          </>
        ) : connected ? (
          <>
            <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
              <p className="flex items-center gap-2 text-[13px] text-ink">
                <Mail aria-hidden="true" className="h-4 w-4 text-sky-300" />
                <span className="truncate">{status.data.gmail_email}</span>
              </p>
              <p className="mt-1 text-[11px] text-ink-subtle">
                Connected {formatDateTime(status.data.connected_at)}
              </p>
            </div>
            <Link
              href="/gmail"
              className={buttonClasses({
                variant: "secondary",
                size: "sm",
                className: "w-full",
              })}
            >
              Manage connection
            </Link>
          </>
        ) : (
          <>
            <p className="text-[13px] leading-relaxed text-ink-muted">
              Connect Gmail so ApplyPilot can read Glassdoor job alerts.
              Sending, deleting and modifying email is never requested.
            </p>
            {connect.error ? (
              <Alert variant="danger">{describeError(connect.error)}</Alert>
            ) : null}
            <button
              type="button"
              onClick={() => connect.mutate()}
              disabled={connect.isPending}
              className={buttonClasses({
                variant: "primary",
                size: "sm",
                className: "w-full",
              })}
            >
              {connect.isPending ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Opening Google…
                </>
              ) : (
                <>
                  <PlugZap aria-hidden="true" className="h-3.5 w-3.5" />
                  Connect Gmail
                </>
              )}
            </button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
