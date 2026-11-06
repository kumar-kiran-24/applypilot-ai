"use client";

import Link from "next/link";
import { Briefcase, ChevronRight, RefreshCw } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClasses } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useApplications } from "@/features/applications/hooks";
import { describeError } from "@/lib/api/errors";
import { formatDate } from "@/lib/utils/format";
import type {
  ApplicationStage,
  ApplicationStatus,
  ApplicationSummary,
  AutomationMode,
} from "@/types/api";

const STATUS: Record<
  ApplicationStatus,
  { label: string; variant: "neutral" | "success" | "warning" | "danger" | "brand" }
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

const STAGE_LABEL: Record<ApplicationStage, string> = {
  applied: "Applied",
  screening: "Screening",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

const MODE_LABEL: Record<AutomationMode, string> = {
  automated: "Automated form filling",
  assisted: "Prepared, you submit",
  manual: "Guided manual",
};

function ApplicationRow({ application }: { application: ApplicationSummary }) {
  const status = STATUS[application.status];
  return (
    <Link
      href={`/applications/${application.id}`}
      className="group block rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 transition-colors hover:border-white/[0.16] hover:bg-white/[0.035] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-blue/60"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">
            {application.job_title}
          </p>
          <p className="mt-0.5 text-[12px] text-ink-muted">
            {application.company ?? "Company not listed"} ·{" "}
            {application.platform_name}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {application.stage ? (
            <Badge variant="brand">{STAGE_LABEL[application.stage]}</Badge>
          ) : null}
          <Badge variant={status.variant}>{status.label}</Badge>
          <ChevronRight
            aria-hidden="true"
            className="h-4 w-4 text-ink-subtle transition-transform group-hover:translate-x-0.5"
          />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge variant="neutral">{MODE_LABEL[application.automation_mode]}</Badge>
        {application.resume_filename ? (
          <Badge variant="neutral" className="max-w-full truncate">
            {application.resume_filename}
          </Badge>
        ) : (
          <Badge variant="warning">No resume selected</Badge>
        )}
        {application.requires_account ? (
          <Badge variant="warning">Company account needed</Badge>
        ) : null}
        {application.missing_required_count > 0 ? (
          <Badge variant="warning">
            {application.missing_required_count} required left
          </Badge>
        ) : (
          <Badge variant="success">Answers complete</Badge>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink-subtle">
        <span>
          {application.questions_confirmed} of {application.questions_total} answers
          confirmed
        </span>
        <span>
          {application.submitted_at
            ? `Submitted ${formatDate(application.submitted_at)}`
            : `Prepared ${formatDate(application.created_at)}`}
        </span>
      </div>
    </Link>
  );
}

export function ApplicationList() {
  const applications = useApplications();
  const items = applications.data?.applications ?? [];

  if (applications.isPending) {
    return (
      <div className="grid gap-3">
        {[0, 1, 2].map((index) => (
          <Card key={index}>
            <CardContent className="space-y-3">
              <Skeleton className="h-4 w-56" />
              <Skeleton className="h-3 w-40" />
              <Skeleton className="h-3 w-full" />
            </CardContent>
          </Card>
        ))}
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
            disabled={applications.isRefetching}
          >
            <RefreshCw
              aria-hidden="true"
              className={`h-3.5 w-3.5 ${applications.isRefetching ? "animate-spin" : ""}`}
            />
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
            icon={Briefcase}
            title="No applications yet"
            description="Open a discovered job and choose Prepare application. ApplyPilot builds the questionnaire, selects a resume and never submits anything without your explicit approval."
            action={
              <Link href="/jobs" className={buttonClasses({ size: "sm" })}>
                Browse discovered jobs
              </Link>
            }
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3">
      {items.map((application) => (
        <ApplicationRow key={application.id} application={application} />
      ))}
    </div>
  );
}
