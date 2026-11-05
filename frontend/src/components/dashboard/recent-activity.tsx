"use client";

import { Activity, Briefcase, Mail, Sparkles, Star } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { useGmailStatus } from "@/features/gmail/hooks";
import { useJobs, useMatchingSummary } from "@/features/jobs/hooks";
import { formatRelative } from "@/lib/utils/format";

interface ActivityEntry {
  key: string;
  icon: typeof Mail;
  title: string;
  detail: string;
  when: string;
}

/**
 * Activity is derived strictly from data the backend actually stores: the Gmail
 * connection timestamp, saved job postings and the most recent match run.
 */
export function RecentActivity() {
  const status = useGmailStatus();
  const jobs = useJobs({ limit: 5 });
  const summary = useMatchingSummary();

  const entries: ActivityEntry[] = [];

  if (status.data?.connected && status.data.connected_at) {
    entries.push({
      key: "gmail-connected",
      icon: Mail,
      title: "Gmail connected",
      detail: status.data.gmail_email ?? "Read-only access granted",
      when: formatRelative(status.data.connected_at),
    });
  }

  const latest = jobs.data?.jobs[0];
  if (latest) {
    const place = [latest.company, latest.location].filter(Boolean).join(" · ");
    entries.push({
      key: `job-saved-${latest.id}`,
      icon: Briefcase,
      title: `Saved ${latest.job_title}`,
      detail: place || "Glassdoor job alert",
      when: formatRelative(latest.received_at ?? latest.discovered_at),
    });
  }

  if (summary.data && summary.data.last_computed_at) {
    const matched = summary.data.jobs_matched;
    const average = summary.data.average_best_score;
    entries.push({
      key: "matches-computed",
      icon: Sparkles,
      title: `Matched ${matched} job${matched === 1 ? "" : "s"} to your resumes`,
      detail:
        average !== null
          ? `Average best match ${average}%`
          : "Open a job to see the comparison",
      when: formatRelative(summary.data.last_computed_at),
    });
  }

  const best = summary.data?.best_match;
  if (best) {
    entries.push({
      key: `best-match-${best.job_id}`,
      icon: Star,
      title: `Best match: ${best.job_title}`,
      detail: `${best.resume_filename ?? "Resume"} · ${best.overall_score}% compatibility`,
      when: best.company ?? "Top pairing",
    });
  }

  const loading = status.isPending || jobs.isPending || summary.isPending;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Recent activity</CardTitle>
          <CardDescription>
            What ApplyPilot has done in this workspace
          </CardDescription>
        </div>
        <Badge variant="neutral">
          <Activity aria-hidden="true" className="h-3 w-3" />
          Live
        </Badge>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-4" aria-hidden="true">
            {[0, 1].map((index) => (
              <div key={index} className="flex gap-3">
                <div className="h-8 w-8 animate-pulse rounded-lg bg-white/[0.06]" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-1/3 animate-pulse rounded bg-white/[0.06]" />
                  <div className="h-3 w-2/3 animate-pulse rounded bg-white/[0.04]" />
                </div>
              </div>
            ))}
          </div>
        ) : entries.length === 0 ? (
          <EmptyState
            icon={Activity}
            title="No activity yet"
            description="Connect Gmail and scan your Glassdoor alerts, then match a job against your resumes. Those events appear here."
          />
        ) : (
          <ol className="space-y-4">
            {entries.map((entry) => {
              const Icon = entry.icon;
              return (
                <li key={entry.key} className="flex gap-3">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-sky-300">
                    <Icon aria-hidden="true" className="h-3.5 w-3.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <p className="text-[13px] font-medium text-ink">
                        {entry.title}
                      </p>
                      <p className="text-[11px] text-ink-subtle">
                        {entry.when}
                      </p>
                    </div>
                    <p className="mt-0.5 truncate text-[12px] text-ink-muted">
                      {entry.detail}
                    </p>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
