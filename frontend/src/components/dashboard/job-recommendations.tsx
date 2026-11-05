"use client";

import { RefreshCw, Search } from "lucide-react";
import Link from "next/link";

import { JobCard } from "@/components/jobs/job-card";
import { Alert } from "@/components/ui/alert";
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
import { useJobs } from "@/features/jobs/hooks";
import { describeError } from "@/lib/api/errors";

export function JobRecommendations() {
  const jobs = useJobs({ limit: 3 });
  const stored = jobs.data?.jobs ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Recent job recommendations</CardTitle>
          <CardDescription>
            Saved from your Glassdoor alert emails, with resume matches
          </CardDescription>
        </div>
        <Link
          href="/jobs"
          className="text-[12px] font-medium text-sky-300 transition-colors hover:text-sky-200"
        >
          View all
        </Link>
      </CardHeader>

      <CardContent className="space-y-3">
        {jobs.isPending ? (
          <JobsSkeleton />
        ) : jobs.isError ? (
          <>
            <Alert variant="danger">{describeError(jobs.error)}</Alert>
            <button
              type="button"
              onClick={() => void jobs.refetch()}
              className={buttonClasses({ variant: "secondary", size: "sm" })}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Try again
            </button>
          </>
        ) : stored.length > 0 ? (
          <>
            {stored.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
            <p className="pt-1 text-[11px] text-ink-subtle">
              {jobs.data?.total ?? stored.length} saved recommendation
              {(jobs.data?.total ?? stored.length) === 1 ? "" : "s"} ·{" "}
              {stored.filter((job) => job.matches_count > 0).length} with resume
              matches
            </p>
          </>
        ) : (
          <EmptyState
            icon={Search}
            title="No saved job recommendations"
            description="Scan your Glassdoor alert emails on the Job discovery page. Saved postings appear here and can be matched against every resume you upload."
            action={
              <Link href="/jobs" className={buttonClasses({ size: "sm" })}>
                Open job discovery
              </Link>
            }
          />
        )}
      </CardContent>
    </Card>
  );
}

function JobsSkeleton() {
  return (
    <div className="space-y-3" aria-hidden="true">
      {[0, 1, 2].map((index) => (
        <div key={index} className="rounded-xl border border-white/[0.06] p-4">
          <Skeleton className="h-4 w-2/5" />
          <Skeleton className="mt-2.5 h-3 w-3/5" />
          <Skeleton className="mt-3 h-5 w-24 rounded-full" />
        </div>
      ))}
    </div>
  );
}
