"use client";

import { Mail, RefreshCw, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { JobCard } from "@/components/jobs/job-card";
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
import { discoveryErrorCopy } from "@/features/gmail/errors";
import { useConnectGmail, useGmailStatus } from "@/features/gmail/hooks";
import {
  useBulkMatchJobs,
  useDiscoverJobs,
  useJobs,
} from "@/features/jobs/hooks";
import { describeError } from "@/lib/api/errors";

const DAY_OPTIONS = [7, 30, 60, 90];
const MESSAGE_OPTIONS = [10, 25, 50];

function JobListSkeleton() {
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

export function GlassdoorDiscovery({
  defaultDays = 30,
  defaultMaxMessages = 25,
}) {
  const status = useGmailStatus();
  const connect = useConnectGmail();
  const scan = useDiscoverJobs();
  const jobs = useJobs({ limit: 50 });
  const bulk = useBulkMatchJobs();
  const [days, setDays] = useState(defaultDays);
  const [maxMessages, setMaxMessages] = useState(defaultMaxMessages);

  const connected = status.data?.connected === true;
  const errorCopy = scan.error ? discoveryErrorCopy(scan.error) : null;
  const stored = jobs.data?.jobs ?? [];
  const matched = stored.filter((job) => job.matches_count > 0).length;

  const scanAgain = () => scan.mutate({ days, maxMessages });

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Glassdoor job discovery</CardTitle>
          <CardDescription>
            Reads job alert emails from Gmail and saves each posting so it can
            be matched against your resumes. Nothing is scraped or auto-applied.
          </CardDescription>
        </div>
        <Badge variant="brand">Read-only</Badge>
      </CardHeader>

      <CardContent className="space-y-5">
        {status.isPending ? (
          <div className="grid gap-3 sm:grid-cols-3" aria-hidden="true">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : status.isError ? (
          <>
            <Alert variant="danger">{describeError(status.error)}</Alert>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void status.refetch()}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Retry
            </Button>
          </>
        ) : !connected ? (
          <EmptyState
            icon={Mail}
            title="Connect Gmail to scan for jobs"
            description="Job discovery searches your Glassdoor alert emails. Connect the inbox you receive them in, then scan. Stored jobs remain available for matching."
            action={
              <Button
                size="sm"
                onClick={() => connect.mutate()}
                disabled={connect.isPending}
              >
                {connect.isPending ? (
                  <>
                    <Spinner className="h-3.5 w-3.5" />
                    Opening Google…
                  </>
                ) : (
                  <>
                    <Mail aria-hidden="true" className="h-3.5 w-3.5" />
                    Connect Gmail
                  </>
                )}
              </Button>
            }
          />
        ) : (
          <>
            <form
              className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
              onSubmit={(event) => {
                event.preventDefault();
                scanAgain();
              }}
            >
              <div>
                <label
                  htmlFor="discovery-days"
                  className="mb-1.5 block text-[12px] text-ink-muted"
                >
                  Search window
                </label>
                <select
                  id="discovery-days"
                  value={days}
                  onChange={(event) => setDays(Number(event.target.value))}
                  disabled={scan.isPending}
                  className="field"
                >
                  {DAY_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      Last {option} days
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label
                  htmlFor="discovery-messages"
                  className="mb-1.5 block text-[12px] text-ink-muted"
                >
                  Emails to inspect
                </label>
                <select
                  id="discovery-messages"
                  value={maxMessages}
                  onChange={(event) =>
                    setMaxMessages(Number(event.target.value))
                  }
                  disabled={scan.isPending}
                  className="field"
                >
                  {MESSAGE_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      Up to {option} emails
                    </option>
                  ))}
                </select>
              </div>
              <Button
                type="submit"
                disabled={scan.isPending}
                className="sm:w-40"
              >
                {scan.isPending ? (
                  <>
                    <Spinner className="h-4 w-4" />
                    Scanning…
                  </>
                ) : (
                  <>
                    <Search aria-hidden="true" className="h-4 w-4" />
                    Scan emails
                  </>
                )}
              </Button>
            </form>

            <p className="text-[11px] leading-relaxed text-ink-subtle">
              Gmail messages are fetched one at a time, so a scan can take up to
              a minute. Postings are saved once and updated on later scans.
            </p>
          </>
        )}

        {scan.isSuccess ? (
          <Alert variant="success" title="Scan complete">
            Inspected recent Glassdoor emails: {scan.data.jobs_found}{" "}
            recommendation{scan.data.jobs_found === 1 ? "" : "s"} found,{" "}
            {scan.data.jobs_created} new and {scan.data.jobs_updated} updated.
          </Alert>
        ) : null}

        {errorCopy ? (
          <>
            <Alert variant="danger" title="Discovery failed">
              {errorCopy.message}
            </Alert>
            <div className="flex flex-wrap gap-2">
              {errorCopy.reconnect ? (
                <Button
                  size="sm"
                  onClick={() => connect.mutate()}
                  disabled={connect.isPending}
                >
                  {connect.isPending ? (
                    <>
                      <Spinner className="h-3.5 w-3.5" />
                      Opening Google…
                    </>
                  ) : (
                    "Reconnect Gmail"
                  )}
                </Button>
              ) : (
                <Button variant="secondary" size="sm" onClick={scanAgain}>
                  <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
                  Try again
                </Button>
              )}
              <Link
                href="/gmail"
                className={buttonClasses({ variant: "ghost", size: "sm" })}
              >
                Connection details
              </Link>
            </div>
          </>
        ) : null}

        {bulk.error ? (
          <Alert variant="danger" title="Matching failed">
            {describeError(bulk.error)}
          </Alert>
        ) : null}

        {bulk.isSuccess ? (
          <Alert variant="success" title="Matching complete">
            {bulk.data.matched_jobs} job
            {bulk.data.matched_jobs === 1 ? "" : "s"} now have resume matches.
            Open a job to compare resumes.
          </Alert>
        ) : null}

        {jobs.isPending ? (
          <JobListSkeleton />
        ) : jobs.isError ? (
          <>
            <Alert variant="danger" title="Could not load stored jobs">
              {describeError(jobs.error)}
            </Alert>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void jobs.refetch()}
            >
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
              Retry
            </Button>
          </>
        ) : stored.length > 0 ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[12px] text-ink-muted">
                <span className="text-ink">{jobs.data?.total ?? stored.length}</span>{" "}
                saved recommendation
                {(jobs.data?.total ?? stored.length) === 1 ? "" : "s"} ·{" "}
                <span className="text-ink">{matched}</span> with matches
              </p>
              <div className="flex flex-wrap items-center gap-2">
                {connected ? (
                  <button
                    type="button"
                    onClick={scanAgain}
                    disabled={scan.isPending}
                    className="inline-flex items-center gap-1.5 text-[12px] text-sky-300 transition-colors hover:text-sky-200 disabled:opacity-50"
                  >
                    <RefreshCw
                      aria-hidden="true"
                      className={`h-3.5 w-3.5 ${scan.isPending ? "animate-spin" : ""}`}
                    />
                    Scan again
                  </button>
                ) : null}
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => bulk.mutate({ limit: 25 })}
                  disabled={bulk.isPending || stored.length === 0}
                >
                  {bulk.isPending ? (
                    <>
                      <Spinner className="h-3.5 w-3.5" />
                      Matching…
                    </>
                  ) : (
                    <>
                      <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                      Match all jobs
                    </>
                  )}
                </Button>
              </div>
            </div>
            <div className="space-y-3">
              {stored.map((job) => (
                <JobCard key={job.id} job={job} />
              ))}
            </div>
          </div>
        ) : (
          <EmptyState
            icon={Search}
            title={
              connected
                ? "No Glassdoor recommendations saved yet"
                : "No saved job recommendations"
            }
            description={
              connected
                ? `No Glassdoor job emails matched in the last ${days} days. Try a wider search window or wait for the next alert.`
                : "Connect Gmail and scan your Glassdoor alerts. Saved postings stay available here even when Gmail is disconnected."
            }
            action={
              connected ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={scanAgain}
                  disabled={scan.isPending}
                >
                  <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
                  Scan again
                </Button>
              ) : (
                <Button
                  size="sm"
                  onClick={() => connect.mutate()}
                  disabled={connect.isPending}
                >
                  <Mail aria-hidden="true" className="h-3.5 w-3.5" />
                  Connect Gmail
                </Button>
              )
            }
          />
        )}
      </CardContent>
    </Card>
  );
}
