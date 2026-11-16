"use client";

import {
  ChevronLeft,
  ChevronRight,
  ListFilter,
  RefreshCw,
  Search,
} from "lucide-react";
import { useState } from "react";

import { JobCard } from "@/components/jobs/job-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
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
import type { JobListQuery, JobSort, JobStateFilter } from "@/types/api";

const PAGE_SIZE = 10;

const STATE_TABS: ReadonlyArray<{ value: JobStateFilter; label: string }> = [
  { value: "active", label: "Active" },
  { value: "saved", label: "Saved" },
  { value: "dismissed", label: "Dismissed" },
  { value: "all", label: "All" },
];

const SORT_OPTIONS: ReadonlyArray<{ value: JobSort; label: string }> = [
  { value: "received_desc", label: "Newest first" },
  { value: "received_asc", label: "Oldest first" },
  { value: "title_asc", label: "Title A–Z" },
  { value: "title_desc", label: "Title Z–A" },
  { value: "company_asc", label: "Company A–Z" },
  { value: "company_desc", label: "Company Z–A" },
  { value: "saved_desc", label: "Recently saved" },
];

const MATCH_OPTIONS = [
  { value: "", label: "Any match state" },
  { value: "true", label: "With resume matches" },
  { value: "false", label: "Without resume matches" },
] as const;

function JobLibrarySkeleton() {
  return (
    <div className="space-y-3" aria-hidden="true">
      {[0, 1, 2].map((index) => (
        <div key={index} className="rounded-xl border border-white/[0.06] p-4">
          <Skeleton className="h-4 w-2/5" />
          <Skeleton className="mt-2.5 h-3 w-3/5" />
          <Skeleton className="mt-3 h-5 w-32 rounded-full" />
        </div>
      ))}
    </div>
  );
}

/**
 * The job workspace index: every stored posting with the filters the backend
 * actually supports. Saved/dismissed decisions are per user and sent to the
 * state endpoints; nothing is filtered or counted in the browser.
 */
export function JobLibrary() {
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState("");
  const [source, setSource] = useState("");
  const [hasMatches, setHasMatches] = useState("");
  const [state, setState] = useState<JobStateFilter>("active");
  const [sort, setSort] = useState<JobSort>("received_desc");
  const [offset, setOffset] = useState(0);
  const [showFilters, setShowFilters] = useState(false);

  const query: JobListQuery = {
    limit: PAGE_SIZE,
    offset,
    state,
    sort,
    has_matches:
      hasMatches === "" ? undefined : hasMatches === "true" ? true : false,
    ...(q ? { q } : {}),
    ...(company.trim() ? { company: company.trim() } : {}),
    ...(location.trim() ? { location: location.trim() } : {}),
    ...(source ? { source } : {}),
  };

  const jobs = useJobs(query);
  const items = jobs.data?.jobs ?? [];
  const total = jobs.data?.total ?? 0;
  const filtered = Boolean(q || company.trim() || location.trim() || source || hasMatches);

  const resetPage = () => setOffset(0);

  const clearFilters = () => {
    setSearchInput("");
    setQ("");
    setCompany("");
    setLocation("");
    setSource("");
    setHasMatches("");
    setSort("received_desc");
    resetPage();
  };

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Job library</CardTitle>
          <CardDescription>
            Search and filter every posting stored from your alert emails, then
            open one to analyze it and compare your resumes.
          </CardDescription>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void jobs.refetch()}
          disabled={jobs.isRefetching}
        >
          <RefreshCw
            aria-hidden="true"
            className={`h-3.5 w-3.5 ${jobs.isRefetching ? "animate-spin" : ""}`}
          />
          Refresh
        </Button>
      </CardHeader>

      <CardContent className="space-y-4">
        <div
          role="tablist"
          aria-label="Job recommendation state"
          className="flex flex-wrap gap-1.5"
        >
          {STATE_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              role="tab"
              aria-selected={state === tab.value}
              onClick={() => {
                setState(tab.value);
                resetPage();
              }}
              className={
                state === tab.value
                  ? "rounded-lg border border-brand-blue/40 bg-brand-blue/[0.12] px-3 py-1.5 text-[12px] font-medium text-sky-200"
                  : "rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12px] text-ink-muted transition-colors hover:border-white/20 hover:text-ink"
              }
            >
              {tab.label}
            </button>
          ))}
        </div>

        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            setQ(searchInput.trim());
            resetPage();
          }}
        >
          <div className="relative min-w-0 flex-1">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle"
            />
            <input
              id="job-search"
              type="search"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search title, company, location or description"
              aria-label="Search jobs"
              className="field pl-9"
            />
          </div>
          <div className="flex gap-2">
            <Button type="submit" disabled={jobs.isFetching}>
              <Search aria-hidden="true" className="h-4 w-4" />
              Search
            </Button>
            <Button
              type="button"
              variant="secondary"
              aria-expanded={showFilters}
              onClick={() => setShowFilters((value) => !value)}
            >
              <ListFilter aria-hidden="true" className="h-4 w-4" />
              Filters
            </Button>
          </div>
        </form>

        {showFilters ? (
          <div className="grid gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label
                htmlFor="job-company"
                className="mb-1.5 block text-[12px] text-ink-muted"
              >
                Company
              </label>
              <input
                id="job-company"
                value={company}
                onChange={(event) => {
                  setCompany(event.target.value);
                  resetPage();
                }}
                placeholder="Any company"
                className="field"
              />
            </div>
            <div>
              <label
                htmlFor="job-location"
                className="mb-1.5 block text-[12px] text-ink-muted"
              >
                Location
              </label>
              <input
                id="job-location"
                value={location}
                onChange={(event) => {
                  setLocation(event.target.value);
                  resetPage();
                }}
                placeholder="Any location"
                className="field"
              />
            </div>
            <div>
              <label
                htmlFor="job-source"
                className="mb-1.5 block text-[12px] text-ink-muted"
              >
                Source
              </label>
              <select
                id="job-source"
                value={source}
                onChange={(event) => {
                  setSource(event.target.value);
                  resetPage();
                }}
                className="field"
              >
                <option value="">Any source</option>
                <option value="glassdoor">Glassdoor</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="job-matches"
                className="mb-1.5 block text-[12px] text-ink-muted"
              >
                Match filter
              </label>
              <select
                id="job-matches"
                value={hasMatches}
                onChange={(event) => {
                  setHasMatches(event.target.value);
                  resetPage();
                }}
                className="field"
              >
                {MATCH_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="job-sort"
                className="mb-1.5 block text-[12px] text-ink-muted"
              >
                Sort by
              </label>
              <select
                id="job-sort"
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value as JobSort);
                  resetPage();
                }}
                className="field"
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-end">
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            </div>
          </div>
        ) : null}

        {jobs.isPending ? (
          <JobLibrarySkeleton />
        ) : jobs.isError ? (
          <div className="space-y-3">
            <Alert variant="danger" title="Could not load jobs">
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
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Search}
            title={
              filtered
                ? "No jobs match these filters"
                : state === "saved"
                  ? "No saved jobs yet"
                  : state === "dismissed"
                    ? "Nothing dismissed"
                    : "No stored jobs yet"
            }
            description={
              filtered
                ? "Try a different search term or clear the filters. Only postings already stored from your alert emails are searched."
                : state === "saved"
                  ? "Save a recommendation from the active list to keep it here. Saved jobs stay available even after new scans."
                  : state === "dismissed"
                    ? "Dismissed postings are hidden from the active list but never deleted, so they can be restored from here."
                    : "Scan your Glassdoor alert emails to store postings, then return here to search, filter and match them."
            }
            action={
              filtered ? (
                <Button variant="secondary" size="sm" onClick={clearFilters}>
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="space-y-3">
            <p className="text-[12px] text-ink-muted">
              Showing{" "}
              <span className="text-ink">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)}
              </span>{" "}
              of <span className="text-ink">{total}</span>{" "}
              {state === "saved"
                ? "saved"
                : state === "dismissed"
                  ? "dismissed"
                  : "stored"}{" "}
              recommendation{total === 1 ? "" : "s"}
            </p>
            {items.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
            <div className="flex items-center justify-between gap-2 pt-1">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0 || jobs.isFetching}
              >
                <ChevronLeft aria-hidden="true" className="h-3.5 w-3.5" />
                Previous
              </Button>
              <p className="text-[11px] text-ink-subtle">
                Page {Math.floor(offset / PAGE_SIZE) + 1} of{" "}
                {Math.max(1, Math.ceil(total / PAGE_SIZE))}
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={offset + PAGE_SIZE >= total || jobs.isFetching}
              >
                Next
                <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
