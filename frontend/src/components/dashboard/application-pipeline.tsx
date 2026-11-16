"use client";

import { Briefcase, ChevronRight, RefreshCw } from "lucide-react";
import Link from "next/link";

import {
  APPLICATION_STAGE_LABEL,
  APPLICATION_STATUS,
} from "@/components/applications/application-list";
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
import { useApplicationPipeline } from "@/features/applications/hooks";
import { describeError } from "@/lib/api/errors";
import { formatDate } from "@/lib/utils/format";
import type { ApplicationStage } from "@/types/api";

const STAGE_ORDER: ApplicationStage[] = [
  "applied",
  "screening",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

function SummaryTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        {label}
      </p>
      <p className="mt-1 text-xl font-semibold text-ink">{value}</p>
      <p className="text-[11px] text-ink-subtle">{hint}</p>
    </div>
  );
}

/**
 * Counts and stages come from `GET /applications/pipeline`, which aggregates the
 * stored `status` and `stage` columns. The dashboard keeps no lifecycle of its
 * own, so the numbers cannot drift from the application workspace.
 */
export function ApplicationPipelineCard() {
  const pipeline = useApplicationPipeline();
  const data = pipeline.data;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Application pipeline</CardTitle>
          <CardDescription>
            Live counts from your stored applications. Statuses come from the
            prepare → approve → submit flow; stages are the ones you set after
            submitting.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          {data && data.total > 0 ? (
            <Badge variant="neutral">{data.total} total</Badge>
          ) : null}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void pipeline.refetch()}
            disabled={pipeline.isRefetching}
          >
            <RefreshCw
              aria-hidden="true"
              className={`h-3.5 w-3.5 ${pipeline.isRefetching ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {pipeline.isPending ? (
          <div className="space-y-3" aria-hidden="true">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[0, 1, 2, 3].map((index) => (
                <Skeleton key={index} className="h-20 w-full rounded-xl" />
              ))}
            </div>
            <Skeleton className="h-16 w-full rounded-xl" />
          </div>
        ) : pipeline.isError ? (
          <div className="space-y-3">
            <Alert variant="danger" title="Could not load the pipeline">
              {describeError(pipeline.error)}
            </Alert>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void pipeline.refetch()}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Retry
            </Button>
          </div>
        ) : !data || data.total === 0 ? (
          <EmptyState
            icon={Briefcase}
            title="No applications yet"
            description="Start an application from a job in your library. It will appear here with its real status and stage as you move through the workflow."
            action={
              <Link href="/jobs" className={buttonClasses({ size: "sm" })}>
                Browse job workspace
              </Link>
            }
          />
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <SummaryTile
                label="In progress"
                value={data.in_progress}
                hint="Prepared but not submitted"
              />
              <SummaryTile
                label="Submitted"
                value={data.submitted}
                hint="Recorded as submitted"
              />
              <SummaryTile
                label="Failed"
                value={data.failed}
                hint="Needs your attention"
              />
              <SummaryTile
                label="Cancelled"
                value={data.cancelled}
                hint="Stopped by you"
              />
            </div>

            <div>
              <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                By stage
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {STAGE_ORDER.map((stage) => (
                  <Badge
                    key={stage}
                    variant={data.by_stage[stage] > 0 ? "brand" : "neutral"}
                  >
                    {APPLICATION_STAGE_LABEL[stage]} · {data.by_stage[stage]}
                  </Badge>
                ))}
              </div>
            </div>

            {data.recent.length > 0 ? (
              <div>
                <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                  Recent applications
                </p>
                <ul className="mt-2 space-y-2">
                  {data.recent.map((application) => (
                    <li key={application.id}>
                      <Link
                        href={`/applications/${application.id}`}
                        className="group flex items-center justify-between gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 transition-colors hover:border-white/[0.16] hover:bg-white/[0.04]"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-[12px] font-medium text-ink">
                            {application.job_title}
                          </p>
                          <p className="mt-0.5 truncate text-[11px] text-ink-subtle">
                            {application.company ?? "Company not listed"} ·{" "}
                            {application.platform_name} ·{" "}
                            {formatDate(
                              application.submitted_at ?? application.updated_at,
                            )}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          {application.stage ? (
                            <Badge variant="brand">
                              {APPLICATION_STAGE_LABEL[application.stage]}
                            </Badge>
                          ) : null}
                          <Badge
                            variant={
                              APPLICATION_STATUS[application.status].variant
                            }
                          >
                            {APPLICATION_STATUS[application.status].label}
                          </Badge>
                          <ChevronRight
                            aria-hidden="true"
                            className="h-4 w-4 text-ink-subtle transition-transform group-hover:translate-x-0.5"
                          />
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
