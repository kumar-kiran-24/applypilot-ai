"use client";

import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  ExternalLink,
  FileCheck2,
  Hand,
  ListChecks,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
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
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  useCancelFieldFill,
  useCancelIntervention,
  useFillExecution,
  useFinalReview,
  useInterventions,
  useMarkInterventionWorking,
  useReanalyzeHumanLoop,
  useResolveIntervention,
  useResumeHumanLoop,
} from "@/features/applications/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDateTime } from "@/lib/utils/format";
import type {
  FinalReviewResponse,
  Intervention,
  InterventionStatus,
  InterventionType,
  ReviewSection,
} from "@/types/api";

type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "brand";

const INTERVENTION_TYPE: Record<
  InterventionType,
  { label: string; variant: BadgeVariant }
> = {
  missing_information: { label: "Missing information", variant: "warning" },
  user_confirmation: { label: "Needs confirmation", variant: "warning" },
  stale_information: { label: "Stale information", variant: "warning" },
  conflicting_information: { label: "Conflicting information", variant: "danger" },
  sensitive_information: { label: "Sensitive — handle yourself", variant: "danger" },
  unsupported_field: { label: "External step required", variant: "danger" },
  browser_session_expired: { label: "Session expired", variant: "danger" },
  form_changed: { label: "Form changed", variant: "brand" },
};

const INTERVENTION_STATUS: Record<
  InterventionStatus,
  { label: string; variant: BadgeVariant }
> = {
  pending: { label: "Pending", variant: "warning" },
  user_working: { label: "You're working on it", variant: "brand" },
  resolved: { label: "Resolved", variant: "success" },
  cancelled: { label: "Dismissed", variant: "neutral" },
  expired: { label: "Expired", variant: "neutral" },
};

const EXECUTION_STATUS: Record<string, string> = {
  planned: "Planned",
  running: "Running",
  needs_user_input: "Paused for you",
  waiting_for_confirmation: "Waiting for confirmation",
  paused: "Paused",
  ready_for_final_review: "Ready for final review",
  completed: "Completed",
  session_expired: "Browser session expired",
  cancelled: "Cancelled",
  failed: "Failed",
};

function InterventionCard({
  item,
  pending,
  onWorking,
  onResolve,
  onCancel,
}: {
  item: Intervention;
  pending: boolean;
  onWorking: () => void;
  onResolve: () => void;
  onCancel: () => void;
}) {
  const type = INTERVENTION_TYPE[item.intervention_type];
  const status = INTERVENTION_STATUS[item.status];
  const active = item.status === "pending" || item.status === "user_working";
  const canResolve =
    active &&
    ["missing_information", "stale_information", "conflicting_information"].includes(
      item.intervention_type,
    );

  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        item.required && active
          ? "border-amber-500/25 bg-amber-500/[0.03]"
          : "border-white/[0.07] bg-white/[0.02]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-ink">
            {item.field_label}
            {item.required ? (
              <span className="ml-1 text-red-300" aria-label="required">
                *
              </span>
            ) : (
              <span className="ml-1.5 text-[10px] uppercase tracking-[0.08em] text-ink-subtle">
                optional
              </span>
            )}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-subtle">
            {item.reason}
          </p>
          {item.status === "resolved" && item.resolution_reason ? (
            <p className="mt-1.5 text-[11px] text-emerald-200/80">
              {item.resolution_reason}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <Badge variant={type.variant}>{type.label}</Badge>
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
      </div>

      <p className="mt-2 text-[10px] text-ink-subtle">
        {item.field_category.replaceAll("_", " ")}
        {item.analysis_version ? ` · analysis v${item.analysis_version}` : ""}
        {item.user_working_at
          ? ` · started ${formatDateTime(item.user_working_at)}`
          : ""}
      </p>

      {active ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {item.status !== "user_working" ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={pending}
              onClick={onWorking}
            >
              {pending ? <Spinner /> : <Hand className="h-3.5 w-3.5" />}
              I&apos;m working on it
            </Button>
          ) : null}
          {canResolve ? (
            <Button size="sm" disabled={pending} onClick={onResolve}>
              {pending ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />}
              Verify & resolve
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" disabled={pending} onClick={onCancel}>
            <X className="h-3.5 w-3.5" />
            Dismiss
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function ReviewSectionCard({ section }: { section: ReviewSection }) {
  const complete = section.status === "complete";
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] font-medium text-ink">{section.label}</p>
        <Badge
          variant={
            complete ? "success" : section.status === "action_required" ? "warning" : "neutral"
          }
        >
          {section.status.replaceAll("_", " ")}
        </Badge>
      </div>
      {section.items.length === 0 ? (
        <p className="mt-1.5 text-[11px] text-ink-subtle">Not provided.</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {section.items.map((item, index) => (
            <li
              key={`${item.label}-${index}`}
              className="flex items-start justify-between gap-2 text-[11px]"
            >
              <span className="min-w-0 text-ink-muted">
                {item.label}
                {item.optional ? (
                  <span className="ml-1 text-[10px] text-ink-subtle">optional</span>
                ) : null}
                {item.detail ? (
                  <span className="block text-ink-subtle">{item.detail}</span>
                ) : null}
              </span>
              {item.present ? (
                <CheckCircle2
                  aria-label="present"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"
                />
              ) : (
                <Clock3
                  aria-label="not provided"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-subtle"
                />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FinalReviewCard({ review }: { review: FinalReviewResponse }) {
  return (
    <div className="space-y-3 rounded-xl border border-brand-blue/20 bg-brand-blue/[0.05] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="flex items-center gap-2 text-[13px] font-semibold text-ink">
            <ClipboardCheck aria-hidden="true" className="h-4 w-4 text-sky-300" />
            Final review checklist
          </p>
          <p className="mt-1 text-[11px] text-ink-subtle">
            {review.ready_for_final_review
              ? "Safe fields are ready for your review."
              : "Some blockers remain before final review."}{" "}
            {review.filled_count} filled · {review.verified_count} verified ·{" "}
            {review.blocker_count} blocker
            {review.blocker_count === 1 ? "" : "s"}.
          </p>
        </div>
        <Badge variant={review.ready_for_final_review ? "success" : "warning"}>
          {review.ready_for_final_review ? "Ready for final review" : "Not ready"}
        </Badge>
      </div>

      {review.resume.filename ? (
        <p className="flex items-center gap-2 text-[11px] text-ink-muted">
          <FileCheck2 aria-hidden="true" className="h-3.5 w-3.5 text-sky-300" />
          Resume: {review.resume.filename}
        </p>
      ) : (
        <p className="text-[11px] text-amber-200">No resume is selected.</p>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        {review.sections.map((section) => (
          <ReviewSectionCard key={section.key} section={section} />
        ))}
      </div>

      {review.blocking_interventions.length > 0 ? (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.05] p-3">
          <p className="text-[11px] font-medium text-amber-100">Remaining blockers</p>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-amber-100/80">
            {review.blocking_interventions.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {review.confirmation_fields.length > 0 ? (
        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <p className="text-[11px] font-medium text-ink">
            Complete these yourself on the employer&apos;s page
          </p>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-ink-subtle">
            {review.confirmation_fields.map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {review.missing_optional_fields.length > 0 ? (
        <p className="text-[11px] text-ink-subtle">
          Optional fields left blank: {review.missing_optional_fields.join(", ")}.
        </p>
      ) : null}

      <p className="text-[11px] leading-relaxed text-ink-subtle">
        {review.final_submission_note}
      </p>
    </div>
  );
}

export function ApplicationHumanLoopPanel({
  applicationId,
}: {
  applicationId: string;
}) {
  const interventions = useInterventions(applicationId);
  const review = useFinalReview(applicationId);
  const execution = useFillExecution(applicationId, Boolean(interventions.data));
  const working = useMarkInterventionWorking(applicationId);
  const resolve = useResolveIntervention(applicationId);
  const dismiss = useCancelIntervention(applicationId);
  const reanalyze = useReanalyzeHumanLoop(applicationId);
  const resume = useResumeHumanLoop(applicationId);
  const cancelExecution = useCancelFieldFill(applicationId);

  const busy =
    working.isPending ||
    resolve.isPending ||
    dismiss.isPending ||
    reanalyze.isPending ||
    resume.isPending ||
    cancelExecution.isPending;
  const pendingId =
    working.variables ??
    resolve.variables ??
    dismiss.variables ??
    null;
  const mutationError =
    working.error ??
    resolve.error ??
    dismiss.error ??
    reanalyze.error ??
    resume.error ??
    cancelExecution.error;

  const data = interventions.data;
  const run = execution.data;
  const active = (data?.interventions ?? []).filter(
    (item) => item.status === "pending" || item.status === "user_working",
  );
  const history = (data?.interventions ?? []).filter(
    (item) => item.status !== "pending" && item.status !== "user_working",
  );

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Sparkles aria-hidden="true" className="h-4 w-4 text-sky-300" />
            Human-in-the-loop review
          </CardTitle>
          <CardDescription>
            Everything ApplyPilot could not safely automate, with the exact reason
            and what happens next. Legal declarations, CAPTCHA, OTP/MFA and final
            submission always stay in your hands.
          </CardDescription>
        </div>
        {data ? (
          <Badge variant={data.blocking_count > 0 ? "warning" : "success"}>
            {data.blocking_count} blocker{data.blocking_count === 1 ? "" : "s"}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {interventions.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : null}

        {interventions.error ? (
          <div className="space-y-3">
            <Alert variant="info" title="No intervention queue yet">
              {describeError(interventions.error)}
            </Alert>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void interventions.refetch()}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </Button>
          </div>
        ) : null}

        {mutationError ? (
          <Alert variant="danger" title="The intervention action was not completed">
            {describeError(mutationError)}
          </Alert>
        ) : null}

        {data ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
              <div>
                <p className="text-[12px] font-medium text-ink">
                  Execution: {EXECUTION_STATUS[run?.status ?? ""] ?? "Not started"}
                </p>
                <p className="mt-0.5 text-[11px] text-ink-subtle">
                  {run
                    ? `Run ${run.run_number} · ${run.succeeded_count} filled · ${run.verified_count} verified`
                    : "Start a fill run to build the human review queue."}
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy || !data.browser_session_id}
                  onClick={() => reanalyze.mutate()}
                >
                  {reanalyze.isPending ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />}
                  Re-analyze form
                </Button>
                {run?.can_resume ? (
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() => resume.mutate()}
                  >
                    {resume.isPending ? <Spinner /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                    Resume filling
                  </Button>
                ) : null}
                {run?.can_cancel ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => cancelExecution.mutate()}
                  >
                    <X className="h-3.5 w-3.5" />
                    Cancel run
                  </Button>
                ) : null}
              </div>
            </div>

            {!data.view.user_can_interact ? (
              <Alert variant="info" title="The employer's page is not shown in ApplyPilot">
                {data.view.reason} Changes you make in a separate browser cannot be
                seen by the backend session. Provide missing answers in ApplyPilot,
                or complete and submit the employer&apos;s page yourself. ApplyPilot
                never submits it for you.
              </Alert>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <a
                href={data.application_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 text-[12px] font-medium text-ink-muted transition-colors hover:border-white/20 hover:text-ink"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Open employer page yourself
                <ArrowUpRight className="h-3.5 w-3.5" />
              </a>
              <p className="text-[11px] text-ink-subtle">
                This external page is separate from the ApplyPilot browser session.
              </p>
            </div>

            {reanalyze.data ? (
              <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                <p className="flex items-center gap-2 text-[12px] font-medium text-ink">
                  <ListChecks aria-hidden="true" className="h-3.5 w-3.5 text-sky-300" />
                  Analysis v{reanalyze.data.analysis_version}
                </p>
                <p className="mt-1 text-[11px] text-ink-subtle">
                  {reanalyze.data.changes.length} structural or value change
                  {reanalyze.data.changes.length === 1 ? "" : "s"} detected ·{" "}
                  {reanalyze.data.newly_completed_fields.length} newly completed ·{" "}
                  {reanalyze.data.blocked_count} blocked
                </p>
                {reanalyze.data.newly_completed_fields.length > 0 ? (
                  <p className="mt-1.5 text-[11px] text-emerald-200/80">
                    Completed: {reanalyze.data.newly_completed_fields.join(", ")}
                  </p>
                ) : null}
                {reanalyze.data.changes.length > 0 ? (
                  <ul className="mt-2 space-y-1 text-[11px] text-ink-muted">
                    {reanalyze.data.changes.slice(0, 8).map((change) => (
                      <li key={`${change.change}-${change.field_id}-${change.label}`}>
                        {change.change}: {change.label}
                        {change.previous_label ? ` (was ${change.previous_label})` : ""}
                        {change.now_has_value && !change.previously_had_value
                          ? " · now has a value"
                          : ""}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className="mt-2 text-[11px] leading-relaxed text-ink-subtle">
                  {reanalyze.data.verification_note}
                </p>
              </div>
            ) : null}

            {active.length === 0 ? (
              <EmptyState
                icon={ShieldCheck}
                title="No active interventions"
                description={
                  data.total === 0
                    ? "Run the safe field fill first. Any field that needs your input will appear here."
                    : "Every reported item has been resolved or dismissed."
                }
              />
            ) : (
              <div className="space-y-2">
                <p className="text-[11px] uppercase tracking-[0.1em] text-ink-subtle">
                  Needs your attention
                </p>
                {active.map((item) => (
                  <InterventionCard
                    key={item.intervention_id}
                    item={item}
                    pending={pendingId === item.intervention_id}
                    onWorking={() => working.mutate(item.intervention_id)}
                    onResolve={() => resolve.mutate(item.intervention_id)}
                    onCancel={() => dismiss.mutate(item.intervention_id)}
                  />
                ))}
              </div>
            )}

            {review.data && review.data.execution_id ? (
              <FinalReviewCard review={review.data} />
            ) : review.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : null}

            {history.length > 0 ? (
              <details className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                <summary className="cursor-pointer text-[11px] font-medium text-ink-muted">
                  History ({history.length})
                </summary>
                <div className="mt-2 space-y-1.5">
                  {history.map((item) => {
                    const status = INTERVENTION_STATUS[item.status];
                    return (
                      <div
                        key={item.intervention_id}
                        className="flex items-center justify-between gap-2 text-[11px]"
                      >
                        <span className="truncate text-ink-muted">
                          {item.field_label}
                        </span>
                        <Badge variant={status.variant}>{status.label}</Badge>
                      </div>
                    );
                  })}
                </div>
              </details>
            ) : null}

            {data.blocking_count > 0 ? (
              <div className="flex items-start gap-2 rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-3">
                <ShieldAlert
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300"
                />
                <p className="text-[11px] leading-relaxed text-amber-100/80">
                  Required information is still blocking final review. ApplyPilot
                  will not guess values or accept declarations for you.
                </p>
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.04] p-3">
                <ShieldCheck
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"
                />
                <p className="text-[11px] leading-relaxed text-emerald-100/80">
                  No blocking information remains. Review everything above, complete
                  any legal/CAPTCHA/OTP step yourself, and submit on the
                  employer&apos;s website.
                </p>
              </div>
            )}

            {data.final_submission_note ? (
              <p className="flex items-start gap-2 text-[11px] text-ink-subtle">
                <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {data.final_submission_note}
              </p>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
