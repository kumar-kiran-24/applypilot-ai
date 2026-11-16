"use client";

import {
  ArrowLeft,
  ArrowUpRight,
  Bookmark,
  BookmarkCheck,
  Briefcase,
  Building2,
  CalendarDays,
  EyeOff,
  FilePlus2,
  MapPin,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  APPLICATION_STAGE_LABEL,
  APPLICATION_STATUS,
} from "@/components/applications/application-list";
import { JobIntelligencePanel } from "@/components/intelligence/job-intelligence-panel";
import { ResumeComparisonPanel } from "@/components/intelligence/resume-comparison-panel";
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
  useAnalyzeApplication,
  useApplications,
} from "@/features/applications/hooks";
import {
  useDismissJob,
  useJob,
  useRestoreJob,
  useSaveJob,
  useUnsaveJob,
} from "@/features/jobs/hooks";
import { describeError } from "@/lib/api/errors";
import { formatDate } from "@/lib/utils/format";
import { safeExternalUrl } from "@/lib/utils/url";
import type { JobRequirements } from "@/types/api";

function SkillGroup({
  title,
  skills,
  variant,
}: {
  title: string;
  skills: string[];
  variant: "brand" | "neutral" | "danger";
}) {
  if (skills.length === 0) {
    return (
      <div>
        <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
          {title}
        </p>
        <p className="mt-1 text-[12px] text-ink-subtle">Not stated</p>
      </div>
    );
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

function RequirementsCard({ requirements }: { requirements: JobRequirements }) {
  const experience =
    requirements.experience_years === null
      ? "Not stated"
      : `${requirements.experience_years}+ years`;
  const education = [
    requirements.education_level,
    requirements.education_field,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Requirements at a glance</CardTitle>
          <CardDescription>
            Reported only from what the posting states. Missing evidence stays
            unknown rather than assumed.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
              Experience
            </p>
            <p className="mt-1 text-[13px] text-ink">{experience}</p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
              Education
            </p>
            <p className="mt-1 text-[13px] text-ink">
              {education || "Not stated"}
            </p>
          </div>
        </div>

        <SkillGroup
          title="Required skills"
          skills={requirements.required_skills}
          variant="brand"
        />
        <SkillGroup
          title="Preferred skills"
          skills={requirements.preferred_skills}
          variant="neutral"
        />

        {requirements.responsibilities.length > 0 ? (
          <div>
            <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
              Responsibilities
            </p>
            <ul className="mt-1.5 space-y-1">
              {requirements.responsibilities.slice(0, 6).map((item) => (
                <li
                  key={item}
                  className="flex gap-2 text-[12px] leading-relaxed text-ink-muted"
                >
                  <span aria-hidden="true" className="text-ink-subtle">
                    •
                  </span>
                  {item}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {requirements.confidence ? (
          <p className="text-[11px] text-ink-subtle">
            Requirement confidence: {requirements.confidence}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function JobWorkspace({ jobId }: { jobId: string }) {
  const router = useRouter();
  const job = useJob(jobId);
  const applications = useApplications();
  const analyze = useAnalyzeApplication();
  const save = useSaveJob();
  const unsave = useUnsaveJob();
  const dismiss = useDismissJob();
  const restore = useRestoreJob();

  const application =
    applications.data?.applications.find((item) => item.job_id === jobId) ??
    null;
  const stateError =
    save.error ?? unsave.error ?? dismiss.error ?? restore.error ?? null;
  const statePending =
    save.isPending || unsave.isPending || dismiss.isPending || restore.isPending;

  if (job.isPending) {
    return (
      <div className="space-y-4" aria-hidden="true">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-4 w-80" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (job.isError || !job.data) {
    return (
      <Card>
        <CardContent className="space-y-4">
          <Alert variant="danger" title="Could not open this job">
            {job.error
              ? describeError(job.error)
              : "This posting is not available for your account."}
          </Alert>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void job.refetch()}
            >
              Retry
            </Button>
            <Link
              href="/jobs"
              className={buttonClasses({ variant: "ghost", size: "sm" })}
            >
              Back to the job library
            </Link>
          </div>
        </CardContent>
      </Card>
    );
  }

  const data = job.data;
  const url = safeExternalUrl(data.job_url);
  const saved = data.user_state.saved;
  const dismissed = data.user_state.dismissed;

  const startApplication = () => {
    analyze.mutate(
      { job_id: data.id },
      {
        onSuccess: (preview) => {
          router.push(`/applications/${preview.application_id}`);
        },
      },
    );
  };

  return (
    <div className="space-y-5">
      <Link
        href="/jobs"
        className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted transition-colors hover:text-ink"
      >
        <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
        Job workspace
      </Link>

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-2xl">
            {data.job_title}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-muted">
            <span className="inline-flex items-center gap-1.5">
              <Building2
                aria-hidden="true"
                className="h-3.5 w-3.5 text-ink-subtle"
              />
              {data.company ?? "Company not listed"}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <MapPin
                aria-hidden="true"
                className="h-3.5 w-3.5 text-ink-subtle"
              />
              {data.location ?? "Location not listed"}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <CalendarDays
                aria-hidden="true"
                className="h-3.5 w-3.5 text-ink-subtle"
              />
              Received {formatDate(data.received_at)}
            </span>
          </div>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <Badge variant="neutral">{data.source}</Badge>
            {saved ? (
              <Badge variant="success">
                <BookmarkCheck aria-hidden="true" className="h-3 w-3" />
                Saved
              </Badge>
            ) : null}
            {dismissed ? <Badge variant="warning">Dismissed</Badge> : null}
            {data.best_match ? (
              <Badge variant="brand">
                {data.best_match.overall_score}% best match
              </Badge>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonClasses({ variant: "secondary", size: "sm" })}
            >
              <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
              Original posting
            </a>
          ) : null}
          <Button
            variant="secondary"
            size="sm"
            disabled={statePending}
            aria-pressed={saved}
            onClick={() => {
              if (saved) {
                unsave.mutate(data.id);
              } else {
                save.mutate(data.id);
              }
            }}
          >
            {saved ? (
              <BookmarkCheck aria-hidden="true" className="h-3.5 w-3.5" />
            ) : (
              <Bookmark aria-hidden="true" className="h-3.5 w-3.5" />
            )}
            {saved ? "Saved" : "Save job"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={statePending}
            onClick={() => {
              if (dismissed) {
                restore.mutate(data.id);
              } else {
                dismiss.mutate(data.id);
              }
            }}
          >
            {dismissed ? (
              <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
            ) : (
              <EyeOff aria-hidden="true" className="h-3.5 w-3.5" />
            )}
            {dismissed ? "Restore" : "Dismiss"}
          </Button>
        </div>
      </div>

      {stateError ? (
        <Alert variant="danger" title="Could not update this recommendation">
          {describeError(stateError)}
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Application</CardTitle>
            <CardDescription>
              ApplyPilot prepares the questionnaire and a recommended resume.
              You review every answer and submit the application yourself.
            </CardDescription>
          </div>
          {application ? (
            <Badge variant="brand">{application.platform_name}</Badge>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4">
          {applications.isPending ? (
            <Skeleton className="h-10 w-full" />
          ) : application ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-4 py-3">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 text-[13px] text-ink">
                  <span>{APPLICATION_STATUS[application.status].label}</span>
                  {application.stage ? (
                    <Badge variant="brand">
                      {APPLICATION_STAGE_LABEL[application.stage]}
                    </Badge>
                  ) : null}
                </p>
                <p className="mt-1 text-[11px] text-ink-subtle">
                  {application.questions_confirmed} of{" "}
                  {application.questions_total} answers confirmed ·{" "}
                  {application.submitted_at
                    ? `submitted ${formatDate(application.submitted_at)}`
                    : `prepared ${formatDate(application.created_at)}`}
                </p>
              </div>
              <Link
                href={`/applications/${application.id}`}
                className={buttonClasses({ size: "sm" })}
              >
                <Briefcase aria-hidden="true" className="h-3.5 w-3.5" />
                Open application
              </Link>
            </div>
          ) : (
            <EmptyState
              icon={Briefcase}
              title="No application started for this job"
              description="Start an application to build the questionnaire from your verified profile and choose a resume. Nothing is submitted automatically."
              action={
                <Button
                  size="sm"
                  onClick={startApplication}
                  disabled={analyze.isPending}
                >
                  {analyze.isPending ? (
                    <>
                      <Spinner className="h-3.5 w-3.5" />
                      Preparing…
                    </>
                  ) : (
                    <>
                      <FilePlus2 aria-hidden="true" className="h-3.5 w-3.5" />
                      Start application
                    </>
                  )}
                </Button>
              }
            />
          )}

          {analyze.error ? (
            <Alert variant="danger" title="Could not prepare this application">
              {describeError(analyze.error)}
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Job description</CardTitle>
                <CardDescription>
                  Exactly the text stored from the alert email. Nothing is
                  rewritten or summarised here.
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {data.job_description ? (
                <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-muted">
                  {data.job_description}
                </p>
              ) : (
                <p className="text-[13px] text-ink-subtle">
                  This posting has no stored description. Analyze it to extract
                  the fields that are available, or open the original posting.
                </p>
              )}
            </CardContent>
          </Card>

          <JobIntelligencePanel jobId={data.id} />
          <ResumeComparisonPanel jobId={data.id} />
        </div>

        <div className="space-y-5">
          <RequirementsCard requirements={data.requirements} />

          <Card>
            <CardHeader>
              <div>
                <CardTitle>Posting details</CardTitle>
                <CardDescription>
                  Provenance for this stored recommendation.
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 text-[12px]">
              <div className="flex items-start justify-between gap-3">
                <span className="text-ink-subtle">Email subject</span>
                <span className="max-w-[60%] break-words text-right text-ink-muted">
                  {data.email_subject}
                </span>
              </div>
              <div className="flex items-start justify-between gap-3">
                <span className="text-ink-subtle">Source</span>
                <span className="text-ink-muted">{data.source}</span>
              </div>
              <div className="flex items-start justify-between gap-3">
                <span className="text-ink-subtle">Received</span>
                <span className="text-ink-muted">
                  {formatDate(data.received_at)}
                </span>
              </div>
              <div className="flex items-start justify-between gap-3">
                <span className="text-ink-subtle">Stored</span>
                <span className="text-ink-muted">
                  {formatDate(data.discovered_at)}
                </span>
              </div>
              {saved && data.user_state.saved_at ? (
                <div className="flex items-start justify-between gap-3">
                  <span className="text-ink-subtle">Saved</span>
                  <span className="text-ink-muted">
                    {formatDate(data.user_state.saved_at)}
                  </span>
                </div>
              ) : null}
              {dismissed && data.user_state.dismissed_at ? (
                <div className="flex items-start justify-between gap-3">
                  <span className="text-ink-subtle">Dismissed</span>
                  <span className="text-ink-muted">
                    {formatDate(data.user_state.dismissed_at)}
                  </span>
                </div>
              ) : null}
              <div className="flex items-center gap-2 border-t border-white/[0.06] pt-3 text-[11px] text-ink-subtle">
                <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                Match scores are comparisons, not hiring probabilities.
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
