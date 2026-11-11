"use client";

import Link from "next/link";
import { FileText, Sparkles } from "lucide-react";
import { useState } from "react";

import { ConfidenceBadge } from "@/components/intelligence/confidence-badge";
import { formatScore, scoreVariant } from "@/components/intelligence/score-meter";
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
import { useSelectApplicationResume } from "@/features/applications/hooks";
import { useCompareResumes, useResumeRecommendation } from "@/features/jobs/hooks";
import { useResumes } from "@/features/resumes/hooks";
import { describeError } from "@/lib/api/errors";
import type { ApplicationPreview } from "@/types/api";

/** Mirrors the backend marker stored in `application.resume_reason`. */
const MANUAL_SELECTION_REASON =
  "Resume selected manually by you for this application.";

const inputClass =
  "w-full rounded-xl border border-white/10 bg-navy-950/60 px-3 py-2 text-[13px] text-ink placeholder:text-ink-subtle transition-colors focus:border-brand-blue/60 focus:outline-none focus:ring-2 focus:ring-brand-blue/25 disabled:cursor-not-allowed disabled:opacity-60";

/**
 * Resume selection for one application.
 *
 * The recommendation is advisory: selecting it is always an explicit user
 * action, a manual choice is never replaced automatically, and this panel never
 * approves or submits anything.
 */
export function ApplicationResumePanel({
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
  const recommendationQuery = useResumeRecommendation(preview.job_id);
  const compare = useCompareResumes(preview.job_id);
  const current = preview.resume;
  const [selected, setSelected] = useState(current.id ?? "");
  const items = resumes.data ?? [];
  const chosen = selected || current.id || "";

  const comparison = recommendationQuery.data;
  const recommendation = comparison?.recommendation ?? null;
  const currentEntry = comparison?.resumes.find(
    (entry) => entry.resume_id === current.id,
  );
  const isManual = current.reason === MANUAL_SELECTION_REASON;
  const isRecommended = Boolean(
    recommendation && current.id === recommendation.resume_id,
  );
  const canChange = editable && !selectResume.isPending;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Resume for this application</CardTitle>
          <CardDescription>
            Chosen by the matching engine unless you pick one yourself. Changing
            it never edits the original PDF.
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isManual ? <Badge variant="neutral">Selected by you</Badge> : null}
          {isRecommended ? <Badge variant="success">Recommended match</Badge> : null}
          {current.score !== null ? (
            <Badge variant={scoreVariant(current.score)}>
              {formatScore(current.score)}% match
            </Badge>
          ) : null}
        </div>
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

        {isManual ? (
          <Alert variant="info" title="Your selection is kept">
            Recommendations will not replace this resume. Choose another resume
            explicitly below if you want to change it.
          </Alert>
        ) : null}

        {currentEntry &&
        (currentEntry.processing_state === "analysis_required" ||
          currentEntry.processing_state === "partial") ? (
          <Alert variant="warning" title="Resume analysis is not current">
            {currentEntry.state_reason} Analyze your resumes in the{" "}
            <Link href="/resumes" className="text-sky-300 hover:underline">
              resume manager
            </Link>{" "}
            to make the comparison and recommendation more reliable.
          </Alert>
        ) : null}

        <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5 text-sky-300" />
            <p className="text-[12px] font-medium text-ink">Recommendation</p>
            {comparison ? (
              <Badge variant="neutral">
                {comparison.compared_resumes} of {comparison.total_resumes}{" "}
                compared
              </Badge>
            ) : null}
          </div>

          {recommendationQuery.isPending ? (
            <div className="mt-2 space-y-2" aria-hidden="true">
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-3 w-full" />
            </div>
          ) : recommendationQuery.isError ? (
            <div className="mt-2">
              <Alert variant="warning" title="Recommendation unavailable">
                {describeError(recommendationQuery.error)}
              </Alert>
              <Button
                variant="ghost"
                size="sm"
                className="mt-2"
                onClick={() => void recommendationQuery.refetch()}
              >
                Retry
              </Button>
            </div>
          ) : !comparison || comparison.status === "not_computed" ? (
            <div className="mt-2 space-y-2">
              <p className="text-[12px] leading-relaxed text-ink-muted">
                {comparison?.message ??
                  "No comparison has been stored for this job yet. Compare your resumes to see the recommended one."}
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => compare.mutate({ force: false })}
                disabled={compare.isPending}
              >
                {compare.isPending ? (
                  <>
                    <Spinner className="h-3.5 w-3.5" />
                    Comparing…
                  </>
                ) : (
                  "Compare resumes"
                )}
              </Button>
            </div>
          ) : recommendation ? (
            <div className="mt-2 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <p className="min-w-0 truncate text-[13px] font-medium text-ink">
                  {recommendation.resume_title}
                </p>
                <Badge variant={scoreVariant(recommendation.overall_score)}>
                  {formatScore(recommendation.overall_score)}% compatibility
                </Badge>
                <ConfidenceBadge confidence={recommendation.confidence} />
              </div>

              {recommendation.close_call || comparison.close_score ? (
                <p className="text-[11px] leading-relaxed text-amber-200">
                  {recommendation.close_call_explanation ??
                    "Another resume is close, so this is a preference rather than a clear winner."}
                </p>
              ) : null}

              {recommendation.reasons.length > 0 ? (
                <ul className="space-y-1">
                  {recommendation.reasons.slice(0, 3).map((reason) => (
                    <li
                      key={reason}
                      className="flex gap-2 text-[12px] leading-relaxed text-ink-muted"
                    >
                      <span aria-hidden="true" className="text-ink-subtle">
                        •
                      </span>
                      {reason}
                    </li>
                  ))}
                </ul>
              ) : null}

              {recommendation.important_gaps.length > 0 ? (
                <p className="text-[11px] leading-relaxed text-ink-subtle">
                  Gaps: {recommendation.important_gaps.slice(0, 3).join("; ")}
                </p>
              ) : null}

              {isRecommended ? (
                <p className="text-[12px] text-emerald-300">
                  This is the recommended resume for the job.
                </p>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!canChange}
                    onClick={() => selectResume.mutate(recommendation.resume_id)}
                  >
                    {selectResume.isPending ? (
                      <>
                        <Spinner className="h-3.5 w-3.5" />
                        Selecting…
                      </>
                    ) : (
                      "Use recommended resume"
                    )}
                  </Button>
                  <span className="text-[11px] text-ink-subtle">
                    {editable
                      ? "Only your click changes the selection."
                      : "The resume cannot be changed in this state."}
                  </span>
                </div>
              )}

              <p className="text-[11px] leading-relaxed text-ink-subtle">
                {comparison.disclaimer}
              </p>
            </div>
          ) : (
            <div className="mt-2 space-y-2">
              <p className="text-[12px] leading-relaxed text-ink-muted">
                {comparison.message ??
                  "No resume has enough usable evidence for a recommendation. Review your resumes before continuing."}
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => compare.mutate({ force: false })}
                disabled={compare.isPending}
              >
                Compare again
              </Button>
            </div>
          )}

          {compare.error ? (
            <div className="mt-2">
              <Alert variant="danger" title="Could not compare resumes">
                {describeError(compare.error)}
              </Alert>
            </div>
          ) : null}
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
