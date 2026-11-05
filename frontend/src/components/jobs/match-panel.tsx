"use client";

import { BarChart3, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useJobMatches, useMatchJob } from "@/features/jobs/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import type {
  MatchConfidence,
  MatchScoreBreakdown,
  ResumeMatch,
} from "@/types/api";

const DIMENSIONS: Array<{ key: keyof MatchScoreBreakdown; label: string }> = [
  { key: "skills", label: "Skills" },
  { key: "experience", label: "Experience" },
  { key: "title", label: "Title relevance" },
  { key: "projects", label: "Projects" },
  { key: "education", label: "Education" },
];

const CONFIDENCE: Record<
  MatchConfidence,
  { label: string; variant: "success" | "warning" | "neutral" }
> = {
  high: { label: "High confidence", variant: "success" },
  medium: { label: "Partial job details", variant: "warning" },
  low: { label: "Limited job details", variant: "neutral" },
};

function scoreVariant(
  score: number,
): "success" | "warning" | "danger" | "brand" {
  if (score >= 75) return "success";
  if (score >= 50) return "warning";
  return "danger";
}

function ScoreRow({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12px] text-ink-muted">{label}</span>
        <span className="text-[12px] font-medium text-ink">
          {value === null ? "Not evaluated" : `${value}%`}
        </span>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]"
        role="presentation"
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-brand-indigo to-brand-blue"
          style={{ width: `${value ?? 0}%` }}
        />
      </div>
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
  if (skills.length === 0) {
    return null;
  }
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

function BulletList({
  title,
  items,
  tone = "default",
}: {
  title: string;
  items: string[];
  tone?: "default" | "muted";
}) {
  if (items.length === 0) {
    return null;
  }
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

function MatchDetail({ match }: { match: ResumeMatch }) {
  return (
    <div className="space-y-4 rounded-lg border border-white/[0.07] bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold text-ink">
            {match.resume_filename}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-subtle">
            Rank #{match.rank}
            {match.is_recommended ? " · Recommended resume" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={CONFIDENCE[match.confidence].variant}>
            {CONFIDENCE[match.confidence].label}
          </Badge>
          <Badge variant={scoreVariant(match.overall_score)}>
            {match.overall_score}% match
          </Badge>
        </div>
      </div>

      <p className="text-[12px] leading-relaxed text-ink-muted">
        {match.explanation}
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        {DIMENSIONS.map((dimension) => (
          <ScoreRow
            key={dimension.key}
            label={dimension.label}
            value={match.scores[dimension.key]}
          />
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <SkillGroup
          title="Matched required skills"
          skills={match.matched_required_skills}
          variant="brand"
        />
        <SkillGroup
          title="Missing required skills"
          skills={match.missing_required_skills}
          variant="danger"
        />
        <SkillGroup
          title="Matched preferred skills"
          skills={match.matched_preferred_skills}
          variant="neutral"
        />
        <SkillGroup
          title="Missing preferred skills"
          skills={match.missing_preferred_skills}
          variant="neutral"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <BulletList title="Matching strengths" items={match.strengths} />
        <BulletList
          title="Improvement areas"
          items={match.suggestions}
          tone="muted"
        />
      </div>
    </div>
  );
}

export function JobMatchPanel({ jobId }: { jobId: string }) {
  const matches = useJobMatches(jobId);
  const recompute = useMatchJob(jobId);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const rows = matches.data?.matches ?? [];
  const selected =
    rows.find((row) => row.resume_id === selectedId) ?? rows[0] ?? null;
  const requirements = matches.data?.job.requirements;

  return (
    <div className="space-y-4 rounded-xl border border-white/[0.07] bg-white/[0.015] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <BarChart3 aria-hidden="true" className="h-4 w-4 text-sky-300" />
          <p className="text-[13px] font-medium text-ink">Resume comparison</p>
          {rows.length > 0 ? (
            <Badge variant="neutral">
              {rows.length} resume{rows.length === 1 ? "" : "s"}
            </Badge>
          ) : null}
        </div>
        {rows.length > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => recompute.mutate({ force: true })}
            disabled={recompute.isPending}
          >
            <RefreshCw
              aria-hidden="true"
              className={cn(
                "h-3.5 w-3.5",
                recompute.isPending && "animate-spin",
              )}
            />
            Recompute
          </Button>
        ) : null}
      </div>

      {requirements && !requirements.has_description ? (
        <Alert variant="warning" title="Limited job description">
          This posting has no description in the alert email. The score only
          uses the job title and information that is actually available.
        </Alert>
      ) : requirements && requirements.required_skills.length === 0 ? (
        <Alert variant="info" title="No explicit requirements found">
          The posting does not state required skills, so scoring uses the
          technologies it mentions without treating them as mandatory.
        </Alert>
      ) : null}

      {recompute.error ? (
        <Alert variant="danger" title="Matching failed">
          {describeError(recompute.error)}
        </Alert>
      ) : null}

      {matches.isPending ? (
        <div className="space-y-3" aria-hidden="true">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : matches.isError ? (
        <>
          <Alert variant="danger" title="Could not load matches">
            {describeError(matches.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void matches.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Sparkles}
          title="Compare your resumes"
          description={
            matches.data?.message ??
            "Evaluate every uploaded resume against this job and see which one fits best."
          }
          action={
            <Button
              size="sm"
              onClick={() => recompute.mutate({})}
              disabled={recompute.isPending}
            >
              {recompute.isPending ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Matching…
                </>
              ) : (
                <>
                  <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                  Match all resumes
                </>
              )}
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            {rows.map((row) => (
              <button
                key={row.resume_id}
                type="button"
                onClick={() => setSelectedId(row.resume_id)}
                aria-pressed={selected?.resume_id === row.resume_id}
                className={cn(
                  "flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                  selected?.resume_id === row.resume_id
                    ? "border-brand-blue/40 bg-brand-blue/[0.08]"
                    : "border-white/10 bg-white/[0.02] hover:border-white/20",
                )}
              >
                <span className="min-w-0">
                  <span className="block truncate text-[12px] font-medium text-ink">
                    {row.resume_filename}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-ink-subtle">
                    #{row.rank}
                    {row.is_recommended ? " · Recommended" : ""}
                  </span>
                </span>
                <Badge variant={scoreVariant(row.overall_score)}>
                  {row.overall_score}%
                </Badge>
              </button>
            ))}
          </div>

          {selected ? <MatchDetail match={selected} /> : null}

          <p className="text-[11px] leading-relaxed text-ink-subtle">
            {matches.data?.disclaimer}
          </p>
        </>
      )}
    </div>
  );
}
