"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  Clipboard,
  Copy,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  X,
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
import {
  useAnswerCategories,
  useAnswerDetail,
  useAnswerKnowledge,
  useResolveSavedAnswer,
  useSavedAnswers,
} from "@/features/answers/hooks";
import {
  useApplication,
  useApplications,
  useUpdateApplicationQuestion,
} from "@/features/applications/hooks";
import {
  useDeleteReusableAnswer,
  useUpsertReusableAnswer,
} from "@/features/profile/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDate } from "@/lib/utils/format";
import type {
  AnswerCategory,
  AnswerTrustLevel,
  CandidateKnowledge,
  SavedAnswer,
} from "@/types/api";

type BadgeVariant = "neutral" | "success" | "warning" | "danger" | "brand";

const TRUST_VARIANT: Record<AnswerTrustLevel, BadgeVariant> = {
  verified_fact: "success",
  user_reviewed: "success",
  resume_suggestion: "warning",
  ai_draft: "warning",
  user_written: "neutral",
  needs_information: "danger",
};

const TRUST_FILTERS: ReadonlyArray<{ value: AnswerTrustLevel; label: string }> = [
  { value: "verified_fact", label: "Verified candidate fact" },
  { value: "user_reviewed", label: "User-reviewed answer" },
  { value: "resume_suggestion", label: "Resume-derived suggestion" },
  { value: "ai_draft", label: "AI-generated draft" },
  { value: "user_written", label: "User-written, not yet reviewed" },
  { value: "needs_information", label: "Requires additional information" },
];

function Count({ value }: { value: number }) {
  return <Badge variant="neutral">{value}</Badge>;
}

function KnowledgeList({
  title,
  description,
  count,
  children,
}: {
  title: string;
  description: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[13px] font-medium text-ink">{title}</p>
          <p className="mt-0.5 text-[11px] text-ink-subtle">{description}</p>
        </div>
        <Count value={count} />
      </div>
      <div className="mt-3 space-y-2">{children}</div>
    </div>
  );
}

function KnowledgePanel({ report }: { report: CandidateKnowledge }) {
  const resolve = useResolveSavedAnswer();
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Candidate knowledge center</CardTitle>
          <CardDescription>
            Missing, conflicting, outdated and unverified information. Nothing is
            changed automatically.
          </CardDescription>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="success">{report.verified_answers} confirmed</Badge>
          <Badge variant="warning">{report.needs_review} to review</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-[12px] leading-relaxed text-ink-muted">
          {report.summary}
        </p>

        {notice ? (
          <Alert variant="success" title="Saved">
            {notice}
          </Alert>
        ) : null}
        {resolve.isError ? (
          <Alert variant="danger" title="Could not resolve this conflict">
            {describeError(resolve.error)}
          </Alert>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2">
          <KnowledgeList
            title="Missing information"
            description="Profile details and answers that still need your input."
            count={report.missing_information.length}
          >
            {report.missing_information.length === 0 ? (
              <p className="text-[12px] text-ink-subtle">Nothing flagged.</p>
            ) : (
              report.missing_information.slice(0, 6).map((item) => (
                <div key={`${item.kind}-${item.field}`}>
                  <p className="text-[12px] text-ink">
                    {item.label}
                    {item.required ? (
                      <span className="ml-1 text-[10px] uppercase tracking-[0.08em] text-amber-300">
                        required
                      </span>
                    ) : null}
                  </p>
                  <p className="text-[11px] leading-relaxed text-ink-subtle">
                    {item.reason}
                  </p>
                  {item.kind === "profile_field" ? (
                    <Link
                      href="/profile"
                      className="text-[11px] text-sky-300 hover:text-sky-200"
                    >
                      Add on profile
                    </Link>
                  ) : null}
                </div>
              ))
            )}
            {report.missing_information.length > 6 ? (
              <p className="text-[11px] text-ink-subtle">
                {report.missing_information.length - 6} more not shown.
              </p>
            ) : null}
          </KnowledgeList>

          <KnowledgeList
            title="Conflicts with verified data"
            description="A stored answer disagrees with your profile or another answer."
            count={report.conflicts.length}
          >
            {report.conflicts.length === 0 ? (
              <p className="text-[12px] text-ink-subtle">Nothing flagged.</p>
            ) : (
              report.conflicts.slice(0, 4).map((item) => (
                <div key={`${item.question_key}-${item.conflicting_source}`}>
                  <p className="text-[12px] text-ink">{item.question_text}</p>
                  <p className="text-[11px] leading-relaxed text-ink-subtle">
                    Stored: {item.stored_answer} · {item.conflicting_source}:{" "}
                    {item.conflicting_answer}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {item.conflicting_source === "profile" ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={resolve.isPending}
                        onClick={() => {
                          setNotice(null);
                          resolve.mutate(
                            {
                              key: item.question_key,
                              input: { resolution: "use_profile" },
                            },
                            {
                              onSuccess: () =>
                                setNotice(
                                  `Adopted your verified profile value for ${item.question_text}.`,
                                ),
                            },
                          );
                        }}
                      >
                        Use profile value
                      </Button>
                    ) : null}
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={resolve.isPending}
                      onClick={() => {
                        setNotice(null);
                        resolve.mutate(
                          {
                            key: item.question_key,
                            input: { resolution: "keep_stored" },
                          },
                          {
                            onSuccess: () =>
                              setNotice(
                                `Re-confirmed ${item.question_text} as it is stored.`,
                              ),
                          },
                        );
                      }}
                    >
                      Keep stored
                    </Button>
                  </div>
                </div>
              ))
            )}
          </KnowledgeList>

          <KnowledgeList
            title="Outdated confirmations"
            description="Confirmed answers that should be checked again."
            count={report.outdated.length}
          >
            {report.outdated.length === 0 ? (
              <p className="text-[12px] text-ink-subtle">Nothing flagged.</p>
            ) : (
              report.outdated.slice(0, 4).map((item) => (
                <div key={`outdated-${item.question_key}`}>
                  <p className="text-[12px] text-ink">{item.question_text}</p>
                  <p className="text-[11px] leading-relaxed text-ink-subtle">
                    {item.reason}
                  </p>
                </div>
              ))
            )}
          </KnowledgeList>

          <KnowledgeList
            title="Not verified"
            description="Drafts and suggestions that are never reused as facts."
            count={report.unverified.length}
          >
            {report.unverified.length === 0 ? (
              <p className="text-[12px] text-ink-subtle">Nothing flagged.</p>
            ) : (
              report.unverified.slice(0, 4).map((item) => (
                <div key={`unverified-${item.question_key}`}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <p className="text-[12px] text-ink">{item.question_text}</p>
                    <Badge variant="warning">{item.trust_label}</Badge>
                  </div>
                  <p className="text-[11px] leading-relaxed text-ink-subtle">
                    {item.reason}
                  </p>
                </div>
              ))
            )}
          </KnowledgeList>
        </div>

        <p className="text-[11px] leading-relaxed text-ink-subtle">
          {report.disclaimer}
        </p>
      </CardContent>
    </Card>
  );
}

function ReusePanel({ answer }: { answer: SavedAnswer }) {
  const applications = useApplications();
  const items = useMemo(
    () => applications.data?.applications ?? [],
    [applications.data],
  );
  const [applicationId, setApplicationId] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const application = useApplication(applicationId, Boolean(applicationId));
  const preview = application.data;
  const update = useUpdateApplicationQuestion(applicationId);
  const detail = useAnswerDetail(answer.question_key);
  const conflicts = detail.data?.conflicts ?? [];

  useEffect(() => {
    if (!applicationId && items[0]) {
      setApplicationId(items[0].id);
    }
  }, [applicationId, items]);

  useEffect(() => {
    setQuestionId("");
    setNotice(null);
  }, [applicationId]);

  const questions = useMemo(
    () =>
      (preview?.sections ?? [])
        .flatMap((section) => section.questions)
        .filter(
          (question) => !question.is_legal && question.field_type !== "file",
        ),
    [preview],
  );

  const insert = () => {
    const question = questions.find((item) => item.id === questionId);
    if (!question || !answer.answer) return;
    setNotice(null);
    update.mutate(
      {
        questionId: question.id,
        answer: answer.answer,
        confirmed: false,
        reusedAnswerKey: answer.question_key,
      },
      {
        onSuccess: () =>
          setNotice(
            `Inserted as an unconfirmed draft in ${preview?.job_title ?? "the application"}. Review and confirm it in the application workspace.`,
          ),
      },
    );
  };

  if (applications.isPending) {
    return <Skeleton className="h-24 w-full" />;
  }
  if (applications.isError) {
    return (
      <Alert variant="danger" title="Could not load applications">
        {describeError(applications.error)}
      </Alert>
    );
  }
  if (items.length === 0) {
    return (
      <p className="text-[12px] text-ink-subtle">
        Start an application first, then you can reuse this answer there.
      </p>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border border-sky-500/20 bg-sky-500/[0.04] p-3">
      <p className="text-[12px] leading-relaxed text-ink-muted">
        Choose where to reuse this answer. It is inserted into ApplyPilot as an
        unconfirmed draft only; nothing is entered on an employer&apos;s site.
      </p>

      {conflicts.length > 0 ? (
        <Alert variant="danger" title="Conflicts with your current information">
          <ul className="space-y-1">
            {conflicts.map((conflict) => (
              <li key={`${conflict.question_key}-${conflict.conflicting_source}`}>
                Stored: {conflict.stored_answer} · {conflict.conflicting_source}:{" "}
                {conflict.conflicting_answer}
              </li>
            ))}
          </ul>
          <p className="mt-1">
            Resolve the conflict before reusing this answer.
          </p>
        </Alert>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label
            htmlFor={`reuse-application-${answer.question_key}`}
            className="mb-1.5 block text-[11px] text-ink-muted"
          >
            Application
          </label>
          <select
            id={`reuse-application-${answer.question_key}`}
            value={applicationId}
            onChange={(event) => setApplicationId(event.target.value)}
            className="field"
          >
            {items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.job_title}
                {item.company ? ` — ${item.company}` : ""}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor={`reuse-question-${answer.question_key}`}
            className="mb-1.5 block text-[11px] text-ink-muted"
          >
            Question
          </label>
          <select
            id={`reuse-question-${answer.question_key}`}
            value={questionId}
            onChange={(event) => setQuestionId(event.target.value)}
            className="field"
            disabled={!preview}
          >
            <option value="">Choose a question</option>
            {questions.map((question) => (
              <option key={question.id} value={question.id}>
                {question.question_text}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={
            !answer.answer ||
            !questionId ||
            update.isPending ||
            conflicts.length > 0
          }
          onClick={insert}
        >
          {update.isPending ? (
            <Spinner className="h-3.5 w-3.5" />
          ) : (
            <Clipboard aria-hidden="true" className="h-3.5 w-3.5" />
          )}
          Insert as draft
        </Button>
        {update.isError ? (
          <span className="text-[11px] text-red-300">
            {describeError(update.error)}
          </span>
        ) : null}
      </div>

      {notice ? (
        <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/[0.06] px-2.5 py-2 text-[11px] leading-relaxed text-emerald-200">
          {notice}
        </p>
      ) : null}
    </div>
  );
}

function SavedAnswerCard({ answer }: { answer: SavedAnswer }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [reuseOpen, setReuseOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [value, setValue] = useState(answer.answer ?? "");
  const [copied, setCopied] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const detail = useAnswerDetail(
    answer.question_key,
    detailsOpen || reuseOpen,
  );
  const update = useUpsertReusableAnswer();
  const remove = useDeleteReusableAnswer();

  useEffect(() => {
    setValue(answer.answer ?? "");
  }, [answer.answer]);

  const copy = async () => {
    if (!answer.answer) return;
    try {
      await navigator.clipboard.writeText(answer.answer);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  const saveEdit = () => {
    if (!value.trim()) return;
    setNotice(null);
    update.mutate(
      {
        key: answer.question_key,
        input: {
          question_text: answer.question_text,
          answer: value.trim(),
        },
      },
      {
        onSuccess: () => {
          setEditing(false);
          setNotice(
            "Saved. Changed wording is unverified until you confirm it again.",
          );
        },
      },
    );
  };

  const confirm = () => {
    if (!answer.answer) return;
    setNotice(null);
    update.mutate(
      {
        key: answer.question_key,
        input: {
          question_text: answer.question_text,
          answer: answer.answer,
          verification_status: "verified",
        },
      },
      {
        onSuccess: () =>
          setNotice("Confirmed as a reviewed answer. It may now be reused."),
      },
    );
  };

  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-ink">
            {answer.question_text}
          </p>
          <p className="mt-0.5 truncate font-mono text-[10px] text-ink-subtle">
            {answer.question_key}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="neutral">{answer.category_label}</Badge>
          <Badge variant={TRUST_VARIANT[answer.trust_level]}>
            {answer.trust_label}
          </Badge>
          {answer.is_sensitive ? (
            <Badge variant="warning">Sensitive</Badge>
          ) : null}
          {answer.is_stale ? <Badge variant="danger">Outdated</Badge> : null}
        </div>
      </div>

      {editing ? (
        <div className="mt-3 space-y-2">
          <textarea
            aria-label={`Edit answer: ${answer.question_text}`}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            rows={5}
            className="field"
          />
          <p className="text-[11px] text-ink-subtle">
            Saving a change marks this answer unverified until you confirm it
            again.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={!value.trim() || update.isPending}
              onClick={saveEdit}
            >
              {update.isPending ? <Spinner className="h-3.5 w-3.5" /> : null}
              Save changes
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setValue(answer.answer ?? "");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <p className="mt-2 whitespace-pre-wrap text-[12px] leading-relaxed text-ink-muted">
          {answer.answer || "No value saved yet."}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={detailsOpen}
          onClick={() => setDetailsOpen((open) => !open)}
        >
          <ChevronDown
            aria-hidden="true"
            className={cn(
              "h-3.5 w-3.5 transition-transform",
              detailsOpen ? "rotate-180" : "",
            )}
          />
          Details
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={editing}
          onClick={() => {
            setNotice(null);
            setEditing(true);
          }}
        >
          <Pencil aria-hidden="true" className="h-3.5 w-3.5" />
          Edit
        </Button>
        {answer.needs_review && answer.answer ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={update.isPending}
            onClick={confirm}
          >
            <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
            Confirm answer
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          disabled={!answer.answer}
          onClick={() => void copy()}
        >
          {copied ? (
            <Check aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <Copy aria-hidden="true" className="h-3.5 w-3.5" />
          )}
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!answer.answer || Boolean(answer.is_legal)}
          aria-expanded={reuseOpen}
          onClick={() => setReuseOpen((open) => !open)}
        >
          <Clipboard aria-hidden="true" className="h-3.5 w-3.5" />
          Reuse
        </Button>
        {deleting ? (
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-red-300">Delete this answer?</span>
            <Button
              size="sm"
              variant="danger"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(answer.question_key, {
                  onSuccess: () => setDeleting(false),
                })
              }
            >
              {remove.isPending ? <Spinner className="h-3.5 w-3.5" /> : null}
              Delete answer
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDeleting(false)}>
              <X aria-hidden="true" className="h-3.5 w-3.5" />
              Cancel
            </Button>
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setNotice(null);
              setDeleting(true);
            }}
          >
            <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
            Delete
          </Button>
        )}
        <span className="ml-auto text-[10px] text-ink-subtle">
          Updated {formatDate(answer.updated_at)}
        </span>
      </div>

      {notice ? (
        <p className="mt-3 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.06] px-2.5 py-2 text-[11px] leading-relaxed text-emerald-200">
          {notice}
        </p>
      ) : null}
      {update.isError || remove.isError ? (
        <Alert variant="danger" title="Could not save this answer" className="mt-3">
          {describeError(update.error ?? remove.error)}
        </Alert>
      ) : null}

      {detailsOpen ? (
        <div className="mt-3 space-y-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="flex flex-wrap gap-1.5">
            <Badge variant={answer.needs_review ? "warning" : "success"}>
              {answer.needs_review ? "Needs review" : "Reviewed"}
            </Badge>
            <Badge variant="neutral">
              Source: {(answer.answer_source ?? "user_input").replaceAll("_", " ")}
            </Badge>
            <Badge variant="neutral">
              Confidence {Math.round((answer.confidence ?? 0) * 100)}%
            </Badge>
            {answer.last_confirmed_at ? (
              <Badge variant="neutral">
                Confirmed {formatDate(answer.last_confirmed_at)}
              </Badge>
            ) : null}
          </div>
          <p className="text-[12px] leading-relaxed text-ink-muted">
            {answer.trust_reason}
          </p>
          {answer.is_stale && answer.freshness_reason ? (
            <Alert variant="warning" title="This answer needs re-confirmation">
              {answer.freshness_reason}
            </Alert>
          ) : null}

          {detail.isPending ? (
            <Skeleton className="h-14 w-full" />
          ) : detail.isError ? (
            <Alert variant="danger" title="Could not load evidence">
              {describeError(detail.error)}
            </Alert>
          ) : (
            <div className="space-y-2">
              <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                Evidence and source
              </p>
              {(detail.data?.evidence ?? []).map((item) => (
                <div
                  key={item.id}
                  className="rounded-lg border border-white/[0.06] px-2.5 py-2"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant={item.verified ? "success" : "warning"}>
                      {item.verified ? "Verified" : "Unverified"}
                    </Badge>
                    <span className="text-[11px] font-medium text-ink">
                      {item.label}
                    </span>
                  </div>
                  <p className="mt-1 break-words text-[11px] text-ink-muted">
                    {item.detail}
                  </p>
                </div>
              ))}
              {(detail.data?.conflicts ?? []).map((conflict) => (
                <Alert
                  key={`${conflict.question_key}-${conflict.conflicting_source}`}
                  variant="danger"
                  title="Conflicting information"
                >
                  Stored: {conflict.stored_answer} ·{" "}
                  {conflict.conflicting_source}: {conflict.conflicting_answer}
                </Alert>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {reuseOpen ? (
        <div className="mt-3">
          <ReusePanel answer={answer} />
        </div>
      ) : null}
    </div>
  );
}

export function AnswerLibrary() {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [category, setCategory] = useState<AnswerCategory | "">("");
  const [trust, setTrust] = useState<AnswerTrustLevel | "">("");

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const filters = useMemo(
    () => ({ q: debouncedSearch, category, trust }),
    [debouncedSearch, category, trust],
  );
  const answers = useSavedAnswers(filters);
  const categories = useAnswerCategories();
  const knowledge = useAnswerKnowledge();

  const hasFilters = Boolean(search.trim() || category || trust);

  return (
    <div className="space-y-4">
      {knowledge.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : knowledge.isError ? (
        <Alert variant="danger" title="Could not load the knowledge review">
          {describeError(knowledge.error)}
        </Alert>
      ) : knowledge.data ? (
        <KnowledgePanel report={knowledge.data} />
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Saved answers</CardTitle>
            <CardDescription>
              Reusable answers across every application. Trust levels come from
              how each answer was produced and confirmed.
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void answers.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_180px_200px]">
            <div className="relative">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-subtle"
              />
              <input
                type="search"
                aria-label="Search saved answers"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search questions, keys or answers"
                className="field pl-9"
              />
            </div>
            <div>
              <label
                htmlFor="answer-category-filter"
                className="sr-only"
              >
                Filter by category
              </label>
              <select
                id="answer-category-filter"
                aria-label="Filter by category"
                value={category}
                onChange={(event) =>
                  setCategory(event.target.value as AnswerCategory | "")
                }
                className="field"
              >
                <option value="">All categories</option>
                {(categories.data?.categories ?? []).map((item) => (
                  <option key={item.category} value={item.category}>
                    {item.label} ({item.count})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="answer-trust-filter" className="sr-only">
                Filter by trust level
              </label>
              <select
                id="answer-trust-filter"
                aria-label="Filter by trust level"
                value={trust}
                onChange={(event) =>
                  setTrust(event.target.value as AnswerTrustLevel | "")
                }
                className="field"
              >
                <option value="">All trust levels</option>
                {TRUST_FILTERS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {answers.isPending ? (
            <div className="space-y-2">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : answers.isError ? (
            <Alert variant="danger" title="Could not load saved answers">
              {describeError(answers.error)}
            </Alert>
          ) : (answers.data?.length ?? 0) === 0 ? (
            <EmptyState
              icon={BookOpen}
              title={hasFilters ? "No answers match these filters" : "No saved answers yet"}
              description={
                hasFilters
                  ? "Try a different search term, category or trust level."
                  : "Answer an application question or review an AI draft in the assistant, then save it for reuse."
              }
              action={
                hasFilters ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setSearch("");
                      setCategory("");
                      setTrust("");
                    }}
                  >
                    Clear filters
                  </Button>
                ) : (
                  <Link href="/assistant" className={buttonClasses({ size: "sm" })}>
                    Open the assistant
                  </Link>
                )
              }
            />
          ) : (
            <div className="space-y-3">
              {answers.data?.map((answer) => (
                <SavedAnswerCard key={answer.question_key} answer={answer} />
              ))}
            </div>
          )}

          <div className="flex items-start gap-2 rounded-xl border border-white/[0.06] px-3 py-2.5 text-[11px] leading-relaxed text-ink-subtle">
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300"
            />
            <span>
              AI drafts and resume suggestions are never treated as facts. Reuse
              only inserts text into your own application for review; ApplyPilot
              never fills or submits an employer form on its own.
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
