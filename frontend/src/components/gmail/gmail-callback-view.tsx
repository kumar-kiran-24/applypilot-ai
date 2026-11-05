"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle2, Mail, RefreshCw } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClasses } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { useGmailStatus } from "@/features/gmail/hooks";
import { describeError } from "@/lib/api/errors";
import { formatDateTime, humanizeScope } from "@/lib/utils/format";

/**
 * Landing point for `GMAIL_FRONTEND_REDIRECT_URI`. The backend never returns a
 * token here; the connection is confirmed by asking `/gmail/status`.
 */
export function GmailCallbackView() {
  const status = useGmailStatus();
  const connected = status.data?.connected === true;

  return (
    <div className="mx-auto max-w-2xl animate-fade-in">
      <Card>
        <CardHeader>
          <CardTitle>Gmail authorization</CardTitle>
          {status.isPending ? (
            <Badge variant="neutral">Checking…</Badge>
          ) : (
            <Badge variant={connected ? "success" : "warning"}>
              {connected ? "Connected" : "Not connected"}
            </Badge>
          )}
        </CardHeader>
        <CardContent className="space-y-5">
          {status.isPending ? (
            <div className="flex items-center gap-3 py-6">
              <Spinner
                className="h-5 w-5 text-sky-300"
                label="Confirming the connection with the backend…"
              />
            </div>
          ) : status.isError ? (
            <>
              <Alert variant="danger" title="Could not verify the connection">
                {describeError(status.error)}
              </Alert>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void status.refetch()}
              >
                <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
                Check again
              </Button>
            </>
          ) : connected ? (
            <>
              <div className="flex items-start gap-3">
                <CheckCircle2
                  aria-hidden="true"
                  className="mt-0.5 h-6 w-6 text-emerald-300"
                />
                <div>
                  <p className="text-sm font-medium text-ink">
                    Gmail is connected
                  </p>
                  <p className="mt-1 text-[13px] text-ink-muted">
                    ApplyPilot can now read job alert emails with the account
                    below. Access is read-only and the token is stored
                    encrypted.
                  </p>
                </div>
              </div>

              <dl className="grid gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 sm:grid-cols-2">
                <div>
                  <dt className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                    Account
                  </dt>
                  <dd className="mt-1 flex items-center gap-2 text-[13px] text-ink">
                    <Mail aria-hidden="true" className="h-4 w-4 text-sky-300" />
                    <span className="truncate">{status.data.gmail_email}</span>
                  </dd>
                </div>
                <div>
                  <dt className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                    Connected
                  </dt>
                  <dd className="mt-1 text-[13px] text-ink">
                    {formatDateTime(status.data.connected_at)}
                  </dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                    Scopes
                  </dt>
                  <dd className="mt-1.5 flex flex-wrap gap-1.5">
                    {status.data.granted_scopes.map((scope) => (
                      <Badge key={scope} variant="brand">
                        {humanizeScope(scope)}
                      </Badge>
                    ))}
                  </dd>
                </div>
              </dl>

              <div className="flex flex-wrap gap-2">
                <Link href="/jobs" className={buttonClasses({ size: "sm" })}>
                  Discover jobs
                </Link>
                <Link
                  href="/gmail"
                  className={buttonClasses({
                    variant: "secondary",
                    size: "sm",
                  })}
                >
                  Manage connection
                </Link>
              </div>
            </>
          ) : (
            <>
              <Alert variant="warning" title="Authorization did not complete">
                Gmail is not connected for this account. This usually means
                consent was cancelled, a different Google account was chosen,
                read-only access was not granted, or the callback landed on a
                different host than the one that started the flow.
              </Alert>
              <div className="flex items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 text-[12px] leading-relaxed text-ink-muted">
                <AlertTriangle
                  aria-hidden="true"
                  className="mt-0.5 h-4 w-4 shrink-0 text-amber-300"
                />
                <p>
                  Start again from the Gmail page and use the same Google
                  account you signed in with. Keep the app and the API on the
                  same host (127.0.0.1 or localhost, never a mix).
                </p>
              </div>
              <Link href="/gmail" className={buttonClasses({ size: "sm" })}>
                Back to Gmail integration
              </Link>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
