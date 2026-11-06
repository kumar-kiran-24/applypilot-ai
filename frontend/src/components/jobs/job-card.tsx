"use client";

import {
  ArrowUpRight,
  Building2,
  CalendarDays,
  ChevronDown,
  FilePlus2,
  MapPin,
  Sparkles,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { JobMatchPanel } from "@/components/jobs/match-panel";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { buttonClasses } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useAnalyzeApplication } from "@/features/applications/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDate } from "@/lib/utils/format";
import { safeExternalUrl } from "@/lib/utils/url";
import type { Job } from "@/types/api";

function scoreVariant(
  score: number,
): "success" | "warning" | "danger" | "brand" {
  if (score >= 75) return "success";
  if (score >= 50) return "warning";
  return "danger";
}

export function JobCard({ job }: { job: Job }) {
  const url = safeExternalUrl(job.job_url);
  const router = useRouter();
  const analyze = useAnalyzeApplication();
  const [open, setOpen] = useState(false);
  const required = job.requirements.required_skills;
  const best = job.best_match;

  const prepare = () => {
    analyze.mutate(
      { job_id: job.id },
      {
        onSuccess: (preview) => {
          router.push(`/applications/${preview.application_id}`);
        },
      },
    );
  };

  return (
    <article className="group relative rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 transition-colors hover:border-white/[0.14] hover:bg-white/[0.035]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold leading-snug text-ink">
            {url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-sky-300 focus-visible:underline"
              >
                {job.job_title}
              </a>
            ) : (
              job.job_title
            )}
          </h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-muted">
            <span className="inline-flex items-center gap-1.5">
              <Building2
                aria-hidden="true"
                className="h-3.5 w-3.5 text-ink-subtle"
              />
              {job.company ?? "Company not listed"}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <MapPin
                aria-hidden="true"
                className="h-3.5 w-3.5 text-ink-subtle"
              />
              {job.location ?? "Location not listed"}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <CalendarDays
                aria-hidden="true"
                className="h-3.5 w-3.5 text-ink-subtle"
              />
              {formatDate(job.received_at)}
            </span>
          </div>
        </div>

        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Open ${job.job_title} on Glassdoor`}
            className="shrink-0 rounded-lg border border-white/10 bg-white/[0.03] p-1.5 text-ink-muted transition-colors hover:border-white/20 hover:text-ink"
          >
            <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
          </a>
        ) : null}
      </div>

      {best ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2">
          <Badge variant={scoreVariant(best.overall_score)}>
            {best.overall_score}% match
          </Badge>
          <span className="min-w-0 truncate text-[12px] text-ink-muted">
            Recommended: {best.resume_filename ?? "resume"}
          </span>
          {job.matches_count > 1 ? (
            <span className="text-[11px] text-ink-subtle">
              · {job.matches_count} resumes compared
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge variant="neutral" className="max-w-full truncate">
          {job.email_subject}
        </Badge>
        {required.length > 0 ? (
          <Badge variant="brand" className="max-w-full truncate">
            Requires {required.slice(0, 3).join(", ")}
            {required.length > 3 ? ` +${required.length - 3}` : ""}
          </Badge>
        ) : null}
        {job.requirements.has_description ? null : (
          <Badge variant="warning">No description</Badge>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className={buttonClasses({
            variant: "secondary",
            size: "sm",
            className: "text-[12px]",
          })}
        >
          <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
          {job.matches_count > 0 ? "Compare resumes" : "Match resumes"}
          <ChevronDown
            aria-hidden="true"
            className={cn(
              "h-3.5 w-3.5 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>
        <button
          type="button"
          onClick={prepare}
          disabled={analyze.isPending}
          className={buttonClasses({
            variant: "primary",
            size: "sm",
            className: "text-[12px]",
          })}
        >
          {analyze.isPending ? (
            <>
              <Spinner className="h-3.5 w-3.5" />
              Preparing…
            </>
          ) : (
            <>
              <FilePlus2 aria-hidden="true" className="h-3.5 w-3.5" />
              Prepare application
            </>
          )}
        </button>
      </div>

      {analyze.error ? (
        <div className="mt-3">
          <Alert variant="danger" title="Could not prepare this application">
            {describeError(analyze.error)}
          </Alert>
        </div>
      ) : null}

      {open ? (
        <div className="mt-3">
          <JobMatchPanel jobId={job.id} />
        </div>
      ) : null}
    </article>
  );
}
