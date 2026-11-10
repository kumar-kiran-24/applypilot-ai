"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  FileText,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UserRound,
  Workflow,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApplicationEligibilityPanel } from "@/components/auto-apply/eligibility-panel";
import { ApplicationBrowserWorkspace } from "@/components/applications/application-browser-workspace";
import { ApplicationFillPanel } from "@/components/applications/application-fill-panel";
import { ApplicationHumanLoopPanel } from "@/components/applications/application-human-loop-panel";
import { EmployerApplicationUrlForm } from "@/components/applications/employer-application-url-form";
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
  useApplication,
  useApplicationWorkflow,
  useApproveApplication,
  useConfirmManualSubmission,
  useSelectApplicationResume,
  useSubmitApplication,
  useUpdateApplicationQuestion,
  useUpdateApplicationStage,
} from "@/features/applications/hooks";
import { useResumes } from "@/features/resumes/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDate, formatDateTime } from "@/lib/utils/format";
import { safeExternalUrl } from "@/lib/utils/url";
import type {
  AnswerSource,
  ApplicationPreview,
  ApplicationQuestion,
  ApplicationSection,
  ApplicationStage,
  ApplicationStatus,
  AutomationMode,
  QuestionStatus,
  QuestionType,
} from "@/types/api";

type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "brand";

const STATUS: Record<
  ApplicationStatus,
  { label: string; variant: BadgeVariant }
> = {
  draft: { label: "Draft", variant: "neutral" },
  analyzing: { label: "Analyzing", variant: "brand" },
  awaiting_user_information: { label: "Needs information", variant: "warning" },
  ready_for_review: { label: "Ready to review", variant: "brand" },
  awaiting_approval: { label: "Awaiting approval", variant: "success" },
  submitting: { label: "Submitting", variant: "brand" },
  submitted: { label: "Submitted", variant: "success" },
  failed: { label: "Failed", variant: "danger" },
  needs_user_action: { label: "Finish manually", variant: "warning" },
  cancelled: { label: "Cancelled", variant: "neutral" },
};

const WORKFLOW_LABEL: Record<string, string> = {
  application_created: "Application created",
  needs_information: "Needs information",
  needs_approval: "Needs your approval",
  approved: "Approved",
  browser_session_ready: "Employer page open",
  form_analysis_pending: "Form analysis pending",
  filling: "Filling safe fields",
  paused_for_user: "Paused for you",
  waiting_for_confirmation: "Waiting for confirmation",
  ready_for_final_review: "Ready for final review",
  awaiting_user_submission: "Awaiting your submission",
  session_expired: "Browser session expired",
  submitted: "Submitted",
  failed: "Needs attention",
  cancelled: "Cancelled",
};

const SUBMISSION_PHASE_LABEL: Record<string, string> = {
  ready_for_final_review: "Final review ready",
  awaiting_user_submission: "Submit on the employer's site",
  user_confirmed_submitted: "You confirmed submission",
  submission_confirmed: "Employer confirmation recorded",
};

function WorkflowSummary({ applicationId }: { applicationId: string }) {
  const workflow = useApplicationWorkflow(applicationId);
  if (workflow.isLoading) return <Skeleton className="h-20 w-full" />;
  if (workflow.error || !workflow.data) return null;
  const data = workflow.data;
  const next = data.next_actions.find((action) => action.enabled);
  return (
    <Card>
      <CardContent className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-ink-subtle">
            <Workflow aria-hidden="true" className="h-3.5 w-3.5" />
            Workflow
          </p>
          <p className="mt-1 text-[13px] font-medium text-ink">
            {WORKFLOW_LABEL[data.workflow_stage] ?? data.workflow_stage}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-subtle">
            {data.blocking_items.length > 0
              ? `${data.blocking_items.length} blocking item${
                  data.blocking_items.length === 1 ? "" : "s"
                }: ${data.blocking_items[0]}`
              : data.ready_for_final_review
                ? "All safe fields are done. Review the employer's page, then submit it yourself."
                : "Progress is saved after every step."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {data.submission_phase ? (
            <Badge variant="brand">
              {SUBMISSION_PHASE_LABEL[data.submission_phase] ??
                data.submission_phase}
            </Badge>
          ) : null}
          {next ? <Badge variant="neutral">Next: {next.label}</Badge> : null}
        </div>
        {data.recovery.length > 0 ? (
          <div className="w-full space-y-1">
            {data.recovery.map((hint) => (
              <p key={hint} className="text-[11px] text-amber-200/80">
                {hint}
              </p>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

const STAGES: Array<{ value: ApplicationStage; label: string }> = [
  { value: "applied", label: "Applied" },
  { value: "screening", label: "Screening" },
  { value: "interview", label: "Interview" },
  { value: "offer", label: "Offer" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
];

const SOURCE: Record<AnswerSource, { label: string; variant: BadgeVariant }> = {
  profile: { label: "Verified profile", variant: "success" },
  resume: { label: "Resume suggestion", variant: "warning" },
  previously_approved: { label: "Reused approved answer", variant: "brand" },
  ai_suggestion: { label: "AI draft — review", variant: "danger" },
  user_input: { label: "Your input", variant: "neutral" },
};

const MODE: Record<AutomationMode, string> = {
  automated: "Automated form filling",
  assisted: "Prepared for you to submit",
  manual: "Guided manual workflow",
};

const QUESTION_STATUS: Record<
  QuestionStatus,
  { label: string; variant: BadgeVariant }
> = {
  missing: { label: "Missing", variant: "warning" },
  suggested: { label: "Needs confirmation", variant: "brand" },
  confirmed: { label: "Confirmed", variant: "success" },
};

const inputClass =
  "w-full rounded-xl border border-white/10 bg-navy-950/60 px-3 py-2 text-[13px] text-ink placeholder:text-ink-subtle transition-colors focus:border-brand-blue/60 focus:outline-none focus:ring-2 focus:ring-brand-blue/25 disabled:cursor-not-allowed disabled:opacity-60";

const EDITABLE: ApplicationStatus[] = [
  "awaiting_user_information",
  "ready_for_review",
  "awaiting_approval",
  "failed",
];

function StatusBadge({ status }: { status: ApplicationStatus }) {
  const meta = STATUS[status];
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div
      className="h-2 overflow-hidden rounded-full bg-white/[0.06]"
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Questionnaire completion"
    >
      <div
        className="h-full rounded-full bg-gradient-to-r from-brand-indigo to-brand-blue transition-all"
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

function MultiChoiceControl({
  question,
  value,
  setValue,
  disabled,
}: {
  question: ApplicationQuestion;
  value: string;
  setValue: (next: string) => void;
  disabled: boolean;
}) {
  const selected = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

  if (question.options.length === 0) {
    return (
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        rows={3}
        className={inputClass}
        aria-label={question.question_text}
        disabled={disabled}
      />
    );
  }

  return (
    <div className="space-y-2">
      {question.options.map((option) => {
        const checked = selected.includes(option);
        return (
          <label
            key={option}
            className={cn(
              "flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2 text-[13px] transition-colors",
              checked
                ? "border-brand-blue/35 bg-brand-blue/10 text-ink"
                : "border-white/10 bg-white/[0.02] text-ink-muted hover:border-white/20",
            )}
          >
            <input
              type="checkbox"
              checked={checked}
              disabled={disabled}
              onChange={(event) => {
                const next = event.target.checked
                  ? [...selected, option]
                  : selected.filter((item) => item !== option);
                setValue(next.join(", "));
              }}
              className="h-4 w-4 rounded border-white/20 bg-white/[0.04]"
            />
            {option}
          </label>
        );
      })}
    </div>
  );
}

function fieldControl(
  question: ApplicationQuestion,
  value: string,
  setValue: (next: string) => void,
  editable: boolean,
) {
  const disabled = !editable || question.field_type === "file";
  if (question.field_type === "textarea") {
    return (
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        rows={4}
        className={inputClass}
        aria-label={question.question_text}
        disabled={disabled}
      />
    );
  }
  if (question.field_type === "multi_choice" || question.field_type === "checkbox") {
    return (
      <MultiChoiceControl
        question={question}
        value={value}
        setValue={setValue}
        disabled={disabled}
      />
    );
  }
  if (question.field_type === "radio") {
    const options = question.options.length > 0 ? question.options : ["Yes", "No"];
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
              name={`question-${question.id}`}
              value={option}
              checked={value === option}
              onChange={() => setValue(option)}
              disabled={disabled}
              className="h-4 w-4 border-white/20 bg-white/[0.04]"
            />
            {option}
          </label>
        ))}
      </div>
    );
  }
  if (question.field_type === "single_choice" || question.field_type === "boolean") {
    const options =
      question.options.length > 0
        ? question.options
        : question.field_type === "boolean"
          ? ["Yes", "No"]
          : [];
    return (
      <select
        value={value}
        onChange={(event) => setValue(event.target.value)}
        className={inputClass}
        aria-label={question.question_text}
        disabled={disabled}
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
  if (question.field_type === "declaration") {
    return (
      <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5">
        <input
          type="checkbox"
          checked={value === "Yes"}
          onChange={(event) => setValue(event.target.checked ? "Yes" : "")}
          className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04]"
          disabled={disabled}
        />
        <span className="text-[12px] leading-relaxed text-ink-muted">
          I have read and confirm this declaration.
        </span>
      </label>
    );
  }
  if (question.field_type === "file") {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5 text-[13px] text-ink">
        <FileText aria-hidden="true" className="h-4 w-4 text-sky-300" />
        {value || "No document selected"}
      </div>
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
      type={type[question.field_type as QuestionType] ?? "text"}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      className={inputClass}
      aria-label={question.question_text}
      disabled={disabled}
    />
  );
}

function QuestionField({
  question,
  editable,
  saving,
  onSave,
}: {
  question: ApplicationQuestion;
  editable: boolean;
  saving: boolean;
  onSave: (answer: string | null, confirmed: boolean) => void;
}) {
  const [value, setValue] = useState(
    question.answer ?? question.suggested_answer ?? "",
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const previousAnswer = useRef(question.answer);

  useEffect(() => {
    if (previousAnswer.current !== question.answer) {
      // Only overwrite local edits when the server value actually changed.
      previousAnswer.current = question.answer;
      setValue(question.answer ?? "");
    }
  }, [question.answer]);

  const status = QUESTION_STATUS[question.status];
  const source = SOURCE[question.answer_source];
  const needsAttention = question.status !== "confirmed";
  const needsFreshConfirmation = question.is_sensitive || question.is_legal;

  const confirm = () => {
    onSave(value.trim() || null, true);
    setConfirmOpen(false);
    setAcknowledged(false);
  };

  return (
    <div
      className={cn(
        "rounded-xl border p-4 transition-colors",
        needsAttention
          ? "border-amber-500/20 bg-amber-500/[0.03]"
          : "border-white/[0.07] bg-white/[0.02]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-ink">
            {question.question_text}
            {question.required ? (
              <span className="ml-1 text-red-300" aria-label="required">
                *
              </span>
            ) : (
              <span className="ml-1.5 text-[10px] uppercase tracking-[0.08em] text-ink-subtle">
                optional
              </span>
            )}
          </p>
          {question.help_text ? (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-subtle">
              {question.help_text}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {question.is_sensitive ? (
            <Badge variant="warning">
              <ShieldCheck aria-hidden="true" className="h-3 w-3" />
              Sensitive
            </Badge>
          ) : null}
          {question.is_legal ? (
            <Badge variant="danger">
              <Lock aria-hidden="true" className="h-3 w-3" />
              Legal
            </Badge>
          ) : null}
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
      </div>

      <div className="mt-3">{fieldControl(question, value, setValue, editable)}</div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={source.variant}>{source.label}</Badge>
          {question.answer_source === "resume" ? (
            <span className="text-[11px] text-ink-subtle">
              Verify this against the actual resume before confirming.
            </span>
          ) : null}
          {question.answer_source === "ai_suggestion" ? (
            <span className="text-[11px] text-ink-subtle">
              Never saved to your profile as fact.
            </span>
          ) : null}
          {question.confidence > 0 && question.confidence < 1 ? (
            <span className="text-[11px] text-ink-subtle">
              {Math.round(question.confidence * 100)}% confidence
            </span>
          ) : null}
        </div>

        {editable ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              disabled={saving || value === (question.answer ?? "")}
              onClick={() => onSave(value.trim() || null, false)}
            >
              Save draft
            </Button>
            <Button
              size="sm"
              variant={needsAttention ? "primary" : "secondary"}
              disabled={saving || !value.trim()}
              onClick={() => {
                if (needsFreshConfirmation) {
                  setConfirmOpen(true);
                } else {
                  confirm();
                }
              }}
            >
              {saving ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Saving…
                </>
              ) : (
                <>
                  <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
                  Confirm answer
                </>
              )}
            </Button>
          </div>
        ) : null}
      </div>

      {confirmOpen ? (
        <div className="mt-3 space-y-2.5 rounded-xl border border-amber-500/25 bg-amber-500/[0.05] p-3">
          <p className="text-[12px] leading-relaxed text-amber-100">
            {question.is_legal
              ? "This is a legal declaration. Read it carefully; it is confirmed per application and never reused automatically."
              : "This answer is sensitive. Confirm that the value is accurate for this application."}
          </p>
          <label className="flex cursor-pointer items-start gap-2 text-[12px] leading-relaxed text-amber-200/90">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04]"
            />
            I confirm the value above is correct and may be used for this
            application.
          </label>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={!acknowledged || saving} onClick={confirm}>
              Confirm and save
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setConfirmOpen(false);
                setAcknowledged(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

    </div>
  );
}

function ResumePanel({
  applicationId,
  preview,
  editable,
}: {
  applicationId: string;
  preview: ApplicationPreview;
  editable: boolean;
}) {
  const resumes = useResumes();
  const selectResume = useSelectApplicationResume(applicationId);
  const current = preview.resume;
  const [selected, setSelected] = useState(current.id ?? "");
  const items = resumes.data ?? [];
  const chosen = selected || current.id || "";

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Resume for this application</CardTitle>
          <CardDescription>
            Chosen by the matching engine. You can override it here without
            changing the original PDF.
          </CardDescription>
        </div>
        {current.score !== null ? (
          <Badge variant={current.score >= 75 ? "success" : "warning"}>
            {current.score}% match
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-sky-300">
            <FileText aria-hidden="true" className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-[13px] font-medium text-ink">
              {current.filename ?? "No resume selected"}
            </p>
            <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">
              {current.reason || "Select a resume before approving the application."}
            </p>
          </div>
        </div>

        {items.length === 0 ? (
          <p className="text-[12px] text-ink-muted">
            Upload a PDF resume in the{" "}
            <Link href="/resumes" className="text-sky-300 hover:underline">
              resume manager
            </Link>{" "}
            to include it here.
          </p>
        ) : editable ? (
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              value={chosen}
              onChange={(event) => setSelected(event.target.value)}
              className={inputClass}
              aria-label="Choose a resume"
            >
              <option value="">Select a resume…</option>
              {items.map((resume) => (
                <option key={resume.id} value={resume.id}>
                  {resume.original_filename}
                  {resume.is_default ? " (default)" : ""}
                </option>
              ))}
            </select>
            <Button
              variant="secondary"
              disabled={!chosen || chosen === current.id || selectResume.isPending}
              onClick={() => selectResume.mutate(chosen)}
              className="shrink-0"
            >
              {selectResume.isPending ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Selecting…
                </>
              ) : (
                "Use this resume"
              )}
            </Button>
          </div>
        ) : null}

        {selectResume.error ? (
          <Alert variant="danger" title="Could not change the resume">
            {describeError(selectResume.error)}
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ApplicationUrlPanel({
  applicationId,
  preview,
}: {
  applicationId: string;
  preview: ApplicationPreview;
}) {
  const link = safeExternalUrl(preview.application_url);
  const editable = EDITABLE.includes(preview.status);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Employer application page</CardTitle>
          <CardDescription>
            The job listing, the employer careers page and the actual
            application form are tracked separately. ApplyPilot opens only the
            employer&apos;s own form and never scrapes job boards.
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="brand">{preview.platform_name}</Badge>
          <Badge variant="neutral">{MODE[preview.automation_mode]}</Badge>
          {preview.application_url_is_job_board ? (
            <Badge variant="warning">Job listing only</Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <EmployerApplicationUrlForm
          applicationId={applicationId}
          initialApplicationUrl={preview.application_url}
          jobListingUrl={preview.job_listing_url}
          employerCareersUrl={preview.employer_careers_url}
          suggestedApplicationUrl={preview.suggested_application_url}
          isJobBoard={preview.application_url_is_job_board}
          disabled={!editable}
          submitLabel="Validate URL and inspect form"
        />

        {link && !preview.application_url_is_job_board ? (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-[12px] text-sky-300 hover:underline"
          >
            Open the employer&apos;s page
            <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
          </a>
        ) : null}

        {preview.requires_account ? (
          <Alert variant="warning" title="A company account may be required">
            Create the account yourself on the employer&apos;s site. ApplyPilot
            never creates accounts, stores company passwords, or completes OTP,
            MFA or CAPTCHA steps for you.
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SectionStatus({ section }: { section: ApplicationSection }) {
  const confirmed = section.questions.filter(
    (question) => question.status === "confirmed",
  ).length;
  const requiredLeft = section.questions.filter(
    (question) => question.required && question.status !== "confirmed",
  ).length;
  const unconfirmed = section.questions.length - confirmed;
  return (
    <span className="flex items-center gap-1.5">
      {requiredLeft > 0 ? (
        <Badge variant="warning">{requiredLeft} left</Badge>
      ) : unconfirmed > 0 ? (
        <Badge variant="neutral">Optional</Badge>
      ) : (
        <Badge variant="success">Done</Badge>
      )}
      <span className="text-[10px] text-ink-subtle">
        {confirmed}/{section.questions.length}
      </span>
    </span>
  );
}

export function ApplicationWorkspace({ applicationId }: { applicationId: string }) {
  const application = useApplication(applicationId);
  const updateQuestion = useUpdateApplicationQuestion(applicationId);
  const approve = useApproveApplication(applicationId);
  const submit = useSubmitApplication(applicationId);
  const markSubmitted = useConfirmManualSubmission(applicationId);
  const updateStage = useUpdateApplicationStage(applicationId);

  const [stepIndex, setStepIndex] = useState(0);
  const [hideConfirmed, setHideConfirmed] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [reference, setReference] = useState("");
  const [stage, setStage] = useState<ApplicationStage>("applied");

  if (application.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (application.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load this application">
            {describeError(application.error)}
          </Alert>
          <div className="mt-4 flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void application.refetch()}
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

  const preview = application.data;
  const editable = EDITABLE.includes(preview.status);
  const questions = preview.sections.flatMap((section) => section.questions);
  const attention = questions.filter((question) => question.status !== "confirmed");
  const submitted = preview.submitted_at !== null;

  const steps: Array<
    | { kind: "section"; section: ApplicationSection }
    | { kind: "review" }
  > = [
    ...preview.sections.map((section) => ({ kind: "section" as const, section })),
    { kind: "review" as const },
  ];
  const safeStep = Math.min(stepIndex, steps.length - 1);
  const currentStep = steps[safeStep];
  const currentSection =
    currentStep?.kind === "section" ? currentStep.section : null;
  const sectionQuestions = (currentSection?.questions ?? []).filter(
    (question) => !hideConfirmed || question.status !== "confirmed",
  );

  const goTo = (index: number) => {
    const next = Math.max(0, Math.min(index, steps.length - 1));
    setStepIndex(next);
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  const jumpToQuestion = (questionKey: string) => {
    const index = steps.findIndex(
      (step) =>
        step.kind === "section" &&
        step.section.questions.some(
          (question) => question.question_key === questionKey,
        ),
    );
    if (index >= 0) {
      setStepIndex(index);
      if (typeof window !== "undefined") {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }
  };

  return (
    <div className="space-y-5">
      <Link
        href="/applications"
        className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted hover:text-ink"
      >
        <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
        All applications
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            {preview.job_title}
          </h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            {preview.company ?? "Company not listed"} · {preview.platform_name}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <StatusBadge status={preview.status} />
            {preview.stage ? (
              <Badge variant="brand">
                {STAGES.find((item) => item.value === preview.stage)?.label ??
                  preview.stage}
              </Badge>
            ) : null}
            <Badge variant="neutral">{MODE[preview.automation_mode]}</Badge>
            {preview.submitted_at ? (
              <Badge variant="neutral">
                Submitted {formatDate(preview.submitted_at)}
              </Badge>
            ) : null}
          </div>
        </div>
        <Link
          href="/profile"
          className={buttonClasses({ variant: "secondary", size: "sm" })}
        >
          <UserRound aria-hidden="true" className="h-3.5 w-3.5" />
          Edit source profile
        </Link>
      </div>

      <Card>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-[13px] font-medium text-ink">
                {preview.progress.confirmed} of {preview.progress.total} answers
                confirmed
              </p>
              <p className="mt-0.5 text-[11px] text-ink-subtle">
                {preview.progress.required_confirmed} of{" "}
                {preview.progress.required_total} required questions answered.
                Progress is saved after every draft and confirmation.
              </p>
            </div>
            <span className="text-sm font-semibold text-ink">
              {preview.progress.percent}%
            </span>
          </div>
          <ProgressBar percent={preview.progress.percent} />

          {preview.warnings.length > 0 ? (
            <div className="space-y-2">
              {preview.warnings.map((warning) => (
                <Alert key={warning} variant="info">
                  {warning}
                </Alert>
              ))}
            </div>
          ) : null}

          {preview.failure_reason ? (
            <Alert variant="danger" title="Last submission attempt">
              {preview.failure_reason}
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      <ApplicationEligibilityPanel applicationId={applicationId} />

      <WorkflowSummary applicationId={applicationId} />

      <ApplicationBrowserWorkspace
        applicationId={applicationId}
        company={preview.company}
        applicationUrl={preview.application_url}
        jobListingUrl={preview.job_listing_url}
        employerCareersUrl={preview.employer_careers_url}
        applicationUrlIsJobBoard={preview.application_url_is_job_board}
        suggestedApplicationUrl={preview.suggested_application_url}
      />

      <ApplicationFillPanel applicationId={applicationId} />

      <ApplicationHumanLoopPanel applicationId={applicationId} />

      <div className="grid gap-5 lg:grid-cols-[250px_minmax(0,1fr)]">
        <nav aria-label="Questionnaire sections" className="lg:sticky lg:top-24 lg:self-start">
          <div className="flex gap-2 overflow-x-auto pb-2 lg:flex-col lg:overflow-visible lg:pb-0">
            {steps.map((step, index) => {
              const active = index === safeStep;
              const label =
                step.kind === "review" ? "Review & approve" : step.section.label;
              const isReview = step.kind === "review";
              return (
                <button
                  key={isReview ? "review" : step.section.section}
                  type="button"
                  onClick={() => goTo(index)}
                  aria-current={active ? "step" : undefined}
                  className={cn(
                    "flex min-w-[190px] items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-left transition-colors lg:min-w-0",
                    active
                      ? "border-brand-blue/35 bg-brand-blue/[0.09]"
                      : "border-white/[0.07] bg-white/[0.02] hover:border-white/[0.15]",
                  )}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      className={cn(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold",
                        active
                          ? "border-brand-blue/40 bg-brand-blue/20 text-sky-200"
                          : "border-white/10 bg-white/[0.04] text-ink-subtle",
                      )}
                    >
                      {index + 1}
                    </span>
                    <span
                      className={cn(
                        "truncate text-[12px]",
                        active ? "text-ink" : "text-ink-muted",
                      )}
                    >
                      {label}
                    </span>
                  </span>
                  {isReview ? (
                    attention.length > 0 ? (
                      <Badge variant="warning">{attention.length}</Badge>
                    ) : (
                      <CheckCircle2
                        aria-hidden="true"
                        className="h-3.5 w-3.5 text-emerald-300"
                      />
                    )
                  ) : (
                    <SectionStatus section={step.section} />
                  )}
                </button>
              );
            })}
          </div>
        </nav>

        <div className="min-w-0 space-y-4">
          {currentStep?.kind === "section" && currentSection ? (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>{currentSection.label}</CardTitle>
                  <CardDescription>
                    {currentSection.section === "documents"
                      ? "The resume and employer page used for this application."
                      : "Answers are prefilled from your verified profile, resume and previously approved answers. Missing required fields are highlighted."}
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <Badge
                    variant={
                      currentSection.questions.every(
                        (question) => question.status === "confirmed",
                      )
                        ? "success"
                        : "warning"
                    }
                  >
                    {currentSection.questions.filter(
                      (question) => question.status === "confirmed",
                    ).length}{" "}
                    / {currentSection.questions.length}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setHideConfirmed((value) => !value)}
                  >
                    {hideConfirmed ? "Show all" : "Hide answered"}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {currentSection.section === "documents" ? (
                  <div className="space-y-4">
                    <ResumePanel
                      applicationId={applicationId}
                      preview={preview}
                      editable={editable}
                    />
                    <ApplicationUrlPanel
                      applicationId={applicationId}
                      preview={preview}
                    />
                  </div>
                ) : null}

                {updateQuestion.error ? (
                  <Alert variant="danger" title="Answer was not saved">
                    {describeError(updateQuestion.error)}
                  </Alert>
                ) : null}

                {sectionQuestions.length === 0 ? (
                  <EmptyState
                    icon={Sparkles}
                    title="Everything in this section is confirmed"
                    description="You can still review every answer before approving the application."
                    action={
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => setHideConfirmed(false)}
                      >
                        Show all answers
                      </Button>
                    }
                  />
                ) : (
                  <div className="space-y-3">
                    {sectionQuestions.map((question) => (
                      <QuestionField
                        key={question.id}
                        question={question}
                        editable={editable}
                        saving={
                          updateQuestion.isPending &&
                          updateQuestion.variables?.questionId === question.id
                        }
                        onSave={(answer, confirmed) =>
                          updateQuestion.mutate({
                            questionId: question.id,
                            answer,
                            confirmed,
                          })
                        }
                      />
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Review and submission</CardTitle>
                  <CardDescription>
                    ApplyPilot fills safe fields and never clicks Submit, Apply or
                    Finish. You submit the application yourself on the employer&apos;s
                    page; approval records what you reviewed.
                  </CardDescription>
                </div>
                {preview.approved_at ? (
                  <Badge variant="success">
                    Approved {formatDateTime(preview.approved_at)}
                  </Badge>
                ) : null}
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                    <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
                      Answers confirmed
                    </p>
                    <p className="mt-1 text-lg font-semibold text-ink">
                      {preview.progress.confirmed}/{preview.progress.total}
                    </p>
                  </div>
                  <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                    <p className="text-[10px] uppercase tracking-[0.1em] text-ink-subtle">
                      Resume
                    </p>
                    <p className="mt-1 truncate text-[12px] text-ink">
                      {preview.resume.filename ?? "No resume selected"}
                    </p>
                  </div>
                </div>

                {preview.missing_required.length > 0 ? (
                  <div>
                    <p className="text-[12px] font-medium text-amber-200">
                      Still required before approval
                    </p>
                    <ul className="mt-2 space-y-1.5">
                      {preview.missing_required.map((item) => (
                        <li key={item.question_key}>
                          <button
                            type="button"
                            onClick={() => jumpToQuestion(item.question_key)}
                            className="flex w-full items-start gap-2 rounded-lg px-1 py-0.5 text-left text-[12px] text-ink-muted hover:bg-white/[0.04] hover:text-ink"
                          >
                            <AlertTriangle
                              aria-hidden="true"
                              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300"
                            />
                            {item.question_text}
                            <ChevronRight
                              aria-hidden="true"
                              className="ml-auto mt-0.5 h-3.5 w-3.5 shrink-0"
                            />
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <Alert variant="success" title="All required answers are confirmed">
                    Review the answers and resume before approving.
                  </Alert>
                )}

                {preview.submitted_at ? (
                  <Alert variant="success" title="Application submitted">
                    {preview.submission_reference
                      ? `Reference: ${preview.submission_reference}`
                      : "The submission is recorded on this application."}
                  </Alert>
                ) : null}

                {preview.status === "failed" ? (
                  <Alert variant="danger" title="Automation did not complete">
                    {preview.failure_reason ??
                      "Complete the application directly on the employer's site."}
                  </Alert>
                ) : null}

                {!submitted ? (
                  <>
                    <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2.5">
                      <input
                        type="checkbox"
                        checked={acknowledged}
                        onChange={(event) => setAcknowledged(event.target.checked)}
                        className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04]"
                      />
                      <span className="text-[12px] leading-relaxed text-ink-muted">
                        I reviewed every answer and the selected resume. I
                        understand ApplyPilot will not submit the application; I
                        will complete the final submission on the employer&apos;s
                        site.
                      </span>
                    </label>

                    {approve.error ? (
                      <Alert variant="danger" title="Approval failed">
                        {describeError(approve.error)}
                      </Alert>
                    ) : null}
                    {submit.error ? (
                      <Alert variant="danger" title="Submission not completed">
                        {describeError(submit.error)}
                      </Alert>
                    ) : null}

                    <div className="flex flex-wrap gap-2">
                      {preview.approved_at ? (
                        <>
                          <Button
                            variant="ghost"
                            onClick={() => approve.mutate(false)}
                            disabled={approve.isPending}
                          >
                            Withdraw approval
                          </Button>
                          <Button
                            onClick={() => submit.mutate()}
                            disabled={!preview.can_submit || submit.isPending}
                          >
                            {submit.isPending ? (
                              <>
                                <Spinner className="h-4 w-4" />
                                Preparing…
                              </>
                            ) : (
                              <>
                                <ArrowUpRight
                                  aria-hidden="true"
                                  className="h-4 w-4"
                                />
                                Continue to manual submission
                              </>
                            )}
                          </Button>
                        </>
                      ) : (
                        <Button
                          onClick={() => approve.mutate(true)}
                          disabled={
                            !acknowledged ||
                            !preview.ready_to_approve ||
                            approve.isPending
                          }
                        >
                          {approve.isPending ? (
                            <>
                              <Spinner className="h-4 w-4" />
                              Approving…
                            </>
                          ) : (
                            <>
                              <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
                              Approve application
                            </>
                          )}
                        </Button>
                      )}
                    </div>

                    <p className="text-[11px] leading-relaxed text-ink-subtle">
                      Approval is bound to the exact answers and resume above.
                      ApplyPilot never submits the application for you: after the
                      handoff steps appear, finish the form on the employer&apos;s
                      site and mark it as submitted. Editing anything afterwards
                      requires you to approve again.
                    </p>
                  </>
                ) : null}

                {preview.status === "needs_user_action" ? (
                  <div className="space-y-3 rounded-xl border border-amber-500/25 bg-amber-500/[0.04] p-4">
                    <div>
                      <p className="text-[13px] font-medium text-amber-100">
                        Finish this application yourself
                      </p>
                      <p className="mt-1 text-[12px] leading-relaxed text-amber-200/80">
                        ApplyPilot did not submit anything. Complete the steps below,
                        then record the submission.
                      </p>
                    </div>
                    <ol className="space-y-1.5">
                      {(submit.data?.steps ?? preview.manual_steps).map((step, index) => (
                        <li
                          key={step}
                          className="flex gap-2 text-[12px] leading-relaxed text-ink-muted"
                        >
                          <span className="text-ink-subtle">{index + 1}.</span>
                          {step}
                        </li>
                      ))}
                    </ol>
                    <div className="flex flex-col gap-2 sm:flex-row">
                      <input
                        value={reference}
                        onChange={(event) => setReference(event.target.value)}
                        className={inputClass}
                        placeholder="Confirmation reference (optional)"
                        aria-label="Confirmation reference"
                      />
                      <Button
                        variant="secondary"
                        className="shrink-0"
                        disabled={markSubmitted.isPending}
                        onClick={() => markSubmitted.mutate(reference.trim() || null)}
                      >
                        {markSubmitted.isPending ? (
                          <>
                            <Spinner className="h-4 w-4" />
                            Saving…
                          </>
                        ) : (
                          "Mark as submitted"
                        )}
                      </Button>
                    </div>
                    {markSubmitted.error ? (
                      <Alert variant="danger" title="Could not record the submission">
                        {describeError(markSubmitted.error)}
                      </Alert>
                    ) : null}
                  </div>
                ) : null}

                {submitted ? (
                  <div className="space-y-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
                    <p className="text-[12px] font-medium text-ink">Status tracking</p>
                    <p className="text-[11px] text-ink-muted">
                      Submitted {formatDateTime(preview.submitted_at)}. Update the
                      stage as the company responds.
                    </p>
                    <div className="flex flex-col gap-2 sm:flex-row">
                      <select
                        value={preview.stage ?? stage}
                        onChange={(event) => {
                          const value = event.target.value as ApplicationStage;
                          setStage(value);
                          updateStage.mutate(value);
                        }}
                        className={inputClass}
                        aria-label="Application stage"
                        disabled={updateStage.isPending}
                      >
                        {STAGES.map((item) => (
                          <option key={item.value} value={item.value}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    {updateStage.error ? (
                      <Alert variant="danger" title="Could not update the stage">
                        {describeError(updateStage.error)}
                      </Alert>
                    ) : null}
                  </div>
                ) : null}
              </CardContent>
            </Card>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => goTo(safeStep - 1)}
              disabled={safeStep === 0}
            >
              <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
              Previous
            </Button>
            <p className="text-[11px] text-ink-subtle">
              {preview.progress.percent}% complete · saved as you go
            </p>
            <Button
              size="sm"
              onClick={() => goTo(safeStep + 1)}
              disabled={safeStep >= steps.length - 1}
            >
              Next
              <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
