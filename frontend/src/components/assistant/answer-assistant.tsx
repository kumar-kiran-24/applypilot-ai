"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  BookOpen,
  Check,
  Clipboard,
  Copy,
  FileText,
  RefreshCw,
  Sparkles,
  Wand2,
} from "lucide-react";

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
import { useApplications, useApplication, useUpdateApplicationQuestion } from "@/features/applications/hooks";
import { useDraftAnswer } from "@/features/assistant/hooks";
import { useReusableAnswers, useUpsertReusableAnswer } from "@/features/profile/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDate } from "@/lib/utils/format";
import type {
  AnswerDraft,
  AnswerDraftEvidence,
  ApplicationQuestion,
  ApplicationStatus,
} from "@/types/api";

const STATUS_LABEL: Record<ApplicationStatus, string> = {
  draft: "Draft",
  analyzing: "Analyzing",
  awaiting_user_information: "Needs information",
  ready_for_review: "Ready to review",
  awaiting_approval: "Awaiting approval",
  submitting: "Submitting",
  submitted: "Submitted",
  failed: "Failed",
  needs_user_action: "Finish manually",
  cancelled: "Cancelled",
};

const KIND_LABEL: Record<AnswerDraftEvidence["kind"], string> = {
  profile: "Profile",
  resume: "Resume",
  job: "Job posting",
  answer_bank: "Saved answer",
};

const KIND_VARIANT: Record<
  AnswerDraftEvidence["kind"],
  "brand" | "success" | "neutral" | "warning"
> = {
  profile: "success",
  resume: "brand",
  job: "neutral",
  answer_bank: "warning",
};

const LLM_LABEL: Record<string, string> = {
  used: "Drafted by the configured model",
  disabled: "Deterministic evidence only",
  not_configured: "LLM not configured",
  failed: "LLM unavailable",
};

function questionLabel(question: ApplicationQuestion) {
  const required = question.required ? " (required)" : "";
  return `${question.question_text}${required}`;
}

export function AnswerAssistant() {
  const applications = useApplications();
  const items = useMemo(
    () => applications.data?.applications ?? [],
    [applications.data],
  );
  const [applicationId, setApplicationId] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [customQuestion, setCustomQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [draft, setDraft] = useState<AnswerDraft | null>(null);
  const [copied, setCopied] = useState(false);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const application = useApplication(applicationId, Boolean(applicationId));
  const preview = application.data;
  const questions = useMemo(
    () => (preview?.sections ?? []).flatMap((section) => section.questions),
    [preview],
  );
  const selectedQuestion = questions.find((item) => item.id === questionId);
  const reusable = useReusableAnswers();
  const draftAnswer = useDraftAnswer();
  const updateQuestion = useUpdateApplicationQuestion(applicationId);
  const saveReusable = useUpsertReusableAnswer();

  useEffect(() => {
    const first = items[0];
    if (!applicationId && first) {
      setApplicationId(first.id);
    }
  }, [applicationId, items]);

  useEffect(() => {
    // A new application or question starts a new draft.
    setDraft(null);
    setAnswer("");
    setSavedMessage(null);
  }, [applicationId, questionId]);

  const questionText = selectedQuestion?.question_text ?? customQuestion.trim();
  const canGenerate =
    Boolean(applicationId) && questionText.length >= 3 && !draftAnswer.isPending;

  const generate = () => {
    if (!applicationId) return;
    setSavedMessage(null);
    draftAnswer.mutate(
      {
        application_id: applicationId,
        ...(selectedQuestion
          ? { question_id: selectedQuestion.id }
          : { question_text: customQuestion.trim() }),
      },
      {
        onSuccess: (data) => {
          setDraft(data);
          setAnswer(data.draft_answer ?? "");
        },
      },
    );
  };

  const copyAnswer = async () => {
    if (!answer.trim()) return;
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access can be denied; the textarea remains selectable.
      setCopied(false);
    }
  };

  const saveToApplication = () => {
    if (!draft?.question_id || !answer.trim()) return;
    updateQuestion.mutate(
      { questionId: draft.question_id, answer: answer.trim(), confirmed: false },
      {
        onSuccess: () =>
          setSavedMessage(
            "Saved as a draft on the application. Review and confirm it in the application workspace.",
          ),
      },
    );
  };

  const saveToBank = () => {
    if (!draft || !answer.trim()) return;
    saveReusable.mutate(
      {
        key: draft.question_key,
        input: {
          question_text: draft.question_text,
          answer: answer.trim(),
          answer_source: "ai_suggestion",
          confidence: draft.confidence,
        },
      },
      {
        onSuccess: () =>
          setSavedMessage(
            "Saved to reusable answers as an unverified suggestion. Confirm it before it is reused.",
          ),
      },
    );
  };

  if (applications.isPending) {
    return (
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardContent className="space-y-3">
            <Skeleton className="h-5 w-56" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-32 w-full" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </CardContent>
        </Card>
      </div>
    );
  }

  if (applications.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load applications">
            {describeError(applications.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            className="mt-4"
            onClick={() => void applications.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (items.length === 0) {
    return (
      <Card>
        <CardContent>
          <EmptyState
            icon={Sparkles}
            title="Start an application to draft answers"
            description="The assistant uses the job posting, your verified profile and your resume. Open a job workspace and start an application, then come back here."
            action={
              <Link href="/jobs" className={buttonClasses({ size: "sm" })}>
                Browse jobs
              </Link>
            }
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Draft an answer</CardTitle>
              <CardDescription>
                Choose an application question, or ask your own. ApplyPilot
                retrieves evidence first and drafts only from it.
              </CardDescription>
            </div>
            <Badge variant="brand">Never auto-submitted</Badge>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label
                htmlFor="assistant-application"
                className="mb-1.5 block text-[12px] text-ink-muted"
              >
                Application
              </label>
              <select
                id="assistant-application"
                value={applicationId}
                onChange={(event) => {
                  setApplicationId(event.target.value);
                  setQuestionId("");
                  setCustomQuestion("");
                }}
                className="field"
              >
                {items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.job_title}
                    {item.company ? ` — ${item.company}` : ""} ·{" "}
                    {STATUS_LABEL[item.status]}
                  </option>
                ))}
              </select>
            </div>

            {application.isPending ? (
              <Skeleton className="h-20 w-full" />
            ) : application.isError ? (
              <Alert variant="danger" title="Could not load this application">
                {describeError(application.error)}
              </Alert>
            ) : preview ? (
              <>
                <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-ink">
                        {preview.job_title}
                      </p>
                      <p className="mt-0.5 text-[12px] text-ink-muted">
                        {preview.company ?? "Company not listed"} ·{" "}
                        {preview.platform_name}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="neutral">
                        {preview.progress.confirmed}/{preview.progress.total} answered
                      </Badge>
                      <Link
                        href={`/jobs/${preview.job_id}`}
                        className="text-[12px] text-sky-300 hover:text-sky-200"
                      >
                        Job context
                      </Link>
                      <Link
                        href={`/applications/${preview.application_id}`}
                        className="text-[12px] text-sky-300 hover:text-sky-200"
                      >
                        Open application
                      </Link>
                    </div>
                  </div>
                  {preview.resume.filename ? (
                    <p className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-subtle">
                      <FileText aria-hidden="true" className="h-3.5 w-3.5" />
                      Using {preview.resume.filename}
                    </p>
                  ) : (
                    <p className="mt-2 text-[11px] text-amber-300">
                      No resume is attached to this application.
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="assistant-question"
                    className="mb-1.5 block text-[12px] text-ink-muted"
                  >
                    Question
                  </label>
                  <select
                    id="assistant-question"
                    value={questionId}
                    onChange={(event) => {
                      setQuestionId(event.target.value);
                      setCustomQuestion("");
                    }}
                    className="field"
                  >
                    <option value="">Ask a different question</option>
                    {questions.map((question) => (
                      <option key={question.id} value={question.id}>
                        {questionLabel(question)}
                      </option>
                    ))}
                  </select>
                  {selectedQuestion ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <Badge variant="neutral">{selectedQuestion.section}</Badge>
                      {selectedQuestion.is_sensitive ? (
                        <Badge variant="warning">Sensitive</Badge>
                      ) : null}
                      {selectedQuestion.is_legal ? (
                        <Badge variant="danger">Legal</Badge>
                      ) : null}
                      <Badge variant="neutral">
                        {selectedQuestion.answer_source.replaceAll("_", " ")}
                      </Badge>
                    </div>
                  ) : null}
                </div>

                {selectedQuestion ? (
                  <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5 text-[13px] text-ink">
                    {selectedQuestion.question_text}
                  </div>
                ) : (
                  <div>
                    <label
                      htmlFor="assistant-custom-question"
                      className="mb-1.5 block text-[12px] text-ink-muted"
                    >
                      Your question
                    </label>
                    <textarea
                      id="assistant-custom-question"
                      value={customQuestion}
                      onChange={(event) => setCustomQuestion(event.target.value)}
                      rows={3}
                      placeholder="e.g. Tell me about a time you improved system reliability."
                      className="field"
                    />
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  <Button onClick={generate} disabled={!canGenerate}>
                    {draftAnswer.isPending ? (
                      <>
                        <Spinner className="h-4 w-4" />
                        Retrieving evidence…
                      </>
                    ) : draft ? (
                      <>
                        <RefreshCw aria-hidden="true" className="h-4 w-4" />
                        Regenerate
                      </>
                    ) : (
                      <>
                        <Wand2 aria-hidden="true" className="h-4 w-4" />
                        Generate answer
                      </>
                    )}
                  </Button>
                  {draftAnswer.isError ? (
                    <span className="text-[12px] text-red-300">
                      {describeError(draftAnswer.error)}
                    </span>
                  ) : null}
                  <span className="text-[11px] text-ink-subtle">
                    Nothing is saved until you choose to save it.
                  </span>
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>

        {draft || answer ? (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Review and edit</CardTitle>
                <CardDescription>
                  Every draft is editable. Confirm it in the application before
                  it can be used for a submission.
                </CardDescription>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {draft ? (
                  <>
                    <Badge
                      variant={
                        draft.confidence_label === "high"
                          ? "success"
                          : draft.confidence_label === "medium"
                            ? "warning"
                            : "danger"
                      }
                    >
                      {Math.round(draft.confidence * 100)}% ·{" "}
                      {draft.confidence_label} confidence
                    </Badge>
                    {draft.is_sensitive ? (
                      <Badge variant="warning">Sensitive</Badge>
                    ) : null}
                    {draft.is_legal ? <Badge variant="danger">Legal</Badge> : null}
                  </>
                ) : (
                  <Badge variant="neutral">Reused answer</Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label
                  htmlFor="assistant-answer"
                  className="mb-1.5 block text-[12px] text-ink-muted"
                >
                  Draft answer
                </label>
                <textarea
                  id="assistant-answer"
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  rows={8}
                  className="field"
                  placeholder={
                    draft?.draft_answer
                      ? undefined
                      : "No draft was generated. Use the evidence below to write your answer."
                  }
                />
                {draft ? (
                  <p className="mt-1.5 text-[11px] text-ink-subtle">
                    {LLM_LABEL[draft.llm_status] ?? draft.llm_status}
                    {draft.llm_model ? ` · ${draft.llm_model}` : ""} ·{" "}
                    {draft.evidence.filter((item) => item.used).length} evidence
                    item(s) cited
                  </p>
                ) : (
                  <p className="mt-1.5 text-[11px] text-ink-subtle">
                    Reused from your saved answers. Verify it is accurate for this
                    application before saving.
                  </p>
                )}
              </div>

              {draft && draft.warnings.length > 0 ? (
                <Alert variant="warning" title="Check before using">
                  <ul className="list-disc space-y-1 pl-4">
                    {draft.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </Alert>
              ) : null}

              {draft && draft.missing_information.length > 0 ? (
                <Alert
                  variant="warning"
                  title="Information still needed"
                >
                  <ul className="space-y-2">
                    {draft.missing_information.map((item) => (
                      <li key={`${item.field}-${item.question}`}>
                        <span className="font-medium text-amber-100">
                          {item.question}
                        </span>
                        <span className="block text-[12px] opacity-80">
                          {item.reason}
                          {item.sensitive ? " Sensitive: confirm this yourself." : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Alert>
              ) : null}

              {draft && draft.conflicts.length > 0 ? (
                <Alert variant="danger" title="Conflicting stored information">
                  <ul className="space-y-2">
                    {draft.conflicts.map((item) => (
                      <li key={`${item.question_key}-${item.conflicting_source}`}>
                        <span className="font-medium">{item.question_text}</span>
                        <span className="block text-[12px]">
                          Stored: {item.stored_answer} · {item.conflicting_source}:{" "}
                          {item.conflicting_answer}
                        </span>
                        <span className="block text-[12px] opacity-80">
                          {item.reason}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Alert>
              ) : null}

              {draft && draft.inferred_claims.length > 0 ? (
                <div>
                  <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                    Inferred, not stated
                  </p>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[12px] text-ink-muted">
                    {draft.inferred_claims.map((claim) => (
                      <li key={claim}>{claim}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void copyAnswer()}
                  disabled={!answer.trim()}
                >
                  {copied ? (
                    <>
                      <Check aria-hidden="true" className="h-3.5 w-3.5" />
                      Copied
                    </>
                  ) : (
                    <>
                      <Copy aria-hidden="true" className="h-3.5 w-3.5" />
                      Copy
                    </>
                  )}
                </Button>
                <Button
                  size="sm"
                  onClick={saveToApplication}
                  disabled={
                    !draft?.question_id ||
                    !answer.trim() ||
                    updateQuestion.isPending ||
                    Boolean(draft?.is_legal)
                  }
                >
                  {updateQuestion.isPending ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <Clipboard aria-hidden="true" className="h-3.5 w-3.5" />
                  )}
                  Save to application
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={saveToBank}
                  disabled={
                    !draft?.save_reusable_allowed ||
                    !answer.trim() ||
                    saveReusable.isPending ||
                    Boolean(draft?.is_legal)
                  }
                >
                  {saveReusable.isPending ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <BookOpen aria-hidden="true" className="h-3.5 w-3.5" />
                  )}
                  Save to reusable answers
                </Button>
              </div>

              {savedMessage ? (
                <Alert variant="success" title="Saved">
                  {savedMessage}
                </Alert>
              ) : null}
              {updateQuestion.isError || saveReusable.isError ? (
                <Alert variant="danger" title="Could not save">
                  {describeError(updateQuestion.error ?? saveReusable.error)}
                </Alert>
              ) : null}

              {draft ? (
                <p className="text-[11px] leading-relaxed text-ink-subtle">
                  {draft.disclaimer}
                </p>
              ) : null}
            </CardContent>
          </Card>
        ) : null}
      </div>

      <div className="space-y-4">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Evidence used</CardTitle>
              <CardDescription>
                Retrieved deterministically from your own data.
              </CardDescription>
            </div>
            <Badge variant="neutral">{draft?.evidence.length ?? 0}</Badge>
          </CardHeader>
          <CardContent>
            {draft ? (
              draft.evidence.length > 0 ? (
                <ul className="space-y-3">
                  {draft.evidence.map((item) => (
                    <li
                      key={item.id}
                      className={cn(
                        "rounded-xl border p-3",
                        item.used
                          ? "border-brand-blue/25 bg-brand-blue/[0.06]"
                          : "border-white/[0.06] bg-white/[0.02]",
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant={KIND_VARIANT[item.kind]}>
                          {KIND_LABEL[item.kind]}
                        </Badge>
                        <Badge variant={item.verified ? "success" : "warning"}>
                          {item.verified ? "Verified" : "Unverified"}
                        </Badge>
                        {item.used ? <Badge variant="brand">Used</Badge> : null}
                      </div>
                      <p className="mt-2 text-[12px] font-medium text-ink">
                        {item.label}
                      </p>
                      <p className="mt-1 line-clamp-4 text-[12px] leading-relaxed text-ink-muted">
                        {item.detail}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[12px] text-ink-subtle">
                  No evidence was found for this question yet.
                </p>
              )
            ) : (
              <p className="text-[12px] text-ink-subtle">
                Generate an answer to see the profile, resume and job evidence it
                used.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Reusable answers</CardTitle>
              <CardDescription>
                Confirmed answers are reused by the questionnaire only after they
                match the question.
              </CardDescription>
            </div>
            <Badge variant="neutral">{reusable.data?.length ?? 0}</Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            {reusable.isPending ? (
              <Skeleton className="h-16 w-full" />
            ) : reusable.isError ? (
              <Alert variant="danger" title="Could not load saved answers">
                {describeError(reusable.error)}
              </Alert>
            ) : (reusable.data?.length ?? 0) === 0 ? (
              <p className="text-[12px] text-ink-subtle">
                Nothing saved yet. Save a reviewed draft to reuse it later.
              </p>
            ) : (
              reusable.data?.slice(0, 8).map((item) => (
                <div
                  key={item.id}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant={
                        item.verification_status === "verified"
                          ? "success"
                          : "warning"
                      }
                    >
                      {item.verification_status}
                    </Badge>
                    {item.is_sensitive ? (
                      <Badge variant="warning">Sensitive</Badge>
                    ) : null}
                  </div>
                  <p className="mt-2 text-[12px] font-medium text-ink">
                    {item.question_text}
                  </p>
                  <p className="mt-1 line-clamp-3 text-[12px] text-ink-muted">
                    {item.answer ?? "No answer stored"}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!item.answer}
                      onClick={() => {
                        setAnswer(item.answer ?? "");
                        setSavedMessage(null);
                      }}
                    >
                      Load into editor
                    </Button>
                    <span className="text-[10px] text-ink-subtle">
                      {formatDate(item.updated_at)}
                    </span>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex gap-3 text-[12px] leading-relaxed text-ink-muted">
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 h-4 w-4 shrink-0 text-amber-300"
            />
            <span>
              ApplyPilot never invents experience, skills, metrics or
              certifications. Sensitive and legal answers stay manual, and
              nothing is entered into an employer site without your approval.
            </span>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
