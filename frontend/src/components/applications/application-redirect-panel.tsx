"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Loader2,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  ShieldX,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

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
import {
  useAnalyzeLiveForm,
  useApplicationWorkflow,
  useDetectApplicationDestination,
} from "@/features/applications/hooks";
import {
  canAnalyzeDestination,
  describeRedirect,
  platformLabel,
  redirectStatusLabel,
  resolveEasyApplyStages,
  smartApplyStepUnsupported,
} from "@/features/applications/redirect-workflow";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { safeExternalUrl } from "@/lib/utils/url";
import type {
  ApplicationRedirectResponse,
  BrowserSession,
} from "@/types/api";

function displayUrl(raw: string | null | undefined): string {
  if (!raw) return "";
  try {
    const url = new URL(raw);
    return `${url.host}${url.pathname || "/"}`;
  } catch {
    return "";
  }
}

function StageIcon({ state }: { state: "done" | "current" | "pending" | "blocked" }) {
  if (state === "done") {
    return <CheckCircle2 aria-hidden="true" className="h-4 w-4 text-emerald-300" />;
  }
  if (state === "current") {
    return <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-sky-300" />;
  }
  if (state === "blocked") {
    return <AlertTriangle aria-hidden="true" className="h-4 w-4 text-amber-300" />;
  }
  return <Circle aria-hidden="true" className="h-3.5 w-3.5 text-ink-subtle" />;
}

export function ApplicationRedirectPanel({
  applicationId,
  session,
  fallbackListingUrl = null,
}: {
  applicationId: string;
  session?: BrowserSession | null;
  fallbackListingUrl?: string | null;
}) {
  const workflow = useApplicationWorkflow(applicationId);
  const detect = useDetectApplicationDestination(applicationId);
  const analyze = useAnalyzeLiveForm(applicationId);
  const [destination, setDestination] =
    useState<ApplicationRedirectResponse | null>(null);

  const detectRef = useRef(detect.mutate);
  detectRef.current = detect.mutate;

  useEffect(() => {
    if (detect.data) setDestination(detect.data);
  }, [detect.data]);

  const validated = Boolean(
    session?.destination_validated || destination?.destination_validated,
  );
  const status =
    destination?.status ?? session?.redirect_status ?? "not_started";
  const sessionOpen = session?.browser_status === "open";
  const shouldPoll =
    sessionOpen && !validated && !["unsupported", "rejected"].includes(status);

  useEffect(() => {
    if (!shouldPoll) return;
    // The backend watches the real navigation; this only asks where the page
    // currently is. It never clicks Easy Apply or changes the page.
    const timer = setTimeout(() => detectRef.current(), 1500);
    const interval = setInterval(() => detectRef.current(), 5000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [shouldPoll, session?.session_id]);

  const notice = describeRedirect({
    session,
    destination,
    hasAnalysis: Boolean(workflow.data?.analysis.analysis_id),
    fillStatus: workflow.data?.execution.execution_status ?? null,
    readyForReview: Boolean(workflow.data?.ready_for_final_review),
  });
  const stages = resolveEasyApplyStages({
    session,
    destination,
    hasAnalysis: Boolean(workflow.data?.analysis.analysis_id),
    fillStatus: workflow.data?.execution.execution_status ?? null,
    readyForReview: Boolean(workflow.data?.ready_for_final_review),
  });

  const platform = platformLabel({ session, destination });
  const listingUrl =
    destination?.listing_url ??
    session?.listing_url ??
    session?.application_url ??
    fallbackListingUrl;
  const formUrl =
    destination?.destination_url ??
    session?.application_destination_url ??
    (validated ? session?.current_url : null);
  const stageUnsupported = smartApplyStepUnsupported({ session, destination });
  const canAnalyze = canAnalyzeDestination({ session, destination });
  const listingLink = safeExternalUrl(listingUrl);

  return (
    <Card id="easy-apply-workflow">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="h-4 w-4 text-sky-300" />
            Easy Apply hand-off
          </CardTitle>
          <CardDescription>
            The listing, the detected application platform and the validated form
            are tracked separately. ApplyPilot never clicks Easy Apply, CAPTCHA,
            sign-in or the final Submit button.
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {platform ? <Badge variant="brand">{platform}</Badge> : null}
          <Badge
            variant={
              status === "validated"
                ? "success"
                : status === "rejected"
                  ? "danger"
                  : status === "unsupported"
                    ? "warning"
                    : "neutral"
            }
          >
            {redirectStatusLabel(status)}
          </Badge>
          {validated ? (
            <Badge variant="success">
              <ShieldCheck aria-hidden="true" className="h-3 w-3" />
              Validated
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {session?.job_title || session?.company ? (
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <p className="text-[13px] font-medium text-ink">
              {session?.job_title || "Selected job"}
            </p>
            <p className="text-[11px] text-ink-subtle">
              {[session?.company, "Easy Apply"]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        ) : null}

        <Alert variant={notice.tone} title={notice.title}>
          {notice.detail}
        </Alert>

        {destination?.warnings.length ? (
          <ul className="list-disc space-y-1 pl-5 text-[11px] text-amber-100/80">
            {destination.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}

        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-2.5">
            <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
              Original job listing
            </p>
            <p className="mt-1 truncate font-mono text-[11px] text-ink-muted">
              {displayUrl(listingUrl) || "Not recorded"}
            </p>
            {listingLink ? (
              <a
                href={listingLink}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-block text-[11px] text-sky-300 hover:underline"
              >
                Open listing
              </a>
            ) : null}
          </div>
          <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-2.5">
            <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
              Application form
            </p>
            <p className="mt-1 truncate font-mono text-[11px] text-ink-muted">
              {displayUrl(formUrl) || "Not detected yet"}
            </p>
          </div>
        </div>

        <ol className="space-y-1.5">
          {stages.map((stage, index) => (
            <li
              key={stage.id}
              className={cn(
                "flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12px]",
                stage.state === "current"
                  ? "bg-sky-500/10 text-sky-100"
                  : stage.state === "blocked"
                    ? "bg-amber-500/10 text-amber-100"
                    : stage.state === "done"
                      ? "text-ink-muted"
                      : "text-ink-subtle",
              )}
            >
              <StageIcon state={stage.state} />
              <span className="w-4 text-[10px] tabular-nums text-ink-subtle">
                {index + 1}
              </span>
              {stage.label}
            </li>
          ))}
        </ol>

        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            disabled={!sessionOpen || detect.isPending}
            onClick={() => detect.mutate()}
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", detect.isPending && "animate-spin")}
            />
            Check destination
          </Button>
          <Button
            size="sm"
            disabled={!canAnalyze || analyze.isPending}
            onClick={() => analyze.mutate()}
          >
            <ScanSearch className="h-3.5 w-3.5" />
            Analyze form
          </Button>
          {!validated && sessionOpen ? (
            <span className="text-[11px] text-ink-subtle">
              Waiting for your Easy Apply click…
            </span>
          ) : null}
        </div>

        {stageUnsupported ? (
          <Alert variant="warning" title="This SmartApply step is not supported yet">
            The destination was validated, but ApplyPilot does not recognise this
            step. Continue in the live browser and review each control yourself.
          </Alert>
        ) : null}

        {detect.error ? (
          <p className="text-[11px] text-red-300">
            <ShieldX aria-hidden="true" className="mr-1 inline h-3 w-3" />
            {describeError(detect.error)}
          </p>
        ) : null}
        {analyze.error ? (
          <p className="text-[11px] text-red-300">
            {describeError(analyze.error)}
          </p>
        ) : null}
        {analyze.isSuccess ? (
          <p className="text-[11px] text-emerald-200">
            Form analysis stored. Review the detected controls before filling.
          </p>
        ) : null}
        <p className="text-[11px] leading-relaxed text-ink-subtle">
          The listing URL is never replaced. Only public destinations on
          recognised application platforms are stored; private, localhost and
          look-alike hosts are blocked. The final submission is always yours.
        </p>
      </CardContent>
    </Card>
  );
}
