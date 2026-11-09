"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpRight,
  CheckCircle2,
  ClipboardList,
  FileText,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";

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
  useConfirmMissingSuggestion,
  useDismissMissingQuestion,
  useMissingInformation,
  useMissingInformationQueue,
  useRefreshMissingInformation,
  useResolveMissingConflict,
  useSubmitMissingAnswer,
} from "@/features/missing-information/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDateTime } from "@/lib/utils/format";
import type {
  ConflictResolution,
  MissingInformationAnswerSource,
  MissingInformationConflict,
  MissingInformationDetail,
  MissingInformationItem,
  MissingInformationItemStatus,
  MissingInformationSummary,
  MissingInformationQueueStatus,
  QuestionType,
} from "@/types/api";

type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "brand";

const ITEM_STATUS: Record<
  MissingInformationItemStatus,
  { label: string; variant: BadgeVariant }
> = {
  missing: { label: "Missing", variant: "warning" },
  suggested: { label: "Suggested", variant: "brand" },
  stale: { label: "Stale answer", variant: "warning" },
  sensitive_confirmation: { label: "Sensitive — confirm", variant: "brand" },
  legal_confirmation: { label: "Legal — confirm", variant: "danger" },
  resolved: { label: "Resolved", variant: "success" },
  dismissed: { label: "Skipped", variant: "neutral" },
};

const QUEUE_STATUS: Record<
  MissingInformationQueueStatus,
  { label: string; variant: BadgeVariant }
> = {
  pending: { label: "Needs information", variant: "warning" },
  ready_to_complete: { label: "Ready to complete", variant: "success" },
};

const SOURCE: Record<
  MissingInformationAnswerSource,
  { label: string; variant: BadgeVariant }
> = {
  profile: { label: "Verified profile", variant: "success" },
  resume: { label: "Resume suggestion", variant: "warning" },
  previously_approved: { label: "Reused approved answer", variant: "brand" },
  ai_suggestion: { label: "AI draft — review", variant: "danger" },
  user_input: { label: "Your input", variant: "neutral" },
  needs_confirmation: { label: "Needs confirmation", variant: "warning" },
};

const inputClass =
  "w-full rounded-xl border border-white/10 bg-navy-950/60 px-3 py-2 text-[13px] text-ink placeholder:text-ink-subtle transition-colors focus:border-brand-blue/60 focus:outline-none focus:ring-2 focus:ring-brand-blue/25 disabled:cursor-not-allowed disabled:opacity-60";

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div
      className="h-2 overflow-hidden rounded-full bg-white/[0.06]"
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Required information completion"
    >
      <div
        className="h-full rounded-full bg-gradient-to-r from-brand-indigo to-brand-blue transition-all"
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

function QueueBadge({ status }: { status: MissingInformationQueueStatus }) {
  const meta = QUEUE_STATUS[status];
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
      <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
        {label}
      </p>
      <p className="mt-1 text-lg font-semibold text-ink">{value}</p>
    </div>
  );
}

function AnswerControl({
  item,
  value,
  setValue,
}: {
  item: MissingInformationItem;
  value: string;
  setValue: (next: string) => void;
}) {
  if (item.field_type === "file") {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5 text-[13px] text-ink">
        <FileText aria-hidden="true" className="h-4 w-4 text-sky-300" />
        {value || "No document selected"}
      </div>
    );
  }
  if (item.field_type === "declaration") {
    return (
      <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5">
        <input
          type="checkbox"
          checked={value === "Yes"}
          onChange={(event) => setValue(event.target.checked ? "Yes" : "")}
          className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04]"
        />
        <span className="text-[12px] leading-relaxed text-ink-muted">
          I have read this declaration and confirm it for this application.
        </span>
      </label>
    );
  }
  if (item.field_type === "textarea") {
    return (
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        rows={3}
        className={inputClass}
        aria-label={item.question_text}
      />
    );
  }
  if (item.field_type === "radio" || item.field_type === "single_choice") {
    const options =
      item.options.length > 0
        ? item.options
        : item.field_type === "radio"
          ? ["Yes", "No"]
          : [];
    return (
      <div className="space-y-2">
        {options.map((option) => (
          <label
            key={option}
            className={cn(
              "flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2 text-[13px] transition-colors",
              value === option
                ? "border-brand-blue/35 bg-brand-blue/10 text-ink"
                : "border-white/10 bg-white/[0.02] text-ink-muted hover:border-white/20",
            )}
          >
            <input
              type="radio"
              name={`missing-${item.question_key}`}
              value={option}
              checked={value === option}
              onChange={() => setValue(option)}
              className="h-4 w-4 border-white/20 bg-white/[0.04]"
            />
            {option}
          </label>
        ))}
      </div>
    );
  }
  if (item.field_type === "boolean") {
    const options =
      item.options.length > 0 ? item.options : ["Yes", "No"];
    return (
      <select
        value={value}
        onChange={(event) => setValue(event.target.value)}
        className={inputClass}
        aria-label={item.question_text}
      >
        <option value="">Select an answer…</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }
  const type: Record<string, string> = {
    email: "email",
    phone: "tel",
    url: "url",
    number: "number",
    date: "date",
  };
  return (
    <input
      type={type[item.field_type as QuestionType] ?? "text"}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      className={inputClass}
      aria-label={item.question_text}
    />
  );
}

function ItemCard({
  item,
  optional,
  pending,
  onSave,
  onSkip,
}: {
  item: MissingInformationItem;
  optional: boolean;
  pending: boolean;
  onSave: (value: string, saveForReuse: boolean) => void;
  onSkip?: () => void;
}) {
  const [value, setValue] = useState(
    item.answer ?? item.suggested_answer ?? "",
  );
  const [saveForReuse, setSaveForReuse] = useState(false);
  const meta = ITEM_STATUS[item.status];
  const suggestion = item.suggested_source
    ? SOURCE[item.suggested_source]
    : null;
  const canReuse =
    !item.is_sensitive && !item.is_legal && item.field_type !== "file";

  useEffect(() => {
    setValue(item.answer ?? item.suggested_answer ?? "");
  }, [item.answer, item.suggested_answer]);

  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        item.blocks_application
          ? "border-amber-500/20 bg-amber-500/[0.03]"
          : "border-white/[0.07] bg-white/[0.02]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-ink">
            {item.question_text}
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
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {item.is_sensitive ? (
            <Badge variant="warning">
              <ShieldCheck aria-hidden="true" className="h-3 w-3" />
              Sensitive
            </Badge>
          ) : null}
          {item.is_legal ? (
            <Badge variant="danger">
              <Lock aria-hidden="true" className="h-3 w-3" />
              Legal
            </Badge>
          ) : null}
          <Badge variant={meta.variant}>{meta.label}</Badge>
        </div>
      </div>

      {item.status === "stale" && item.answer ? (
        <p className="mt-2 rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-3 py-2 text-[11px] leading-relaxed text-amber-100/90">
          Current stored value: <span className="font-medium">{item.answer}</span>
        </p>
      ) : null}

      <div className="mt-3">
        <AnswerControl item={item} value={value} setValue={setValue} />
      </div>

      {suggestion ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <Badge variant={suggestion.variant}>{suggestion.label}</Badge>
          {item.confidence > 0 && item.confidence < 1 ? (
            <span className="text-[11px] text-ink-subtle">
              {Math.round(item.confidence * 100)}% confidence
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        {canReuse ? (
          <label className="flex cursor-pointer items-start gap-2 text-[11px] leading-relaxed text-ink-muted">
            <input
              type="checkbox"
              checked={saveForReuse}
              onChange={(event) => setSaveForReuse(event.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 rounded border-white/20 bg-white/[0.04]"
            />
            Save for future applications
          </label>
        ) : (
          <span className="text-[11px] text-ink-subtle">
            {item.is_legal
              ? "Confirmed for this application only."
              : item.is_sensitive
                ? "Sensitive answers are never reused."
                : null}
          </span>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          {onSkip ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={onSkip}
            >
              Skip
            </Button>
          ) : null}
          <Button
            size="sm"
            disabled={pending || !value.trim()}
            onClick={() => onSave(value.trim(), saveForReuse)}
          >
            {pending ? (
              <>
                <Spinner className="h-3.5 w-3.5" />
                Saving…
              </>
            ) : (
              <>
                <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
                {optional ? "Save answer" : "Confirm answer"}
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ConflictCard({
  conflict,
  pending,
  onResolve,
}: {
  conflict: MissingInformationConflict;
  pending: boolean;
  onResolve: (
    resolution: ConflictResolution,
    answer?: string | null,
  ) => void;
}) {
  const [edited, setEdited] = useState(conflict.stored_answer);
  return (
    <div className="rounded-xl border border-red-500/20 bg-red-500/[0.03] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[13px] font-medium text-ink">
            {conflict.question_text}
            {conflict.blocks_application ? (
              <span className="ml-1 text-red-300" aria-label="required">
                *
              </span>
            ) : null}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-subtle">
            {conflict.reason}
          </p>
        </div>
        {conflict.blocks_application ? (
          <Badge variant="danger">Blocks application</Badge>
        ) : (
          <Badge variant="warning">Resolve before reuse</Badge>
        )}
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
          <p className="text-[10px] uppercase tracking-[0.08em] text-ink-subtle">
            Stored answer
          </p>
          <p className="mt-1 text-[12px] text-ink">{conflict.stored_answer}</p>
        </div>
        <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
          <p className="text-[10px] uppercase tracking-[0.08em] text-ink-subtle">
            Conflicting value ({conflict.conflicting_source})
          </p>
          <p className="mt-1 text-[12px] text-ink">
            {conflict.conflicting_answer}
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-col gap-2">
        <input
          value={edited}
          onChange={(event) => setEdited(event.target.value)}
          className={inputClass}
          placeholder="Corrected value"
          aria-label={`Corrected value for ${conflict.question_text}`}
        />
        <div className="flex flex-wrap gap-1.5">
          {conflict.resolution_options.includes("keep_stored") ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={pending}
              onClick={() => onResolve("keep_stored")}
            >
              Keep stored
            </Button>
          ) : null}
          {conflict.resolution_options.includes("use_profile") ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={pending}
              onClick={() => onResolve("use_profile")}
            >
              Use profile value
            </Button>
          ) : null}
          {conflict.resolution_options.includes("edited") ? (
            <Button
              size="sm"
              disabled={pending || !edited.trim()}
              onClick={() => onResolve("edited", edited.trim())}
            >
              Save corrected value
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function DetailSection({
  title,
  description,
  count,
  children,
}: {
  title: string;
  description?: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {typeof count === "number" ? (
          <Badge variant={count > 0 ? "warning" : "success"}>{count}</Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

function ApplicationQueueDetail({ applicationId }: { applicationId: string }) {
  const detail = useMissingInformation(applicationId);
  const refresh = useRefreshMissingInformation(applicationId);
  const submit = useSubmitMissingAnswer(applicationId);
  const confirm = useConfirmMissingSuggestion(applicationId);
  const dismiss = useDismissMissingQuestion(applicationId);
  const resolve = useResolveMissingConflict(applicationId);

  if (detail.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (detail.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load this queue">
            {describeError(detail.error)}
          </Alert>
          <div className="mt-4 flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void detail.refetch()}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Retry
            </Button>
            <Link
              href="/applications"
              className={buttonClasses({ variant: "ghost", size: "sm" })}
            >
              Back to applications
            </Link>
          </div>
        </CardContent>
      </Card>
    );
  }

  const data: MissingInformationDetail = detail.data;
  const pendingKey =
    (submit.isPending && submit.variables?.questionKey) ||
    (confirm.isPending && confirm.variables?.questionKey) ||
    (dismiss.isPending && dismiss.variables) ||
    null;
  const mutationError = submit.error ?? confirm.error ?? dismiss.error ?? resolve.error;

  const saveItem = (item: MissingInformationItem) => (
    value: string,
    saveForReuse: boolean,
  ) => {
    const isSuggestion =
      item.suggested_answer !== null &&
      item.suggested_source !== null &&
      item.status !== "legal_confirmation" &&
      value === item.suggested_answer;
    if (isSuggestion) {
      confirm.mutate({
        questionKey: item.question_key,
        answer: null,
        saveForReuse,
      });
      return;
    }
    submit.mutate({
      questionKey: item.question_key,
      answer: value,
      saveForReuse,
    });
  };

  const simpleRequired = data.required_questions.filter(
    (item) => item.status === "missing" || item.status === "suggested",
  );
  const simpleOptional = data.optional_questions.filter(
    (item) => item.status === "missing" || item.status === "suggested",
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>{data.job_title}</CardTitle>
            <CardDescription>
              {data.company ?? "Company not listed"} · {data.required_remaining}{" "}
              required question{data.required_remaining === 1 ? "" : "s"}{" "}
              remaining
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <QueueBadge status={data.queue_status} />
            <Button
              size="sm"
              variant="secondary"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              {refresh.isPending ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Recalculating…
                </>
              ) : (
                <>
                  <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
                  Refresh
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
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[12px] text-ink-muted">
              {data.required_resolved} of {data.required_total} required answers
              resolved · {data.matched_count} previously matched
            </p>
            <span className="text-sm font-semibold text-ink">
              {data.progress_percent}%
            </span>
          </div>
          <ProgressBar percent={data.progress_percent} />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <Stat label="Required left" value={data.required_remaining} />
            <Stat label="Matched" value={data.matched_count} />
            <Stat label="Stale" value={data.stale_count} />
            <Stat label="Conflicts" value={data.conflict_count} />
            <Stat label="Sensitive" value={data.sensitive_confirmation_count} />
          </div>
          <p className="text-[11px] text-ink-subtle">
            Queue recalculated{" "}
            {data.last_recalculated_at
              ? formatDateTime(data.last_recalculated_at)
              : "just now"}
            . Optional questions never block the application.
          </p>

          {data.ready_to_complete ? (
            <Alert variant="success" title="All required information is resolved">
              The application is marked ready to complete. Review it once more in
              the application workspace before approving anything.
            </Alert>
          ) : null}

          {mutationError ? (
            <Alert variant="danger" title="The queue could not be updated">
              {describeError(mutationError)}
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      <DetailSection
        title="Required questions"
        description="Answer these before the application can proceed."
        count={data.required_remaining}
      >
        {simpleRequired.length === 0 ? (
          data.required_remaining === 0 ? (
            <Alert variant="success">
              Every required question is resolved.
            </Alert>
          ) : (
            <p className="text-[12px] text-ink-muted">
              Remaining required items are listed in the sensitive, legal, stale
              or conflict sections below.
            </p>
          )
        ) : (
          simpleRequired.map((item) => (
            <ItemCard
              key={item.question_key}
              item={item}
              optional={false}
              pending={pendingKey === item.question_key}
              onSave={saveItem(item)}
            />
          ))
        )}
      </DetailSection>

      {data.sensitive_confirmations.length > 0 ? (
        <DetailSection
          title="Sensitive and legal confirmations"
          description="These answers are prefilled but need a fresh confirmation for this application. Legal declarations are never reused."
          count={data.sensitive_confirmations.length}
        >
          {data.sensitive_confirmations.map((item) => (
            <ItemCard
              key={item.question_key}
              item={item}
              optional={!item.required}
              pending={pendingKey === item.question_key}
              onSave={saveItem(item)}
              onSkip={
                !item.required && !item.is_legal
                  ? () => dismiss.mutate(item.question_key)
                  : undefined
              }
            />
          ))}
        </DetailSection>
      ) : null}

      {data.stale_answers.length > 0 ? (
        <DetailSection
          title="Stale answers"
          description="The stored confirmation expired. Reconfirm the value or replace it."
          count={data.stale_answers.length}
        >
          {data.stale_answers.map((item) => (
            <ItemCard
              key={item.question_key}
              item={item}
              optional={!item.required}
              pending={pendingKey === item.question_key}
              onSave={saveItem(item)}
            />
          ))}
        </DetailSection>
      ) : null}

      {data.conflicts.length > 0 ? (
        <DetailSection
          title="Conflicts"
          description="A stored answer disagrees with your verified profile or another answer."
          count={data.conflicts.length}
        >
          {data.conflicts.map((conflict) => (
            <ConflictCard
              key={conflict.question_key}
              conflict={conflict}
              pending={
                resolve.isPending &&
                resolve.variables?.questionKey === conflict.question_key
              }
              onResolve={(resolution, answer) =>
                resolve.mutate({
                  questionKey: conflict.question_key,
                  resolution,
                  answer,
                })
              }
            />
          ))}
        </DetailSection>
      ) : null}

      {simpleOptional.length > 0 ? (
        <DetailSection
          title="Optional questions"
          description="Unanswered optional questions are shown here. Skipping them never blocks the application."
          count={simpleOptional.length}
        >
          {simpleOptional.map((item) => (
            <ItemCard
              key={item.question_key}
              item={item}
              optional
              pending={pendingKey === item.question_key}
              onSave={saveItem(item)}
              onSkip={
                !item.is_legal
                  ? () => dismiss.mutate(item.question_key)
                  : undefined
              }
            />
          ))}
        </DetailSection>
      ) : null}

      {data.matched_questions.length > 0 ? (
        <DetailSection
          title="Previously matched"
          description="These answers were matched from your verified profile, resume or approved answer bank."
          count={data.matched_questions.length}
        >
          <ul className="space-y-2">
            {data.matched_questions.map((item) => {
              const source = item.answer_source ? SOURCE[item.answer_source] : null;
              return (
                <li
                  key={item.question_key}
                  className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="text-[12px] font-medium text-ink">
                      {item.question_text}
                    </p>
                    <p className="mt-0.5 truncate text-[11px] text-ink-muted">
                      {item.answer}
                    </p>
                  </div>
                  {source ? (
                    <Badge variant={source.variant}>{source.label}</Badge>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </DetailSection>
      ) : null}

      {data.dismissed_questions.length > 0 ? (
        <DetailSection
          title="Skipped optional questions"
          description="You chose not to answer these. They stay persisted but never block the application."
          count={data.dismissed_questions.length}
        >
          <div className="flex flex-wrap gap-1.5">
            {data.dismissed_questions.map((item) => (
              <Badge key={item.question_key} variant="neutral">
                {item.question_text}
              </Badge>
            ))}
          </div>
        </DetailSection>
      ) : null}
    </div>
  );
}

function QueueListItem({
  summary,
  active,
  onSelect,
}: {
  summary: MissingInformationSummary;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "w-full min-w-[220px] rounded-xl border px-3 py-3 text-left transition-colors lg:min-w-0",
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
            {summary.company ?? "Company not listed"}
          </p>
        </div>
        <QueueBadge status={summary.queue_status} />
      </div>
      <div className="mt-2.5">
        <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full bg-gradient-to-r from-brand-indigo to-brand-blue"
            style={{ width: `${summary.progress_percent}%` }}
          />
        </div>
        <p className="mt-1.5 text-[10px] text-ink-subtle">
          {summary.required_remaining} required left ·{" "}
          {summary.progress_percent}%
        </p>
      </div>
    </button>
  );
}

export function MissingInformationBoard() {
  const queue = useMissingInformationQueue();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (!queue.data) {
      return;
    }
    const exists = queue.data.applications.some(
      (item) => item.application_id === selectedId,
    );
    if (!exists) {
      setSelectedId(queue.data.applications[0]?.application_id ?? null);
    }
  }, [queue.data, selectedId]);

  if (queue.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (queue.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load the missing-information queue">
            {describeError(queue.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            className="mt-4"
            onClick={() => void queue.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (queue.data.total === 0) {
    return (
      <EmptyState
        icon={ClipboardList}
        title="No applications need information"
        description="Prepare an application and ApplyPilot will list exactly which required questions still need your answer."
        action={
          <Link
            href="/applications"
            className={buttonClasses({ variant: "primary", size: "sm" })}
          >
            <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
            Go to applications
          </Link>
        }
      />
    );
  }

  const selected =
    queue.data.applications.find(
      (item) => item.application_id === selectedId,
    ) ?? queue.data.applications[0];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-ink-muted">
          {queue.data.total} application
          {queue.data.total === 1 ? "" : "s"} in the queue ·{" "}
          {queue.data.applications.filter((item) => item.ready_to_complete).length}{" "}
          ready to complete
        </p>
        <Button
          variant="secondary"
          size="sm"
          disabled={queue.isFetching}
          onClick={() => void queue.refetch()}
        >
          {queue.isFetching ? (
            <>
              <Spinner className="h-3.5 w-3.5" />
              Recalculating…
            </>
          ) : (
            <>
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Recalculate all
            </>
          )}
        </Button>
      </div>

      <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
        <nav
          aria-label="Applications with missing information"
          className="lg:sticky lg:top-24 lg:self-start"
        >
          <div className="flex gap-2 overflow-x-auto pb-2 lg:flex-col lg:overflow-visible lg:pb-0">
            {queue.data.applications.map((summary) => (
              <QueueListItem
                key={summary.application_id}
                summary={summary}
                active={summary.application_id === selected?.application_id}
                onSelect={() => setSelectedId(summary.application_id)}
              />
            ))}
          </div>
          <div className="mt-3 hidden items-start gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-[11px] leading-relaxed text-ink-subtle lg:flex">
            <Sparkles aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Answers are matched against your verified profile and approved
            answer bank. ApplyPilot never invents a value.
          </div>
        </nav>

        <div className="min-w-0">
          {selected ? (
            <ApplicationQueueDetail
              key={selected.application_id}
              applicationId={selected.application_id}
            />
          ) : null}
        </div>
      </div>

      <div className="flex items-start gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-[11px] leading-relaxed text-ink-subtle">
        <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        ApplyPilot never stores passwords, one-time codes, authentication cookies
        or payment details. Legal declarations are confirmed per application and
        are never reused. Optional questions are excluded from blocking progress.
      </div>
    </div>
  );
}
