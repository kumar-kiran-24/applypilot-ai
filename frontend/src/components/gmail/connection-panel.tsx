"use client";

import {
  CheckCircle2,
  Mail,
  PlugZap,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  useConnectGmail,
  useDisconnectGmail,
  useGmailStatus,
} from "@/features/gmail/hooks";
import { describeError } from "@/lib/api/errors";
import { formatDateTime, humanizeScope } from "@/lib/utils/format";

export function ConnectionPanel() {
  const status = useGmailStatus();
  const connect = useConnectGmail();
  const disconnect = useDisconnectGmail();
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const connected = status.data?.connected === true;

  const handleDisconnect = () => {
    setNotice(null);
    disconnect.mutate(undefined, {
      onSuccess: (result) => {
        setConfirming(false);
        setNotice(result.message);
      },
    });
  };

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Gmail connection</CardTitle>
          <CardDescription>
            Incremental, read-only authorization — separate from Google sign-in
          </CardDescription>
        </div>
        {status.isPending ? (
          <Skeleton className="h-5 w-24 rounded-full" />
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

      <CardContent className="space-y-5">
        {status.isPending ? (
          <div className="space-y-3">
            <SkeletonText lines={2} />
            <Skeleton className="h-9 w-40" />
          </div>
        ) : status.isError ? (
          <>
            <Alert variant="danger" title="Could not load Gmail status">
              {describeError(status.error)}
            </Alert>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void status.refetch()}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Retry
            </Button>
          </>
        ) : connected ? (
          <>
            <div className="grid gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 sm:grid-cols-2">
              <div>
                <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                  Gmail account
                </p>
                <p className="mt-1 flex items-center gap-2 text-[13px] text-ink">
                  <Mail aria-hidden="true" className="h-4 w-4 text-sky-300" />
                  <span className="truncate">{status.data.gmail_email}</span>
                </p>
              </div>
              <div>
                <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                  Connected
                </p>
                <p className="mt-1 text-[13px] text-ink">
                  {formatDateTime(status.data.connected_at)}
                </p>
              </div>
              <div className="sm:col-span-2">
                <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                  Granted scopes
                </p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {status.data.granted_scopes.length > 0 ? (
                    status.data.granted_scopes.map((scope) => (
                      <Badge key={scope} variant="brand">
                        {humanizeScope(scope)}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-[12px] text-ink-subtle">
                      No scopes reported
                    </span>
                  )}
                </div>
              </div>
            </div>

            {connect.error ? (
              <Alert variant="danger">{describeError(connect.error)}</Alert>
            ) : null}
            {disconnect.error ? (
              <Alert variant="danger">{describeError(disconnect.error)}</Alert>
            ) : null}
            {notice ? (
              <Alert
                variant={
                  disconnect.data?.token_revoked === false
                    ? "warning"
                    : "success"
                }
                title="Gmail disconnected"
              >
                {notice}
              </Alert>
            ) : null}

            {confirming ? (
              <div className="rounded-xl border border-red-500/25 bg-red-500/[0.06] p-4">
                <p className="text-[13px] font-medium text-red-200">
                  Disconnect Gmail?
                </p>
                <p className="mt-1 text-[12px] leading-relaxed text-red-200/80">
                  ApplyPilot will revoke its access token at Google where
                  possible and delete the encrypted credential. Job discovery
                  stops until you reconnect.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={handleDisconnect}
                    disabled={disconnect.isPending}
                  >
                    {disconnect.isPending ? (
                      <>
                        <Spinner className="h-3.5 w-3.5" />
                        Disconnecting…
                      </>
                    ) : (
                      <>
                        <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                        Yes, disconnect
                      </>
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirming(false)}
                    disabled={disconnect.isPending}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => connect.mutate()}
                  disabled={connect.isPending}
                >
                  {connect.isPending ? (
                    <>
                      <Spinner className="h-3.5 w-3.5" />
                      Opening Google…
                    </>
                  ) : (
                    <>
                      <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
                      Reconnect
                    </>
                  )}
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setConfirming(true)}
                >
                  <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                  Disconnect
                </Button>
              </div>
            )}
          </>
        ) : (
          <>
            <div className="flex items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
              <ShieldCheck
                aria-hidden="true"
                className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300"
              />
              <div className="space-y-1.5 text-[12px] leading-relaxed text-ink-muted">
                <p className="text-[13px] font-medium text-ink">
                  Read-only by design
                </p>
                <p>
                  ApplyPilot requests{" "}
                  <code className="text-ink">gmail.readonly</code> plus basic
                  profile scopes. It never sends, deletes or modifies email, and
                  the refresh token is encrypted by the backend.
                </p>
                <p className="flex items-center gap-1.5 text-ink-subtle">
                  <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
                  Use the same Google account you signed in with.
                </p>
              </div>
            </div>

            {connect.error ? (
              <Alert variant="danger">{describeError(connect.error)}</Alert>
            ) : null}

            <Button
              onClick={() => connect.mutate()}
              disabled={connect.isPending}
              className="w-full sm:w-auto"
            >
              {connect.isPending ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Opening Google…
                </>
              ) : (
                <>
                  <PlugZap aria-hidden="true" className="h-4 w-4" />
                  Connect Gmail
                </>
              )}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
