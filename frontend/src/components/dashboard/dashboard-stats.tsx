"use client";

import { Briefcase, FileText, Mail, Sparkles } from "lucide-react";

import { StatCard } from "@/components/dashboard/stat-card";
import { useGmailStatus } from "@/features/gmail/hooks";
import { useJobs, useMatchingSummary } from "@/features/jobs/hooks";

/**
 * Only values the backend can actually prove are shown. A metric with no data
 * yet renders an em dash and an explanation rather than a fabricated zero.
 */
export function DashboardStats() {
  const status = useGmailStatus();
  const jobs = useJobs({ limit: 1 });
  const summary = useMatchingSummary();

  const connected = status.data?.connected === true;
  const gmailValue = status.isPending
    ? "…"
    : status.isError
      ? "Unavailable"
      : connected
        ? "Connected"
        : "Not connected";

  const jobsValue = jobs.isPending
    ? "…"
    : jobs.isError
      ? "—"
      : String(jobs.data?.total ?? 0);

  const matchedValue = summary.isPending
    ? "…"
    : summary.isError
      ? "—"
      : String(summary.data?.jobs_matched ?? 0);

  const resumesValue = summary.isPending
    ? "…"
    : summary.isError
      ? "—"
      : String(summary.data?.resumes_total ?? 0);

  const averageBest = summary.data?.average_best_score;

  return (
    <section
      aria-label="Application statistics"
      className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
    >
      <StatCard
        label="Saved jobs"
        value={jobsValue}
        hint="Stored from your Glassdoor alert emails"
        icon={Briefcase}
        tone="blue"
        loading={jobs.isPending}
      />
      <StatCard
        label="Matched jobs"
        value={matchedValue}
        hint={
          averageBest !== null && averageBest !== undefined
            ? `Average best match ${averageBest}%`
            : "Open a job to compare your resumes"
        }
        icon={Sparkles}
        tone="violet"
        loading={summary.isPending}
      />
      <StatCard
        label="Resumes"
        value={resumesValue}
        hint={
          (summary.data?.resumes_total ?? 0) > 0
            ? `${summary.data?.resumes_matched ?? 0} used in matches`
            : "Upload a PDF to start matching"
        }
        icon={FileText}
        tone="slate"
        loading={summary.isPending}
      />
      <StatCard
        label="Gmail"
        value={gmailValue}
        hint={status.data?.gmail_email ?? "Read-only inbox access"}
        icon={Mail}
        tone={connected ? "emerald" : "slate"}
      />
    </section>
  );
}
