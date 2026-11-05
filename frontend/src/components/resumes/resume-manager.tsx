"use client";

import Link from "next/link";
import { FileText, RefreshCw, Sparkles, UploadCloud } from "lucide-react";
import { useRef, useState, type ChangeEvent } from "react";

import { ResumeCard } from "@/components/resumes/resume-card";
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
import { useMatchingSummary } from "@/features/jobs/hooks";
import {
  useDeleteResume,
  useResumes,
  useSetDefaultResume,
  useUploadResume,
} from "@/features/resumes/hooks";
import { describeError } from "@/lib/api/errors";
import { MAX_RESUME_BYTES, RESUME_ACCEPT } from "@/lib/constants";

const MAX_MB = Math.round(MAX_RESUME_BYTES / (1024 * 1024));

export function ResumeManager() {
  const resumes = useResumes();
  const summary = useMatchingSummary();
  const upload = useUploadResume();
  const setDefault = useSetDefaultResume();
  const remove = useDeleteResume();
  const inputRef = useRef<HTMLInputElement>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  const items = resumes.data ?? [];
  const statsById = new Map(
    (summary.data?.resume_stats ?? []).map((stat) => [stat.resume_id, stat]),
  );

  const openPicker = () => {
    setValidationError(null);
    inputRef.current?.click();
  };

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Allow picking the same file again after a later change.
    event.target.value = "";
    if (!file) {
      return;
    }

    setValidationError(null);
    upload.reset();

    const looksLikePdf =
      file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    if (!looksLikePdf) {
      setValidationError("Only PDF resumes are supported.");
      return;
    }
    if (file.size === 0) {
      setValidationError("That file is empty. Choose a different PDF.");
      return;
    }
    if (file.size > MAX_RESUME_BYTES) {
      setValidationError(`Resumes must be ${MAX_MB} MB or smaller.`);
      return;
    }
    upload.mutate(file);
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Upload a resume</CardTitle>
            <CardDescription>
              PDF only, up to {MAX_MB} MB. Text is extracted on the backend;
              scanned PDFs are reported instead of being silently ignored.
            </CardDescription>
          </div>
          <Badge variant="neutral">
            {items.length} {items.length === 1 ? "resume" : "resumes"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <input
            ref={inputRef}
            type="file"
            accept={RESUME_ACCEPT}
            className="sr-only"
            aria-label="Resume PDF"
            onChange={handleFile}
            disabled={upload.isPending}
          />

          <div className="flex flex-col items-start gap-4 rounded-xl border border-dashed border-white/12 bg-white/[0.015] p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-sky-300">
                <UploadCloud aria-hidden="true" className="h-5 w-5" />
              </span>
              <div>
                <p className="text-[13px] font-medium text-ink">
                  Add a PDF resume to your library
                </p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">
                  ApplyPilot parses contact details, summary, skills, education,
                  experience, projects and certifications from the file. Your
                  candidate profile is never overwritten.
                </p>
              </div>
            </div>
            <Button
              onClick={openPicker}
              disabled={upload.isPending}
              className="w-full sm:w-auto"
            >
              {upload.isPending ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Uploading…
                </>
              ) : (
                <>
                  <UploadCloud aria-hidden="true" className="h-4 w-4" />
                  Choose PDF
                </>
              )}
            </Button>
          </div>

          {validationError ? (
            <Alert variant="danger" title="File not accepted">
              {validationError}
            </Alert>
          ) : null}
          {upload.error ? (
            <Alert variant="danger" title="Upload failed">
              {describeError(upload.error)}
            </Alert>
          ) : null}
          {setDefault.error ? (
            <Alert variant="danger" title="Could not set default resume">
              {describeError(setDefault.error)}
            </Alert>
          ) : null}
          {remove.error ? (
            <Alert variant="danger" title="Could not delete resume">
              {describeError(remove.error)}
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      {items.length > 0 ? <MatchingOverview /> : null}

      {resumes.isPending ? (
        <div className="grid gap-4">
          {[0, 1].map((index) => (
            <Card key={index}>
              <CardContent className="space-y-3">
                <Skeleton className="h-10 w-10 rounded-xl" />
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-3 w-32" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : resumes.isError ? (
        <Card>
          <CardContent>
            <Alert variant="danger" title="Could not load your resumes">
              {describeError(resumes.error)}
            </Alert>
            <Button
              variant="secondary"
              size="sm"
              className="mt-4"
              onClick={() => void resumes.refetch()}
              disabled={resumes.isRefetching}
            >
              <RefreshCw
                aria-hidden="true"
                className={`h-3.5 w-3.5 ${resumes.isRefetching ? "animate-spin" : ""}`}
              />
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <CardContent>
            <EmptyState
              icon={FileText}
              title="No resumes yet"
              description="Upload your first PDF resume. ApplyPilot will store it securely, parse its content and use the default resume for matching."
              action={
                <Button onClick={openPicker} disabled={upload.isPending}>
                  <UploadCloud aria-hidden="true" className="h-4 w-4" />
                  Upload your first resume
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {items.map((resume) => (
            <ResumeCard
              key={resume.id}
              resume={resume}
              matchStat={statsById.get(resume.id)}
              onSetDefault={(id) => setDefault.mutate(id)}
              onDelete={(id) => remove.mutate(id)}
              isSettingDefault={
                setDefault.isPending && setDefault.variables === resume.id
              }
              isDeleting={remove.isPending && remove.variables === resume.id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Library-level matching summary. Scores describe overlap with stored postings;
 * they are not hiring probabilities.
 */
function MatchingOverview() {
  const summary = useMatchingSummary();
  const data = summary.data;

  if (summary.isPending) {
    return (
      <Card>
        <CardContent className="space-y-3" aria-hidden="true">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-14 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (summary.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load match summary">
            {describeError(summary.error)}
          </Alert>
        </CardContent>
      </Card>
    );
  }

  if (!data || data.jobs_matched === 0) {
    return (
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-sky-300">
              <Sparkles aria-hidden="true" className="h-4 w-4" />
            </span>
            <div>
              <p className="text-[13px] font-medium text-ink">
                No jobs matched yet
              </p>
              <p className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">
                Open a saved job and compare your resumes to see which one fits
                best.
              </p>
            </div>
          </div>
          <Link href="/jobs" className={buttonClasses({ variant: "secondary", size: "sm" })}>
            Compare resumes
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Matching overview</CardTitle>
          <CardDescription>
            How your resumes score against stored job postings
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <OverviewStat label="Jobs matched" value={String(data.jobs_matched)} />
          <OverviewStat
            label="Resumes used"
            value={`${data.resumes_matched} of ${data.resumes_total}`}
          />
          <OverviewStat
            label="Average best match"
            value={
              data.average_best_score === null
                ? "—"
                : `${data.average_best_score}%`
            }
          />
        </div>

        {data.best_match ? (
          <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
            <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
              Strongest pairing
            </p>
            <p className="mt-1 text-[12px] text-ink-muted">
              <span className="font-medium text-ink">
                {data.best_match.resume_filename ?? "Resume"}
              </span>{" "}
              for {data.best_match.job_title}
              {data.best_match.company ? ` at ${data.best_match.company}` : ""} ·{" "}
              {data.best_match.overall_score}% match
            </p>
          </div>
        ) : null}

        <p className="text-[11px] leading-relaxed text-ink-subtle">
          {data.disclaimer}
        </p>
      </CardContent>
    </Card>
  );
}

function OverviewStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        {label}
      </p>
      <p className="mt-1 text-lg font-semibold leading-none text-ink">{value}</p>
    </div>
  );
}
