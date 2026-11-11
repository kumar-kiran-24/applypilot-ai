"use client";

import Link from "next/link";
import { BarChart3, Layers, RefreshCw, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import { ConfidenceBadge } from "@/components/intelligence/confidence-badge";
import { EvidenceList } from "@/components/intelligence/evidence-list";
import {
  ScoreMeter,
  formatScore,
  scoreVariant,
} from "@/components/intelligence/score-meter";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useCompareResumes, useResumeRecommendation } from "@/features/jobs/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import type {
  ResumeComparisonEntry,
  ResumeComparisonResponse,
  ResumeRecommendation,
} from "@/types/api";

const PROCESSING_STATE: Record<
  string,
  { label: string; variant: "success" | "warning" | "neutral" | "danger" }
> = {
  ready: { label: "Analysis ready", variant: "success" },
  partial: { label: "Partial inputs", variant: "warning" },
  analysis_required: { label: "Analysis required", variant: "warning" },
  extraction_failed: { label: "Extraction failed", variant: "danger" },
  invalid: { label: "Unusable resume", variant: "danger" },
  inaccessible: { label: "Not comparable", variant: "neutral" },
};

function stateBadge(state: string) {
  return PROCESSING_STATE[state] ?? { label: state, variant: "neutral" as const };
}

function BulletList({
  title,
  items,
  tone = "default",
}: {
  title: string;
  items: string[];
  tone?: "default" | "muted";
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        {title}
      </p>
      <ul className="mt-1.5 space-y-1">
        {items.map((item) => (
          <li
            key={item}
            className={cn(
              "flex gap-2 text-[12px] leading-relaxed",
              tone === "muted" ? "text-ink-subtle" : "text-ink-muted",
            )}
          >
            <span aria-hidden="true" className="text-ink-subtle">
              •
            </span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SkillGroup({
  title,
  skills,
  variant,
}: {
  title: string;
  skills: string[];
  variant: "brand" | "danger" | "neutral";
}) {
  if (skills.length === 0) return null;
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        {title}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {skills.map((skill) => (
          <Badge key={skill} variant={variant}>
            {skill}
          </Badge>
        ))}
      </div>
    </div>
  );
}

/**
 * The recommended resume, its evidence and alternatives. Every value is taken
 * from the backend; this component never recomputes or re-ranks scores.
 */
export function RecommendationSummary({
  recommendation,
  closeScore,
  disclaimer,
  action,
}: {
  recommendation: ResumeRecommendation;
  closeScore: boolean;
  disclaimer?: string;
  action?: React.ReactNode;
}) {
  const closeCall = recommendation.close_call || closeScore;

  return (
    <div className="space-y-3 rounded-xl border border-brand-blue/25 bg-brand-blue/[0.06] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[11px] uppercase tracking-[0.08em] text-sky-300">
            Recommended resume
          </p>
          <p className="mt-0.5 truncate text-[14px] font-semibold text-ink">
            {recommendation.resume_title}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={scoreVariant(recommendation.overall_score)}>
            {formatScore(recommendation.overall_score)}% compatibility
          </Badge>
          <ConfidenceBadge confidence={recommendation.confidence} />
        </div>
      </div>

      {closeCall ? (
        <Alert variant="warning" title="Scores are close">
          {recommendation.close_call_explanation ??
            `Another resume is within ${formatScore(
              recommendation.close_call_threshold,
            )} points, so this is a preference rather than a clear winner. Compare the differences before choosing.`}
        </Alert>
      ) : null}

      <BulletList title="Why this resume" items={recommendation.reasons} />
      <BulletList
        title="Important gaps"
        items={recommendation.important_gaps}
        tone="muted"
      />

      {recommendation.alternatives.length > 0 ? (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
            Alternatives
          </p>
          <ul className="mt-1.5 space-y-2">
            {recommendation.alternatives.map((alternative) => (
              <li
                key={alternative.resume_id}
                className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-[12px] font-medium text-ink">
                    {alternative.resume_title}
                  </p>
                  <p className="text-[11px] text-ink-subtle">
                    {formatScore(alternative.overall_score)}% ·{" "}
                    {formatScore(alternative.score_difference)} points lower
                  </p>
                </div>
                {alternative.distinguishing_factors.length > 0 ? (
                  <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                    Differs on {alternative.distinguishing_factors.join(", ")}.
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {recommendation.supporting_evidence.length > 0 ? (
        <EvidenceList
          evidence={recommendation.supporting_evidence.slice(0, 4)}
          title="Evidence behind the recommendation"
        />
      ) : null}

      {action ? <div>{action}</div> : null}

      <p className="text-[11px] leading-relaxed text-ink-subtle">
        {disclaimer ??
          "Compatibility scores describe overlap with this posting. They are not hiring probabilities."}
      </p>
    </div>
  );
}

function EntryDetail({ entry }: { entry: ResumeComparisonEntry }) {
  const badge = stateBadge(entry.processing_state);
  const strengths = entry.evidence
    .filter((item) => item.category !== "education")
    .map((item) => item.description)
    .slice(0, 4);

  return (
    <div className="space-y-4 rounded-lg border border-white/[0.07] bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold text-ink">
            {entry.resume_title}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-subtle">
            {entry.rank !== null ? `Rank #${entry.rank}` : "Not ranked"}
            {entry.is_recommended ? " · Recommended" : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={badge.variant}>{badge.label}</Badge>
          {entry.evidence_confidence ? (
            <ConfidenceBadge confidence={entry.evidence_confidence} />
          ) : null}
          {entry.overall_score !== null ? (
            <Badge variant={scoreVariant(entry.overall_score)}>
              {formatScore(entry.overall_score)}% match
            </Badge>
          ) : null}
        </div>
      </div>

      <p className="text-[12px] leading-relaxed text-ink-muted">
        {entry.state_reason}
      </p>

      {entry.match_processing_state && entry.match_processing_state !== "ready" ? (
        <Badge variant="neutral">
          Match inputs: {entry.match_processing_state.replace(/_/g, " ")}
        </Badge>
      ) : null}

      {entry.comparable ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <ScoreMeter
            label={
              entry.required_skills_matched !== null &&
              entry.required_skills_total !== null
                ? `Required-skill coverage (${entry.required_skills_matched}/${entry.required_skills_total})`
                : "Required-skill coverage"
            }
            value={entry.required_skill_coverage}
          />
          <ScoreMeter label="Skills" value={entry.scores.skills} />
          <ScoreMeter label="Experience" value={entry.scores.experience} />
          <ScoreMeter label="Projects" value={entry.scores.projects} />
          <ScoreMeter label="Education" value={entry.scores.education} />
          <ScoreMeter label="Title relevance" value={entry.scores.title} />
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <SkillGroup
          title="Matched required skills"
          skills={entry.matched_required_skills}
          variant="brand"
        />
        <SkillGroup
          title="Missing required skills"
          skills={entry.missing_required_skills}
          variant="danger"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <BulletList title="Strengths from evidence" items={strengths} />
        <BulletList title="Gaps" items={entry.gaps} tone="muted" />
      </div>

      <EvidenceList evidence={entry.evidence} title="Evidence" />

      {entry.explanation ? (
        <p className="text-[11px] leading-relaxed text-ink-subtle">
          {entry.explanation}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Full comparison view. It reads the stored recommendation and only recomputes
 * when the user asks, so opening a job never triggers an expensive pass.
 */
export function ResumeComparisonPanel({ jobId }: { jobId: string }) {
  const comparisonQuery = useResumeRecommendation(jobId);
  const compare = useCompareResumes(jobId);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const data: ResumeComparisonResponse | undefined = comparisonQuery.data;
  const entries = useMemo(() => {
    const rows = data?.resumes ?? [];
    return [...rows].sort((left, right) => {
      if (left.rank === null && right.rank === null) return 0;
      if (left.rank === null) return 1;
      if (right.rank === null) return -1;
      return left.rank - right.rank;
    });
  }, [data]);

  const selected =
    entries.find((entry) => entry.resume_id === selectedId) ??
    entries.find((entry) => entry.is_recommended) ??
    entries[0] ??
    null;

  const compareButton = (label: string, force: boolean) => (
    <Button
      variant={force ? "ghost" : "primary"}
      size="sm"
      onClick={() => compare.mutate({ force })}
      disabled={compare.isPending}
    >
      {compare.isPending ? (
        <Spinner className="h-3.5 w-3.5" />
      ) : force ? (
        <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
      ) : (
        <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
      )}
      {label}
    </Button>
  );

  return (
    <section className="space-y-4 rounded-xl border border-white/[0.07] bg-white/[0.015] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <BarChart3 aria-hidden="true" className="h-4 w-4 text-sky-300" />
          <p className="text-[13px] font-medium text-ink">Resume comparison</p>
          {data && data.total_resumes > 0 ? (
            <Badge variant="neutral">
              {data.compared_resumes} of {data.total_resumes} compared
            </Badge>
          ) : null}
        </div>
        {data && data.status !== "not_computed" ? (
          compareButton("Recompute", true)
        ) : null}
      </div>

      {compare.error ? (
        <Alert variant="danger" title="Comparison failed">
          {describeError(compare.error)}
        </Alert>
      ) : null}

      {comparisonQuery.isPending ? (
        <div className="space-y-3" aria-hidden="true">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : comparisonQuery.isError ? (
        <>
          <Alert variant="danger" title="Could not load the comparison">
            {describeError(comparisonQuery.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void comparisonQuery.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </>
      ) : !data ? (
        <EmptyState
          icon={Sparkles}
          title="Compare your resumes"
          description="Evaluate every uploaded resume against this job and see which one fits best."
          action={compareButton("Compare resumes", false)}
        />
      ) : data.status === "not_computed" ? (
        <EmptyState
          icon={Sparkles}
          title="No comparison yet"
          description={
            data.message ??
            "Compare your resumes to see compatibility scores and a recommendation."
          }
          action={compareButton("Compare resumes", false)}
        />
      ) : data.status === "no_resumes" ? (
        <EmptyState
          icon={Layers}
          title="No resumes to compare"
          description={
            data.message ??
            "Upload at least one PDF resume before comparing it with this job."
          }
          action={
            <Link
              href="/resumes"
              className="text-[13px] text-sky-300 hover:underline"
            >
              Open the resume manager
            </Link>
          }
        />
      ) : (
        <>
          {data.message ? (
            <p className="text-[11px] leading-relaxed text-ink-subtle">
              {data.message}
            </p>
          ) : null}

          {data.status === "no_suitable_resume" ? (
            <Alert variant="warning" title="No suitable resume">
              {data.message ??
                "None of your resumes had enough usable evidence for a confident recommendation. Review or analyze your resumes, then compare again."}
            </Alert>
          ) : null}

          {data.recommendation ? (
            <RecommendationSummary
              recommendation={data.recommendation}
              closeScore={data.close_score}
              disclaimer={data.disclaimer}
            />
          ) : null}

          {entries.length > 0 ? (
            <div className="space-y-3">
              <div className="grid gap-2 sm:grid-cols-2">
                {entries.map((entry) => {
                  const badge = stateBadge(entry.processing_state);
                  const active = selected?.resume_id === entry.resume_id;
                  return (
                    <button
                      key={entry.resume_id}
                      type="button"
                      onClick={() => setSelectedId(entry.resume_id)}
                      aria-pressed={active}
                      className={cn(
                        "flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                        active
                          ? "border-brand-blue/40 bg-brand-blue/[0.08]"
                          : "border-white/10 bg-white/[0.02] hover:border-white/20",
                      )}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[12px] font-medium text-ink">
                          {entry.resume_title}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-ink-subtle">
                          {entry.rank !== null ? `#${entry.rank}` : "Not ranked"}
                          {entry.is_recommended ? " · Recommended" : ""} ·{" "}
                          {badge.label}
                        </span>
                      </span>
                      {entry.overall_score !== null ? (
                        <Badge variant={scoreVariant(entry.overall_score)}>
                          {formatScore(entry.overall_score)}%
                        </Badge>
                      ) : (
                        <Badge variant="neutral">—</Badge>
                      )}
                    </button>
                  );
                })}
              </div>

              {selected ? <EntryDetail entry={selected} /> : null}
            </div>
          ) : null}

          {data.processing_failures.length > 0 ? (
            <div>
              <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                Resumes that could not be compared
              </p>
              <ul className="mt-1.5 space-y-1.5">
                {data.processing_failures.map((failure) => (
                  <li
                    key={failure.resume_id}
                    className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2"
                  >
                    <p className="text-[12px] font-medium text-ink">
                      {failure.resume_title}
                    </p>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-ink-muted">
                      {failure.reason}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <p className="text-[11px] leading-relaxed text-ink-subtle">
            {data.disclaimer}
          </p>
        </>
      )}
    </section>
  );
}
