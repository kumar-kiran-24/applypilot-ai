"use client";

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDashed,
  Eye,
  FileText,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";

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
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  useAnalyzeLiveForm,
  useCancelFieldFill,
  useFillExecution,
  useFillPlan,
  usePauseFieldFill,
  useResumeFieldFill,
  useStartBrowserSession,
  useStartFieldFill,
} from "@/features/applications/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import type { FillFieldStatus, FillPlanField } from "@/types/api";

type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "brand";

const FIELD_STATUS: Record<
  FillFieldStatus,
  { label: string; variant: BadgeVariant }
> = {
  ready: { label: "Ready to fill", variant: "success" },
  needs_confirmation: { label: "Needs confirmation", variant: "warning" },
  needs_human_input: { label: "Your input required", variant: "warning" },
  already_filled: { label: "Left unchanged", variant: "neutral" },
  skipped: { label: "Skipped", variant: "neutral" },
  succeeded: { label: "Filled", variant: "success" },
  failed: { label: "Failed", variant: "danger" },
  pending_confirmation: { label: "Awaiting confirmation", variant: "warning" },
  pending_human_input: { label: "Awaiting your input", variant: "warning" },
};

const EXECUTION_STATUS: Record<string, { label: string; variant: BadgeVariant }> = {
  planned: { label: "Planned", variant: "neutral" },
  running: { label: "Filling", variant: "brand" },
  needs_user_input: { label: "Paused for you", variant: "warning" },
  waiting_for_confirmation: {
    label: "Waiting for confirmation",
    variant: "warning",
  },
  paused: { label: "Paused", variant: "warning" },
  ready_for_final_review: {
    label: "Ready for final review",
    variant: "success",
  },
  completed: { label: "Fields filled", variant: "success" },
  session_expired: { label: "Browser session expired", variant: "danger" },
  cancelled: { label: "Cancelled", variant: "neutral" },
  failed: { label: "Failed", variant: "danger" },
};

function FieldRow({
  field,
  resultStatus,
  verified,
}: {
  field: FillPlanField;
  resultStatus?: FillFieldStatus;
  verified?: boolean;
}) {
  const meta = FIELD_STATUS[resultStatus ?? field.status];
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12px] font-medium text-ink">
            {field.label}
            {field.required ? (
              <span className="ml-1 text-amber-300" aria-label="required">
                *
              </span>
            ) : null}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-subtle">
            {field.classification.replaceAll("_", " ")} · {field.action}
            {field.proposed_value ? " · value prepared" : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {verified ? (
            <CheckCircle2
              aria-hidden="true"
              className="h-3.5 w-3.5 text-emerald-300"
            />
          ) : null}
          <Badge variant={meta.variant}>{meta.label}</Badge>
        </div>
      </div>
      {field.status === "ready" && field.proposed_value ? (
        <p className="mt-2 truncate rounded-lg bg-navy-950/50 px-2 py-1 text-[11px] text-ink-muted">
          {field.proposed_value}
        </p>
      ) : null}
      {field.reason ? (
        <p className="mt-2 text-[11px] leading-relaxed text-ink-subtle">
          {field.reason}
        </p>
      ) : null}
    </div>
  );
}

function CountBadge({
  label,
  value,
  variant,
}: {
  label: string;
  value: number;
  variant: BadgeVariant;
}) {
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
      <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 text-lg font-semibold",
          variant === "success" && "text-emerald-300",
          variant === "warning" && "text-amber-200",
          variant === "danger" && "text-rose-300",
          variant === "brand" && "text-sky-200",
          variant === "neutral" && "text-ink",
        )}
      >
        {value}
      </p>
    </div>
  );
}

export function ApplicationFillPanel({
  applicationId,
}: {
  applicationId: string;
}) {
  const plan = useFillPlan(applicationId);
  const execution = useFillExecution(applicationId, Boolean(plan.data));
  const startBrowser = useStartBrowserSession(applicationId);
  const analyzeForm = useAnalyzeLiveForm(applicationId);
  const startFill = useStartFieldFill(applicationId);
  const pauseFill = usePauseFieldFill(applicationId);
  const resumeFill = useResumeFieldFill(applicationId);
  const cancelFill = useCancelFieldFill(applicationId);

  const busy =
    startBrowser.isPending ||
    analyzeForm.isPending ||
    startFill.isPending ||
    pauseFill.isPending ||
    resumeFill.isPending ||
    cancelFill.isPending;

  const mutationError =
    startBrowser.error ??
    analyzeForm.error ??
    startFill.error ??
    pauseFill.error ??
    resumeFill.error ??
    cancelFill.error;

  const data = plan.data;
  const run = execution.data;
  const resultByField = new Map(
    (run?.results ?? []).map((result) => [result.field_id, result]),
  );

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Bot aria-hidden="true" className="h-4 w-4 text-sky-300" />
            Intelligent form filling
          </CardTitle>
          <CardDescription>
            Preview exactly which fields ApplyPilot will fill from your verified
            profile and confirmed answers. Legal declarations, CAPTCHA, OTP/MFA,
            passwords and sensitive questions are always left to you.
          </CardDescription>
        </div>
        {data ? (
          <Badge variant={data.executable ? "brand" : "neutral"}>
            {data.eligible_count} eligible
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {plan.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : null}

        {plan.error ? (
          <div className="space-y-3">
            <Alert variant="info" title="A live form analysis is required">
              {describeError(plan.error)}
            </Alert>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => startBrowser.mutate()}
                disabled={busy}
              >
                {startBrowser.isPending ? <Spinner /> : <Play className="h-3.5 w-3.5" />}
                Open employer page
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => analyzeForm.mutate()}
                disabled={busy}
              >
                {analyzeForm.isPending ? <Spinner /> : <Eye className="h-3.5 w-3.5" />}
                Analyze current form
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => plan.refetch()}
                disabled={busy}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Refresh
              </Button>
            </div>
            <p className="text-[11px] text-ink-subtle">
              ApplyPilot does not open a replacement browser automatically when a
              session expires, and it does not stream a live browser preview.
            </p>
          </div>
        ) : null}

        {mutationError ? (
          <Alert variant="danger" title="Fill action was not completed">
            {describeError(mutationError)}
          </Alert>
        ) : null}

        {data ? (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <CountBadge
                label="Ready"
                value={data.eligible_count}
                variant="success"
              />
              <CountBadge
                label="Confirm"
                value={data.confirmation_count}
                variant="warning"
              />
              <CountBadge
                label="Your input"
                value={data.human_input_count}
                variant="warning"
              />
              <CountBadge
                label="Unchanged"
                value={data.already_filled_count}
                variant="neutral"
              />
            </div>

            <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
              <div className="flex items-center gap-2">
                <FileText aria-hidden="true" className="h-3.5 w-3.5 text-sky-300" />
                <p className="text-[12px] font-medium text-ink">Selected resume</p>
              </div>
              <p className="mt-1 truncate text-[11px] text-ink-muted">
                {data.resume.filename ?? "No resume selected"}
                {data.resume.will_upload
                  ? " · will be uploaded to the employer's file field"
                  : ""}
              </p>
              {data.resume.reason ? (
                <p className="mt-1 text-[11px] text-ink-subtle">
                  {data.resume.reason}
                </p>
              ) : null}
            </div>

            {data.warnings.length > 0 ? (
              <div className="space-y-2">
                {data.warnings.map((warning) => (
                  <Alert key={warning} variant="info">
                    {warning}
                  </Alert>
                ))}
              </div>
            ) : null}

            {run ? (
              <div className="space-y-2 rounded-xl border border-brand-blue/20 bg-brand-blue/[0.06] p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-[12px] font-medium text-ink">Fill progress</p>
                  <Badge
                    variant={
                      EXECUTION_STATUS[run.status]?.variant ?? "neutral"
                    }
                  >
                    {EXECUTION_STATUS[run.status]?.label ?? run.status}
                  </Badge>
                </div>
                <p className="text-[11px] text-ink-muted">
                  Run {run.run_number} · {run.succeeded_count} filled ·{" "}
                  {run.verified_count} verified · {run.failed_count} failed ·{" "}
                  {run.pending_confirmation_count +
                    run.pending_human_input_count}{" "}
                  awaiting you
                </p>
                {run.pause_reason ? (
                  <p className="text-[11px] text-amber-200">{run.pause_reason}</p>
                ) : null}
                {run.failure_reason ? (
                  <p className="text-[11px] text-rose-300">{run.failure_reason}</p>
                ) : null}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => startFill.mutate(false)}
                disabled={busy || !data.executable}
              >
                {startFill.isPending ? <Spinner /> : <Play className="h-3.5 w-3.5" />}
                Fill safe fields
              </Button>
              {run?.can_pause ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => pauseFill.mutate()}
                  disabled={busy}
                >
                  <Pause className="h-3.5 w-3.5" />
                  Pause
                </Button>
              ) : null}
              {run?.can_resume ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => resumeFill.mutate()}
                  disabled={busy}
                >
                  <Play className="h-3.5 w-3.5" />
                  Resume
                </Button>
              ) : null}
              {run?.can_cancel ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => cancelFill.mutate()}
                  disabled={busy}
                >
                  <X className="h-3.5 w-3.5" />
                  Cancel
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => plan.refetch()}
                disabled={busy}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Re-plan
              </Button>
            </div>

            <div className="space-y-2">
              {data.fields.map((field) => {
                const result = resultByField.get(field.field_id);
                return (
                  <FieldRow
                    key={field.field_id}
                    field={field}
                    resultStatus={result?.status}
                    verified={result?.verified}
                  />
                );
              })}
            </div>

            <div className="flex items-start gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
              {data.confirmation_count + data.human_input_count > 0 ? (
                <AlertTriangle
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300"
                />
              ) : (
                <ShieldCheck
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"
                />
              )}
              <p className="text-[11px] leading-relaxed text-ink-subtle">
                {data.final_submission_note}
              </p>
            </div>
          </>
        ) : null}

        {!plan.isLoading && !plan.error && !data ? (
          <div className="flex items-center gap-2 text-[11px] text-ink-subtle">
            <CircleDashed aria-hidden="true" className="h-3.5 w-3.5" />
            The fill plan could not be read.
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
