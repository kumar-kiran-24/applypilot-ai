"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  FileText,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  DecisionBadge,
  ELIGIBILITY_DECISION,
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
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  useApproveEligibility,
  useAutoApplySettings,
  useEligibilityList,
  useEligibilityReport,
  useEvaluateEligibility,
  useUpdateAutoApplySettings,
  useWithdrawEligibilityApproval,
} from "@/features/auto-apply/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDateTime } from "@/lib/utils/format";
import type {
  AutoApplySettings,
  EligibilityCheck,
  EligibilityReport,
  EligibilitySummary,
  EmploymentType,
  RelocationPreference,
  WorkMode,
} from "@/types/api";

const WORK_MODES: Array<{ value: WorkMode; label: string }> = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];

const EMPLOYMENT_TYPES: Array<{ value: EmploymentType; label: string }> = [
  { value: "full_time", label: "Full-time" },
  { value: "part_time", label: "Part-time" },
  { value: "contract", label: "Contract" },
  { value: "internship", label: "Internship" },
  { value: "freelance", label: "Freelance" },
  { value: "temporary", label: "Temporary" },
];

const inputClass =
  "w-full rounded-xl border border-white/10 bg-navy-950/60 px-3 py-2 text-[13px] text-ink placeholder:text-ink-subtle transition-colors focus:border-brand-blue/60 focus:outline-none focus:ring-2 focus:ring-brand-blue/25 disabled:cursor-not-allowed disabled:opacity-60";

const labelClass =
  "text-[11px] font-medium uppercase tracking-[0.08em] text-ink-subtle";

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "success" | "warning" | "danger" | "brand";
}) {
  const tones: Record<string, string> = {
    neutral: "text-ink",
    success: "text-emerald-300",
    warning: "text-amber-300",
    danger: "text-red-300",
    brand: "text-sky-300",
  };
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
      <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
        {label}
      </p>
      <p className={cn("mt-1 text-lg font-semibold", tones[tone])}>{value}</p>
    </div>
  );
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function toggleValue<T>(values: T[], value: T): T[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function SettingsEditor({ settings }: { settings: AutoApplySettings }) {
  const update = useUpdateAutoApplySettings();
  const [minimumMatch, setMinimumMatch] = useState(
    String(settings.minimum_resume_match_percentage),
  );
  const [maxPerDay, setMaxPerDay] = useState(
    String(settings.max_applications_per_day),
  );
  const [preferredTitles, setPreferredTitles] = useState(
    settings.preferred_job_titles.join(", "),
  );
  const [excludedTitles, setExcludedTitles] = useState(
    settings.excluded_job_titles.join(", "),
  );
  const [preferredCompanies, setPreferredCompanies] = useState(
    settings.preferred_companies.join(", "),
  );
  const [excludedCompanies, setExcludedCompanies] = useState(
    settings.excluded_companies.join(", "),
  );
  const [preferredLocations, setPreferredLocations] = useState(
    settings.preferred_locations.join(", "),
  );
  const [workModes, setWorkModes] = useState<WorkMode[]>(
    settings.work_modes as WorkMode[],
  );
  const [employmentTypes, setEmploymentTypes] = useState<EmploymentType[]>(
    settings.employment_types as EmploymentType[],
  );
  const [relocation, setRelocation] = useState<RelocationPreference>(
    settings.relocation_willingness,
  );
  const [minimumSalary, setMinimumSalary] = useState(
    settings.minimum_salary === null ? "" : String(settings.minimum_salary),
  );
  const [maximumSalary, setMaximumSalary] = useState(
    settings.maximum_salary === null ? "" : String(settings.maximum_salary),
  );
  const [currency, setCurrency] = useState(settings.currency);
  const [requireApproval, setRequireApproval] = useState(
    settings.require_user_approval,
  );
  const [requireReview, setRequireReview] = useState(
    settings.require_user_review,
  );

  useEffect(() => {
    setMinimumMatch(String(settings.minimum_resume_match_percentage));
    setMaxPerDay(String(settings.max_applications_per_day));
    setPreferredTitles(settings.preferred_job_titles.join(", "));
    setExcludedTitles(settings.excluded_job_titles.join(", "));
    setPreferredCompanies(settings.preferred_companies.join(", "));
    setExcludedCompanies(settings.excluded_companies.join(", "));
    setPreferredLocations(settings.preferred_locations.join(", "));
    setWorkModes(settings.work_modes as WorkMode[]);
    setEmploymentTypes(settings.employment_types as EmploymentType[]);
    setRelocation(settings.relocation_willingness);
    setMinimumSalary(
      settings.minimum_salary === null ? "" : String(settings.minimum_salary),
    );
    setMaximumSalary(
      settings.maximum_salary === null ? "" : String(settings.maximum_salary),
    );
    setCurrency(settings.currency);
    setRequireApproval(settings.require_user_approval);
    setRequireReview(settings.require_user_review);
  }, [settings]);

  const save = () => {
    update.mutate({
      minimum_resume_match_percentage: Number(minimumMatch),
      max_applications_per_day: Number(maxPerDay),
      preferred_job_titles: splitList(preferredTitles),
      excluded_job_titles: splitList(excludedTitles),
      preferred_companies: splitList(preferredCompanies),
      excluded_companies: splitList(excludedCompanies),
      preferred_locations: splitList(preferredLocations),
      work_modes: workModes,
      employment_types: employmentTypes,
      relocation_willingness: relocation,
      minimum_salary: minimumSalary.trim() ? minimumSalary.trim() : null,
      maximum_salary: maximumSalary.trim() ? maximumSalary.trim() : null,
      currency: currency.trim().toUpperCase(),
      require_user_approval: requireApproval,
      require_user_review: requireReview,
    });
  };

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Automation preferences</CardTitle>
          <CardDescription>
            Job targeting and safety limits. These never overwrite your existing
            job-search preferences.
          </CardDescription>
        </div>
        <Badge variant="neutral">Phase 2D</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1">
            <span className={labelClass}>Minimum resume match %</span>
            <input
              type="number"
              min={0}
              max={100}
              value={minimumMatch}
              onChange={(event) => setMinimumMatch(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="space-y-1">
            <span className={labelClass}>Max applications / day</span>
            <input
              type="number"
              min={1}
              max={50}
              value={maxPerDay}
              onChange={(event) => setMaxPerDay(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="space-y-1">
            <span className={labelClass}>Currency</span>
            <input
              value={currency}
              maxLength={3}
              onChange={(event) => setCurrency(event.target.value)}
              className={inputClass}
            />
          </label>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1">
            <span className={labelClass}>Preferred job titles</span>
            <input
              value={preferredTitles}
              onChange={(event) => setPreferredTitles(event.target.value)}
              placeholder="Backend Engineer, Platform Engineer"
              className={inputClass}
            />
          </label>
          <label className="space-y-1">
            <span className={labelClass}>Excluded job titles</span>
            <input
              value={excludedTitles}
              onChange={(event) => setExcludedTitles(event.target.value)}
              placeholder="Sales, Recruiter"
              className={inputClass}
            />
          </label>
          <label className="space-y-1">
            <span className={labelClass}>Preferred companies</span>
            <input
              value={preferredCompanies}
              onChange={(event) => setPreferredCompanies(event.target.value)}
              placeholder="Acme, Globex"
              className={inputClass}
            />
          </label>
          <label className="space-y-1">
            <span className={labelClass}>Excluded companies</span>
            <input
              value={excludedCompanies}
              onChange={(event) => setExcludedCompanies(event.target.value)}
              placeholder="Company you never want to apply to"
              className={inputClass}
            />
          </label>
          <label className="space-y-1 sm:col-span-2">
            <span className={labelClass}>Preferred locations</span>
            <input
              value={preferredLocations}
              onChange={(event) => setPreferredLocations(event.target.value)}
              placeholder="Austin, Remote"
              className={inputClass}
            />
          </label>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <p className={labelClass}>Work modes</p>
            <div className="flex flex-wrap gap-1.5">
              {WORK_MODES.map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  onClick={() => setWorkModes(toggleValue(workModes, mode.value))}
                  className={cn(
                    "rounded-full border px-3 py-1 text-[11px] transition-colors",
                    workModes.includes(mode.value)
                      ? "border-brand-blue/40 bg-brand-blue/15 text-sky-200"
                      : "border-white/10 bg-white/[0.03] text-ink-muted hover:border-white/20",
                  )}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <p className={labelClass}>Employment types</p>
            <div className="flex flex-wrap gap-1.5">
              {EMPLOYMENT_TYPES.map((type) => (
                <button
                  key={type.value}
                  type="button"
                  onClick={() =>
                    setEmploymentTypes(toggleValue(employmentTypes, type.value))
                  }
                  className={cn(
                    "rounded-full border px-3 py-1 text-[11px] transition-colors",
                    employmentTypes.includes(type.value)
                      ? "border-brand-blue/40 bg-brand-blue/15 text-sky-200"
                      : "border-white/10 bg-white/[0.03] text-ink-muted hover:border-white/20",
                  )}
                >
                  {type.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1">
            <span className={labelClass}>Minimum salary</span>
            <input
              value={minimumSalary}
              onChange={(event) => setMinimumSalary(event.target.value)}
              placeholder="Not set"
              className={inputClass}
            />
          </label>
          <label className="space-y-1">
            <span className={labelClass}>Maximum salary</span>
            <input
              value={maximumSalary}
              onChange={(event) => setMaximumSalary(event.target.value)}
              placeholder="Not set"
              className={inputClass}
            />
          </label>
          <label className="space-y-1">
            <span className={labelClass}>Relocation willingness</span>
            <select
              value={relocation}
              onChange={(event) =>
                setRelocation(event.target.value as RelocationPreference)
              }
              className={inputClass}
            >
              <option value="unknown">Not specified</option>
              <option value="yes">Willing to relocate</option>
              <option value="negotiable">Negotiable</option>
              <option value="no">Not willing</option>
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-4 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
          <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink-muted">
            <input
              type="checkbox"
              checked={requireApproval}
              onChange={(event) => setRequireApproval(event.target.checked)}
              className="h-4 w-4 rounded border-white/20 bg-white/[0.04]"
            />
            Require my approval before opening an employer application
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink-muted">
            <input
              type="checkbox"
              checked={requireReview}
              onChange={(event) => setRequireReview(event.target.checked)}
              className="h-4 w-4 rounded border-white/20 bg-white/[0.04]"
            />
            Require my review before any final submission
          </label>
        </div>

        {update.isError ? (
          <Alert variant="danger" title="Settings could not be saved">
            {describeError(update.error)}
          </Alert>
        ) : null}
        {update.isSuccess && !update.isPending ? (
          <Alert variant="success">Automation preferences saved.</Alert>
        ) : null}

        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] text-ink-subtle">
            Salary and relocation stay empty until you set them. ApplyPilot never
            invents values.
          </p>
          <Button size="sm" disabled={update.isPending} onClick={save}>
            {update.isPending ? (
              <>
                <Spinner className="h-3.5 w-3.5" />
                Saving…
              </>
            ) : (
              "Save preferences"
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ApplicationRow({
  summary,
  active,
  onSelect,
}: {
  summary: EligibilitySummary;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "w-full min-w-[240px] rounded-xl border px-3 py-3 text-left transition-colors lg:min-w-0",
        active
          ? "border-brand-blue/35 bg-brand-blue/[0.09]"
          : "border-white/[0.07] bg-white/[0.02] hover:border-white/[0.15]",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12px] font-medium text-ink">
            {summary.job_title}
          </p>
          <p className="mt-0.5 truncate text-[11px] text-ink-subtle">
            {summary.company ?? "Company not listed"} · {summary.platform_name}
          </p>
        </div>
        <DecisionBadge decision={summary.overall_decision} />
      </div>
      <div className="mt-2.5 flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full bg-gradient-to-r from-brand-indigo to-brand-blue"
            style={{ width: `${summary.readiness_percentage}%` }}
          />
        </div>
        <span className="text-[10px] font-medium text-ink-subtle">
          {summary.readiness_percentage}%
        </span>
      </div>
      <div className="mt-1.5 space-y-0.5 text-[10px] text-ink-subtle">
        <p className="truncate">
          <FileText aria-hidden="true" className="mr-1 inline h-3 w-3" />
          {summary.resume_filename ?? "No resume selected"}
          {summary.resume_match_score !== null
            ? ` · ${summary.resume_match_score}% match`
            : " · match not scored"}
        </p>
        <p>
          {summary.blocking_count > 0
            ? `${summary.blocking_count} blocking issue${summary.blocking_count === 1 ? "" : "s"}`
            : summary.approved
              ? "Approved report"
              : "No blocking issues"}
          {` · ${summary.warning_count} warning${summary.warning_count === 1 ? "" : "s"}`}
        </p>
        {summary.next_action ? (
          <p className="line-clamp-2 text-sky-200/80">
            Next: {summary.next_action}
          </p>
        ) : null}
        <p>Evaluated {formatDateTime(summary.evaluation_timestamp)}</p>
      </div>
    </button>
  );
}

function CheckRow({ check }: { check: EligibilityCheck }) {
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
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-ink">{check.label}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
            {check.explanation}
          </p>
          <p className="mt-1 text-[10px] text-ink-subtle">
            Evidence: {check.source}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {check.blocking ? (
            <Badge variant="danger">Blocking</Badge>
          ) : (
            <Badge variant="warning">Warning</Badge>
          )}
          <StatusBadge status={check.status} />
        </div>
      </div>
      {check.suggested_resolution ? (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] leading-relaxed text-sky-200/90">
          <Sparkles aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
          {check.suggested_resolution}
        </p>
      ) : null}
    </div>
  );
}

function EligibilityDetail({ applicationId }: { applicationId: string }) {
  const report = useEligibilityReport(applicationId);
  const evaluate = useEvaluateEligibility(applicationId);
  const approve = useApproveEligibility(applicationId);
  const withdraw = useWithdrawEligibilityApproval(applicationId);
  const [acknowledged, setAcknowledged] = useState(false);
  const [showPassed, setShowPassed] = useState(false);

  useEffect(() => {
    setAcknowledged(false);
  }, [report.data?.report_version, applicationId]);

  if (report.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (report.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load this eligibility report">
            {describeError(report.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            className="mt-4"
            onClick={() => void report.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  const data: EligibilityReport = report.data;
  const decision = ELIGIBILITY_DECISION[data.overall_decision];
  const mutationError =
    evaluate.error ?? approve.error ?? withdraw.error ?? null;
  const approvalPending = approve.isPending || withdraw.isPending;
  const canApprove = data.overall_decision !== "blocked";
  const approvalStale = data.approval.approved && !data.approval.current;
  const confirmations = data.failed_checks.filter(
    (check) => check.status === "needs_confirmation",
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>{data.job_title}</CardTitle>
            <CardDescription>
              {data.company ?? "Company not listed"} · {data.platform_name} ·{" "}
              {data.application_status.replaceAll("_", " ")}
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <DecisionBadge decision={data.overall_decision} />
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
            <Link
              href={`/applications/${data.application_id}`}
              className={buttonClasses({ variant: "ghost", size: "sm" })}
            >
              Open application
              <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
            </Link>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert
            variant={
              data.overall_decision === "blocked"
                ? "danger"
                : data.overall_decision === "ready_to_prepare"
                  ? "success"
                  : "info"
            }
            title={decision.label}
          >
            {decision.description} {data.message}
          </Alert>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              {data.resume_selected ? (
                <Badge variant="neutral">
                  {data.resume_filename ?? "Resume selected"}
                </Badge>
              ) : (
                <Badge variant="warning">No resume selected</Badge>
              )}
              {data.resume_match_score !== null ? (
                <Badge variant="brand">{data.resume_match_score}% match</Badge>
              ) : (
                <Badge variant="neutral">Match not scored</Badge>
              )}
              {data.approval.approved && data.approval.current ? (
                <Badge variant="success">Approved</Badge>
              ) : approvalStale ? (
                <Badge variant="warning">Approval out of date</Badge>
              ) : (
                <Badge variant="warning">Not approved</Badge>
              )}
            </div>
            <span className="text-sm font-semibold text-ink">
              {data.readiness_percentage}% ready
            </span>
          </div>
          <ReadinessBar percent={data.readiness_percentage} />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat
              label="Blocking"
              value={data.blocking_issues.length}
              tone={data.blocking_issues.length > 0 ? "danger" : "success"}
            />
            <Stat
              label="Warnings"
              value={data.warnings.length}
              tone={data.warnings.length > 0 ? "warning" : "success"}
            />
            <Stat
              label="Passed"
              value={data.passed_checks.length}
              tone="success"
            />
            <Stat label="Checks" value={data.checks.length} />
          </div>
          <p className="text-[11px] text-ink-subtle">
            Evaluated {formatDateTime(data.evaluation_timestamp)} · report version{" "}
            <span className="font-mono">{data.report_version}</span>
          </p>
        </CardContent>
      </Card>

      {data.blocking_issues.length > 0 ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Blocking issues</CardTitle>
              <CardDescription>
                This application is not ready. Resolve every item below.
              </CardDescription>
            </div>
            <Badge variant="danger">{data.blocking_issues.length}</Badge>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.blocking_issues.map((check) => (
              <CheckRow key={check.name} check={check} />
            ))}
          </CardContent>
        </Card>
      ) : null}

      {data.warnings.length > 0 ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Warnings</CardTitle>
              <CardDescription>
                These do not block preparation, but review them before approving.
              </CardDescription>
            </div>
            <Badge variant="warning">{data.warnings.length}</Badge>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.warnings.map((check) => (
              <CheckRow key={check.name} check={check} />
            ))}
          </CardContent>
        </Card>
      ) : null}

      {confirmations.length > 0 ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Confirmations needed</CardTitle>
              <CardDescription>
                These answers or declarations need your explicit confirmation
                before this application can be prepared.
              </CardDescription>
            </div>
            <Badge variant="warning">
              {confirmations.length}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-2">
            {confirmations.map((check) => (
              <CheckRow key={`confirmation-${check.name}`} check={check} />
            ))}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Explicit approval</CardTitle>
            <CardDescription>
              Approval binds to the exact report version above. Any change to the
              application, resume, answers or settings requires a new approval.
            </CardDescription>
          </div>
          {data.approval.approved && data.approval.current ? (
            <Badge variant="success">Approved</Badge>
          ) : (
            <Badge variant="warning">Required</Badge>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {mutationError ? (
            <Alert variant="danger" title="The report could not be updated">
              {describeError(mutationError)}
            </Alert>
          ) : null}

          {data.approval.approved && data.approval.current ? (
            <>
              <Alert variant="success" title="You approved this report">
                Approved {formatDateTime(data.approval.approved_at)} · version{" "}
                <span className="font-mono">
                  {data.approval.report_version}
                </span>
                . ApplyPilot may prepare this form, but it will never submit it
                without a separate final review.
              </Alert>
              <Button
                variant="secondary"
                size="sm"
                disabled={approvalPending}
                onClick={() => withdraw.mutate(data.report_version)}
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
            </>
          ) : (
            <>
              {approvalStale ? (
                <Alert variant="warning" title="The report changed">
                  You approved an earlier version. Review this report and approve
                  version{" "}
                  <span className="font-mono">{data.report_version}</span>.
                </Alert>
              ) : null}
              {!canApprove ? (
                <Alert variant="danger" title="Approval is disabled">
                  Resolve the blocking issues above and re-evaluate. Approval is
                  never recorded for a blocked report.
                </Alert>
              ) : null}
              <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5 text-[12px] leading-relaxed text-ink-muted">
                <input
                  type="checkbox"
                  checked={acknowledged}
                  disabled={!canApprove}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04]"
                />
                I reviewed every check, the selected resume and the answer
                results in this exact report version.
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={!acknowledged || !canApprove || approvalPending}
                  onClick={() => approve.mutate(data.report_version)}
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
                <span className="text-[11px] text-ink-subtle">
                  Version{" "}
                  <span className="font-mono">{data.report_version}</span>
                </span>
              </div>
            </>
          )}

          {data.require_user_review ? (
            <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-ink-subtle">
              <Lock aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
              Your settings also require a final review before submission. Phase 2D
              never submits an application.
            </p>
          ) : null}
        </CardContent>
      </Card>

      {data.required_user_actions.length > 0 ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Recommended next actions</CardTitle>
              <CardDescription>
                The shortest safe path to making this application ready.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {data.required_user_actions.map((action) => (
                <li
                  key={action}
                  className="flex items-start gap-2 text-[12px] leading-relaxed text-ink-muted"
                >
                  <ChevronRight
                    aria-hidden="true"
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-300"
                  />
                  {action}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>All checks</CardTitle>
            <CardDescription>
              Deterministic results with their evidence source. Passed checks can
              be expanded.
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowPassed((value) => !value)}
          >
            {showPassed ? "Hide passed" : "Show passed"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.failed_checks
            .filter((check) => !check.blocking)
            .map((check) => (
              <CheckRow key={`failed-${check.name}`} check={check} />
            ))}
          {showPassed
            ? data.passed_checks.map((check) => (
                <div
                  key={check.name}
                  className="flex items-start justify-between gap-2 rounded-xl border border-emerald-500/15 bg-emerald-500/[0.03] px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="text-[12px] font-medium text-ink">
                      {check.label}
                    </p>
                    <p className="mt-0.5 text-[11px] text-ink-muted">
                      {check.explanation}
                    </p>
                  </div>
                  <CheckCircle2
                    aria-hidden="true"
                    className="h-4 w-4 shrink-0 text-emerald-300"
                  />
                </div>
              ))
            : null}
          {!showPassed && data.passed_checks.length > 0 ? (
            <p className="text-[11px] text-ink-subtle">
              {data.passed_checks.length} checks passed.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

export function AutoApplyDashboard() {
  const list = useEligibilityList();
  const settings = useAutoApplySettings();
  const updateSettings = useUpdateAutoApplySettings();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmEnable, setConfirmEnable] = useState(false);

  useEffect(() => {
    if (!list.data) {
      return;
    }
    const exists = list.data.applications.some(
      (item) => item.application_id === selectedId,
    );
    if (!exists) {
      setSelectedId(list.data.applications[0]?.application_id ?? null);
    }
  }, [list.data, selectedId]);

  if (list.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (list.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not evaluate auto-apply eligibility">
            {describeError(list.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            className="mt-4"
            onClick={() => void list.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  const overview = list.data.overview;
  const selected =
    list.data.applications.find(
      (item) => item.application_id === selectedId,
    ) ?? list.data.applications[0];
  const automationEnabled = settings.data?.automation_enabled ?? false;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Applications" value={overview.total} />
        <Stat
          label="Ready to prepare"
          value={overview.ready_to_prepare}
          tone="success"
        />
        <Stat
          label="Awaiting review"
          value={overview.ready_for_user_review}
          tone="brand"
        />
        <Stat label="Blocked" value={overview.blocked} tone="danger" />
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Needs info"
          value={overview.needs_information}
          tone="warning"
        />
        <Stat
          label="Needs confirmation"
          value={overview.needs_confirmation}
          tone="warning"
        />
        <Stat
          label="Needs approval"
          value={overview.needs_user_approval}
          tone="brand"
        />
        <Stat label="Ineligible" value={overview.ineligible} tone="danger" />
        <Stat label="Duplicates" value={overview.duplicate} tone="danger" />
        <Stat
          label="Already applied"
          value={overview.already_applied}
          tone="neutral"
        />
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Auto-apply master switch</CardTitle>
            <CardDescription>
              Disabled by default. Enabling it only permits preparation; it never
              submits an application in this phase.
            </CardDescription>
          </div>
          <Badge variant={automationEnabled ? "success" : "warning"}>
            {automationEnabled ? "Automation enabled" : "Automation disabled"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-3">
            <input
              type="checkbox"
              checked={automationEnabled}
              disabled={settings.isPending || updateSettings.isPending}
              onChange={(event) => {
                if (event.target.checked) {
                  // Enabling automation is never a single click.
                  setConfirmEnable(true);
                } else {
                  setConfirmEnable(false);
                  updateSettings.mutate({ automation_enabled: false });
                }
              }}
              className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04]"
            />
            <span className="text-[12px] leading-relaxed text-ink-muted">
              Allow ApplyPilot to prepare applications that pass every eligibility
              check and that I explicitly approved. I remain responsible for
              final submission.
            </span>
          </label>
          {settings.data ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge
                variant={
                  settings.data.require_user_approval ? "brand" : "warning"
                }
              >
                {settings.data.require_user_approval
                  ? "User approval required"
                  : "Approval not required"}
              </Badge>
              <Badge
                variant={settings.data.require_user_review ? "brand" : "warning"}
              >
                {settings.data.require_user_review
                  ? "Final review required"
                  : "Final review not required"}
              </Badge>
              <Badge variant="neutral">
                {settings.data.max_applications_per_day} applications / day
              </Badge>
            </div>
          ) : null}

          {confirmEnable && !automationEnabled ? (
            <div className="space-y-2.5 rounded-xl border border-amber-500/25 bg-amber-500/[0.05] p-3">
              <p className="text-[12px] font-medium text-amber-100">
                Confirm enabling automation
              </p>
              <p className="text-[11px] leading-relaxed text-amber-200/80">
                ApplyPilot will be allowed to prepare forms for applications that
                pass every eligibility check and that you explicitly approved. It
                still never submits an application, and you can pause automation
                at any time.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={updateSettings.isPending}
                  onClick={() =>
                    updateSettings.mutate(
                      { automation_enabled: true },
                      { onSuccess: () => setConfirmEnable(false) },
                    )
                  }
                >
                  {updateSettings.isPending ? (
                    <>
                      <Spinner className="h-3.5 w-3.5" />
                      Enabling…
                    </>
                  ) : (
                    "Yes, enable automation"
                  )}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirmEnable(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}

          {automationEnabled ? (
            <Button
              size="sm"
              variant="danger"
              disabled={updateSettings.isPending}
              onClick={() =>
                updateSettings.mutate({ automation_enabled: false })
              }
            >
              {updateSettings.isPending ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Pausing…
                </>
              ) : (
                "Pause automation"
              )}
            </Button>
          ) : null}

          {settings.isError ? (
            <Alert variant="danger" title="Settings could not be loaded">
              {describeError(settings.error)}
            </Alert>
          ) : null}
          {updateSettings.isError ? (
            <Alert variant="danger" title="The master switch could not be changed">
              {describeError(updateSettings.error)}
            </Alert>
          ) : null}
          {!settings.isPending && settings.data && !automationEnabled ? (
            <Alert variant="info">
              Automation is off. Eligibility reports are still generated, and you
              can review every check and approve a report without enabling it.
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      {settings.data ? <SettingsEditor settings={settings.data} /> : null}

      {list.data.applications.length === 0 ? (
        <Card>
          <CardContent>
            <EmptyState
              icon={ClipboardList}
              title="No applications to evaluate"
              description="Prepare an application first. ApplyPilot will then show whether it is eligible, needs information, needs confirmation or is ineligible."
              action={
                <Link
                  href="/applications"
                  className={buttonClasses({ variant: "primary", size: "sm" })}
                >
                  Go to applications
                </Link>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
          <nav
            aria-label="Applications by auto-apply eligibility"
            className="lg:sticky lg:top-24 lg:self-start"
          >
            <div className="flex gap-2 overflow-x-auto pb-2 lg:flex-col lg:overflow-visible lg:pb-0">
              {list.data.applications.map((summary) => (
                <ApplicationRow
                  key={summary.application_id}
                  summary={summary}
                  active={summary.application_id === selected?.application_id}
                  onSelect={() => setSelectedId(summary.application_id)}
                />
              ))}
            </div>
            <div className="mt-3 hidden items-start gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-[11px] leading-relaxed text-ink-subtle lg:flex">
              <AlertTriangle
                aria-hidden="true"
                className="mt-0.5 h-3.5 w-3.5 shrink-0"
              />
              A blocked application is never shown as ready. Approval is recorded
              only for a report with no blocking issues.
            </div>
          </nav>

          <div className="min-w-0">
            {selected ? (
              <EligibilityDetail
                key={selected.application_id}
                applicationId={selected.application_id}
              />
            ) : (
              <Card>
                <CardContent>
                  <EmptyState
                    icon={XCircle}
                    title="Select an application"
                    description="Choose an application to see its eligibility report."
                  />
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}

      <div className="flex items-start gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-[11px] leading-relaxed text-ink-subtle">
        <ShieldCheck
          aria-hidden="true"
          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-300"
        />
        ApplyPilot never stores passwords, one-time codes, authentication cookies or
        payment details. Legal declarations are confirmed per application. Resume
        match scores are evidence, not automatic eligibility.
      </div>
    </div>
  );
}
