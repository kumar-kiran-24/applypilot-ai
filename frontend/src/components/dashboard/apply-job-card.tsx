"use client";

import {
  ArrowRight,
  Building2,
  ClipboardPaste,
  Link2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useImportJob } from "@/features/jobs/hooks";
import { validateJobImportUrl } from "@/features/jobs/import-url";
import { describeError } from "@/lib/api/errors";

/**
 * Dashboard entry point for applying to a job the user found themselves.
 *
 * The pasted URL is validated locally for an immediate message and again by the
 * backend (host allow-list, DNS/public-address check) before a job record is
 * created or reused. Glassdoor is never fetched or scraped: what cannot be read
 * from the URL stays unknown on the job workspace.
 */
export function ApplyJobCard() {
  const router = useRouter();
  const importJob = useImportJob();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const imported = importJob.data;

  // Show the success state briefly, then open the job workspace. Importing the
  // same posting again reuses its id, so this cannot fork the library.
  useEffect(() => {
    if (!imported) return;
    const timer = setTimeout(() => {
      router.push(`/jobs/${imported.job.id}`);
    }, 600);
    return () => clearTimeout(timer);
  }, [imported, router]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    const validation = validateJobImportUrl(url);
    if (!validation.ok) {
      setError(validation.error);
      return;
    }
    try {
      await importJob.mutateAsync(validation.url);
    } catch {
      // Rendered below with the backend's exact reason.
    }
  };

  const busy = importJob.isPending || Boolean(imported);

  return (
    <section
      aria-labelledby="apply-to-job-heading"
      className="relative overflow-hidden rounded-3xl border border-sky-400/20 bg-navy-850/70 p-6 shadow-card backdrop-blur-md sm:p-7"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-surface-sheen"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-sky-500/15 blur-[90px]"
      />

      <div className="relative grid gap-6 lg:grid-cols-[1.2fr_1fr] lg:items-center">
        <div>
          <Badge variant="brand">
            <Sparkles aria-hidden="true" className="h-3 w-3" />
            Apply to a Job
          </Badge>
          <h2
            id="apply-to-job-heading"
            className="mt-3 text-xl font-semibold tracking-tight text-ink sm:text-2xl"
          >
            Have a job link? Start from the listing.
          </h2>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-ink-muted">
            Paste a Glassdoor job listing URL. ApplyPilot validates it, stores
            the posting once and opens its workspace so you can review the match
            and start the application.
          </p>

          <form onSubmit={submit} className="mt-5 space-y-2.5">
            <label
              htmlFor="apply-job-url"
              className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-subtle"
            >
              <Link2 aria-hidden="true" className="h-3.5 w-3.5" />
              Glassdoor job listing URL
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <ClipboardPaste
                  aria-hidden="true"
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle"
                />
                <input
                  id="apply-job-url"
                  type="url"
                  inputMode="url"
                  autoComplete="off"
                  spellCheck={false}
                  value={url}
                  onChange={(event) => {
                    setUrl(event.target.value);
                    if (error) setError(null);
                  }}
                  disabled={busy}
                  placeholder="https://www.glassdoor.com/job-listing/..."
                  className="h-11 w-full rounded-xl border border-white/10 bg-navy-950/70 pl-9 pr-3 text-[13px] text-ink placeholder:text-ink-subtle/60 focus:border-brand-blue/50 focus:outline-none disabled:opacity-60"
                />
              </div>
              <Button
                type="submit"
                disabled={busy || !url.trim()}
                className="h-11 shrink-0"
              >
                {importJob.isPending ? (
                  <>
                    <Spinner />
                    Importing…
                  </>
                ) : (
                  <>
                    Import Job
                    <ArrowRight aria-hidden="true" className="h-4 w-4" />
                  </>
                )}
              </Button>
            </div>
          </form>

          {error ? (
            <Alert variant="danger" className="mt-3" title="Check the job URL">
              {error}
            </Alert>
          ) : null}

          {importJob.error ? (
            <Alert
              variant="danger"
              className="mt-3"
              title="This job link could not be imported"
            >
              {describeError(importJob.error)}
            </Alert>
          ) : null}

          {imported ? (
            <Alert
              variant="success"
              className="mt-3"
              title={imported.reused ? "Posting already in your library" : "Job imported"}
            >
              {imported.reused
                ? "The existing job record was reused, so nothing was duplicated. Opening its workspace…"
                : "The posting is stored. Opening its workspace…"}
              {imported.extraction.notes.length > 0 ? (
                <span className="mt-1 block text-ink-subtle">
                  {imported.extraction.notes[0]}
                </span>
              ) : null}
            </Alert>
          ) : null}

          <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-ink-subtle">
            <ShieldCheck
              aria-hidden="true"
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"
            />
            Only public Glassdoor listing links are accepted. The page is never
            scraped or auto-opened, and only the listing URL and job id are read.
          </p>
        </div>

        <div className="grid gap-2.5 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4 text-[12px] text-ink-muted">
          <p className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-subtle">
            <Building2 aria-hidden="true" className="h-3.5 w-3.5" />
            What happens next
          </p>
          <ol className="space-y-2">
            <li className="flex gap-2">
              <span className="text-sky-300">1</span>
              The listing is validated and stored once, even if you import it twice.
            </li>
            <li className="flex gap-2">
              <span className="text-sky-300">2</span>
              The job workspace shows the resume recommendation and match score.
            </li>
            <li className="flex gap-2">
              <span className="text-sky-300">3</span>
              You open the application, review every proposed answer, and submit it
              yourself.
            </li>
          </ol>
          <p className="border-t border-white/[0.06] pt-2.5 text-[11px] text-ink-subtle">
            ApplyPilot never clicks Easy Apply, solves CAPTCHA, enters OTP/MFA
            codes or submits the final form.
          </p>
        </div>
      </div>
    </section>
  );
}
