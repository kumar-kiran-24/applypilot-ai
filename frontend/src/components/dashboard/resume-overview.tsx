"use client";

import Link from "next/link";
import { FileText, RefreshCw, Sparkles } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { buttonClasses } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useMatchingSummary } from "@/features/jobs/hooks";
import { describeError } from "@/lib/api/errors";

export function ResumeOverview() {
  const summary = useMatchingSummary();
  const data = summary.data;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Resume overview</CardTitle>
          <CardDescription>Documents ready for matching</CardDescription>
        </div>
        {data ? (
          <Badge variant="neutral">
            {data.resumes_total} {data.resumes_total === 1 ? "resume" : "resumes"}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {summary.isPending ? (
          <div className="space-y-3" aria-hidden="true">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/2" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : summary.isError ? (
          <>
            <Alert variant="danger">{describeError(summary.error)}</Alert>
            <button
              type="button"
              onClick={() => void summary.refetch()}
              className={buttonClasses({ variant: "secondary", size: "sm" })}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Try again
            </button>
          </>
        ) : !data || data.resumes_total === 0 ? (
          <EmptyState
            icon={FileText}
            title="No resumes on file"
            description="Upload a PDF resume so ApplyPilot can compare it against the jobs it has discovered."
            action={
              <Link href="/resumes" className={buttonClasses({ size: "sm" })}>
                Upload a resume
              </Link>
            }
          />
        ) : (
          <>
            <div className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
              <div className="flex items-center gap-2 text-[12px] text-ink-muted">
                <Sparkles aria-hidden="true" className="h-3.5 w-3.5 text-sky-300" />
                {data.jobs_matched} job{data.jobs_matched === 1 ? "" : "s"} matched
              </div>
              <span className="text-[12px] font-medium text-ink">
                {data.average_best_score === null
                  ? "No scores yet"
                  : `${data.average_best_score}% avg best`}
              </span>
            </div>

            <ul className="space-y-2">
              {data.resume_stats.slice(0, 3).map((stat) => (
                <li
                  key={stat.resume_id}
                  className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="min-w-0 truncate text-[12px] font-medium text-ink">
                      {stat.resume_filename}
                    </p>
                    {stat.average_score !== null ? (
                      <span className="shrink-0 text-[11px] text-ink-subtle">
                        {stat.average_score}% avg
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 text-[11px] text-ink-subtle">
                    {stat.recommended_jobs > 0
                      ? `Recommended for ${stat.recommended_jobs} job${stat.recommended_jobs === 1 ? "" : "s"}`
                      : "No recommendations yet"}
                    {stat.is_default ? " · Default" : ""}
                  </p>
                </li>
              ))}
            </ul>

            <Link
              href="/resumes"
              className="text-[12px] font-medium text-sky-300 transition-colors hover:text-sky-200"
            >
              Manage resumes
            </Link>
          </>
        )}
      </CardContent>
    </Card>
  );
}
