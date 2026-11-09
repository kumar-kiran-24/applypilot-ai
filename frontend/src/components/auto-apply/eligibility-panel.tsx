"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ChevronRight,
  FileText,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  DecisionBadge,
  ELIGIBILITY_DECISION,
  ELIGIBILITY_STATUS,
  ReadinessBar,
  StatusBadge,
} from "@/components/auto-apply/eligibility-badges";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClasses } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  useApproveEligibility,
  useEligibilityReport,
  useEvaluateEligibility,
  useWithdrawEligibilityApproval,
} from "@/features/auto-apply/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDateTime } from "@/lib/utils/format";
import type { EligibilityCheck, EligibilityReport } from "@/types/api";

/** Checks that explain the resume and job-preference side of a report. */
const HIGHLIGHT_CHECKS = [
  "resume_suitability",
  "job_preferences",
  "salary_expectations",
  "work_authorization",
] as const;

function SummaryStat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  const tones: Record<string, string> = {
    neutral: "text-ink",
    success: "text-emerald-300",
    warning: "text-amber-300",
    danger: "text-red-300",
  };
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-2.5">
      <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
        {label}
      </p>
      <p className={cn("mt-1 text-base font-semibold", tones[tone])}>{value}</p>
    </div>
  );
}

function CheckItem({ check }: { check: EligibilityCheck }) {
  return (
    <div
      className={cn(
        "rounded-xl border p-3",
        check.blocking
          ? "border-red-500/20 bg-red-500/[0.03]"
          : "border-white/[0.07] bg-white/[0.02]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-[12px] font-medium text-ink">{check.label}</p>
        <div className="flex shrink-0 items-center gap-1.5">
          {check.blocking ? (
            <Badge variant="danger">Blocking</Badge>
          ) : (
            <Badge variant="warning">Warning</Badge>
          )}
          <StatusBadge status={check.status} />
        </div>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
        {check.explanation}
      </p>
      <p className="mt-1 text-[10px] text-ink-subtle">
        Source: {check.source}
      </p>
      {check.suggested_resolution ? (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] leading-relaxed text-sky-200/90">
          <Sparkles aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
          {check.suggested_resolution}
        </p>
      ) : null}
    </div>
  );
}

function HighlightCheck({ check }: { check: EligibilityCheck }) {
  const meta = ELIGIBILITY_STATUS[check.status];
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] font-medium text-ink">{check.label}</p>
        <Badge variant={meta.variant}>{meta.label}</Badge>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
        {check.explanation}
      </p>
      {check.suggested_resolution ? (
        <p className="mt-1.5 text-[11px] leading-relaxed text-sky-200/90">
          {check.suggested_resolution}
        </p>
      ) : null}
    </div>
  );
}

function ReportBody({ report }: { report: EligibilityReport }) {
  const evaluate = useEvaluateEligibility(report.application_id);
  const approve = useApproveEligibility(report.application_id);
  const withdraw = useWithdrawEligibilityApproval(report.application_id);
  const [acknowledged, setAcknowledged] = useState(false);

  useEffect(() => {
    setAcknowledged(false);
  }, [report.report_version]);

  const decision = ELIGIBILITY_DECISION[report.overall_decision];
  const approvalCurrent = report.approval.approved && report.approval.current;
  const approvalStale = report.approval.approved && !report.approval.current;
  const canApprove = report.overall_decision !== "blocked";
  const mutationError = evaluate.error ?? approve.error ?? withdraw.error;
  const approvalPending = approve.isPending || withdraw.isPending;
  const confirmations = report.failed_checks.filter(
    (check) => check.status === "needs_confirmation",
  );
  const highlights = HIGHLIGHT_CHECKS.flatMap((name) => {
    const check = report.checks.find((item) => item.name === name);
    return check ? [check] : [];
  });

  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle>Auto-apply eligibility</CardTitle>
          <CardDescription>
            Deterministic checks run against your profile, resume, answers and
            automation settings. Resume match alone never makes an application
            eligible.
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <DecisionBadge decision={report.overall_decision} />
          <Button
            size="sm"
            variant="secondary"
            disabled={evaluate.isPending}
            onClick={() => evaluate.mutate()}
          >
            {evaluate.isPending ? (
              <>
                <Spinner className="h-3.5 w-3.5" />
                Evaluating…
              </>
            ) : (
              <>
                <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
                Re-evaluate
              </>
            )}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Alert
          variant={
            report.overall_decision === "blocked"
              ? "danger"
              : report.overall_decision === "ready_to_prepare"
                ? "success"
                : "info"
          }
          title={decision.label}
        >
          {decision.description} {report.message}
        </Alert>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {report.resume_selected ? (
              <Badge variant="neutral">
                <FileText aria-hidden="true" className="h-3 w-3" />
                {report.resume_filename ?? "Resume selected"}
              </Badge>
            ) : (
              <Badge variant="warning">No resume selected</Badge>
            )}
            {report.resume_match_score !== null ? (
              <Badge variant="brand">{report.resume_match_score}% resume match</Badge>
            ) : (
              <Badge variant="neutral">Resume match not scored</Badge>
            )}
            {approvalCurrent ? (
              <Badge variant="success">Approved</Badge>
            ) : approvalStale ? (
              <Badge variant="warning">Approval out of date</Badge>
            ) : (
              <Badge variant="warning">Not approved</Badge>
            )}
          </div>
          <span className="text-sm font-semibold text-ink">
            {report.readiness_percentage}% ready
          </span>
        </div>
        <ReadinessBar percent={report.readiness_percentage} label="Application eligibility readiness" />

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <SummaryStat
            label="Blocking"
            value={report.blocking_issues.length}
            tone={report.blocking_issues.length > 0 ? "danger" : "success"}
          />
          <SummaryStat
            label="Warnings"
            value={report.warnings.length}
            tone={report.warnings.length > 0 ? "warning" : "success"}
          />
          <SummaryStat
            label="Passed"
            value={report.passed_checks.length}
            tone="success"
          />
          <SummaryStat label="Checks" value={report.checks.length} />
        </div>

        <p className="text-[11px] text-ink-subtle">
          Evaluated {formatDateTime(report.evaluation_timestamp)} · report version{" "}
          <span className="font-mono">{report.report_version}</span>
        </p>

        {mutationError ? (
          <Alert variant="danger" title="The report could not be updated">
            {describeError(mutationError)}
          </Alert>
        ) : null}

        {report.blocking_issues.length > 0 ? (
          <div className="space-y-2">
            <p className="flex items-center gap-1.5 text-[12px] font-medium text-ink">
              <AlertTriangle
                aria-hidden="true"
                className="h-3.5 w-3.5 text-red-300"
              />
              Why this application is blocked
            </p>
            {report.blocking_issues.map((check) => (
              <CheckItem key={check.name} check={check} />
            ))}
          </div>
        ) : null}

        {report.warnings.length > 0 ? (
          <div className="space-y-2">
            <p className="text-[12px] font-medium text-ink">
              Non-blocking warnings
            </p>
            {report.warnings.map((check) => (
              <CheckItem key={check.name} check={check} />
            ))}
          </div>
        ) : null}

        {confirmations.length > 0 ? (
          <div className="space-y-2">
            <p className="text-[12px] font-medium text-ink">
              Required confirmations
            </p>
            {confirmations.map((check) => (
              <CheckItem key={check.name} check={check} />
            ))}
          </div>
        ) : null}

        {highlights.length > 0 ? (
          <div className="space-y-2">
            <p className="text-[12px] font-medium text-ink">
              Resume and job preference checks
            </p>
            {highlights.map((check) => (
              <HighlightCheck key={check.name} check={check} />
            ))}
          </div>
        ) : null}

        {report.required_user_actions.length > 0 ? (
          <div className="space-y-1.5 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
            <p className="text-[12px] font-medium text-ink">
              Recommended next actions
            </p>
            <ul className="space-y-1.5">
              {report.required_user_actions.map((action) => (
                <li
                  key={action}
                  className="flex items-start gap-1.5 text-[11px] leading-relaxed text-ink-muted"
                >
                  <ChevronRight
                    aria-hidden="true"
                    className="mt-0.5 h-3 w-3 shrink-0 text-sky-300"
                  />
                  {action}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {approvalCurrent ? (
          <div className="space-y-2">
            <Alert variant="success" title="You approved this report">
              Approved {formatDateTime(report.approval.approved_at)} · version{" "}
              <span className="font-mono">{report.approval.report_version}</span>.
              ApplyPilot may prepare this form, but it never submits without a
              separate final review.
            </Alert>
            <Button
              size="sm"
              variant="secondary"
              disabled={approvalPending}
              onClick={() => withdraw.mutate(report.report_version)}
            >
              {withdraw.isPending ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Withdrawing…
                </>
              ) : (
                "Withdraw approval"
              )}
            </Button>
          </div>
        ) : (
          <div className="space-y-2.5 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
            <p className="text-[12px] font-medium text-ink">Explicit approval</p>
            {approvalStale ? (
              <Alert variant="warning" title="The report changed">
                You approved an earlier version. Review this report and approve
                version <span className="font-mono">{report.report_version}</span>.
              </Alert>
            ) : null}
            {!canApprove ? (
              <Alert variant="danger" title="Approval is disabled">
                Resolve the blocking issues and re-evaluate. Approval is never
                recorded for a blocked report.
              </Alert>
            ) : null}
            <label className="flex cursor-pointer items-start gap-2.5 text-[12px] leading-relaxed text-ink-muted">
              <input
                type="checkbox"
                checked={acknowledged}
                disabled={!canApprove}
                onChange={(event) => setAcknowledged(event.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04]"
              />
              I reviewed every check, the selected resume and this exact report
              version.
            </label>
            <Button
              size="sm"
              disabled={!acknowledged || !canApprove || approvalPending}
              onClick={() => approve.mutate(report.report_version)}
            >
              {approve.isPending ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Recording approval…
                </>
              ) : (
                <>
                  <ShieldCheck aria-hidden="true" className="h-3.5 w-3.5" />
                  Approve this report
                </>
              )}
            </Button>
          </div>
        )}

        <Link
          href="/auto-apply"
          className={buttonClasses({ variant: "ghost", size: "sm" })}
        >
          Open auto-apply dashboard
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </CardContent>
    </Card>
  );
}

/**
 * Eligibility summary and approval controls embedded in an application page.
 * Every value comes from the backend report; nothing is derived client-side.
 */
export function ApplicationEligibilityPanel({
  applicationId,
}: {
  applicationId: string;
}) {
  const report = useEligibilityReport(applicationId);

  if (report.isPending) {
    return (
      <Card>
        <CardContent className="space-y-3">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-28 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (report.isError) {
    return (
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Auto-apply eligibility</CardTitle>
            <CardDescription>
              The eligibility report for this application could not be loaded.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <Alert variant="danger" title="Eligibility unavailable">
            {describeError(report.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void report.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  return <ReportBody report={report.data} />;
}
