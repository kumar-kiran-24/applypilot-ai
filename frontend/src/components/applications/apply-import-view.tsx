"use client";

import { ArrowRight, ClipboardPaste, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useImportJob } from "@/features/jobs/hooks";
import { validateJobImportUrl } from "@/features/jobs/import-url";
import { describeError } from "@/lib/api/errors";

/**
 * `/apply?url=...`: validates and imports the URL once, then opens the job
 * workspace. A failed import stays here with the exact reason and an editable
 * field, so the user never loses the link they pasted.
 */
export function ApplyImportView({ initialUrl }: { initialUrl: string | null }) {
  const router = useRouter();
  const importJob = useImportJob();
  const [url, setUrl] = useState(initialUrl ?? "");
  const [error, setError] = useState<string | null>(
    initialUrl ? null : "No job URL was provided.",
  );
  const started = useRef(false);
  const importRef = useRef(importJob.mutateAsync);
  importRef.current = importJob.mutateAsync;
  const routerRef = useRef(router);
  routerRef.current = router;

  useEffect(() => {
    if (started.current || !initialUrl) return;
    started.current = true;
    const validation = validateJobImportUrl(initialUrl);
    if (!validation.ok) {
      setError(validation.error);
      return;
    }
    importRef
      .current(validation.url)
      .then((result) => {
        routerRef.current.replace(`/jobs/${result.job.id}`);
      })
      .catch(() => {
        // The mutation error is displayed below with the backend's reason.
      });
  }, [initialUrl]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    const validation = validateJobImportUrl(url);
    if (!validation.ok) {
      setError(validation.error);
      return;
    }
    try {
      const result = await importJob.mutateAsync(validation.url);
      router.push(`/jobs/${result.job.id}`);
    } catch {
      // Rendered below.
    }
  };

  return (
    <div className="glass-panel w-full max-w-xl p-6 sm:p-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-sky-300/80">
        Apply to a job
      </p>
      <h1 className="mt-2 text-xl font-semibold tracking-tight text-ink">
        Importing your job listing
      </h1>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
        The link is validated on the server, stored once and opened in its
        workspace. Glassdoor is not scraped and its page is not fetched.
      </p>

      <form onSubmit={submit} className="mt-5 space-y-2.5">
        <label
          htmlFor="apply-import-url"
          className="block text-[12px] font-medium text-ink"
        >
          Glassdoor job listing URL
        </label>
        <div className="relative">
          <ClipboardPaste
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle"
          />
          <input
            id="apply-import-url"
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            value={url}
            onChange={(event) => {
              setUrl(event.target.value);
              if (error) setError(null);
            }}
            disabled={importJob.isPending}
            className="h-11 w-full rounded-xl border border-white/10 bg-navy-950/70 pl-9 pr-3 text-[13px] text-ink placeholder:text-ink-subtle/60 focus:border-brand-blue/50 focus:outline-none disabled:opacity-60"
            placeholder="https://www.glassdoor.com/job-listing/..."
          />
        </div>
        <Button
          type="submit"
          disabled={importJob.isPending || !url.trim()}
          className="h-11 w-full"
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
      </form>

      {error ? (
        <Alert variant="danger" className="mt-4" title="Check the job URL">
          {error}
        </Alert>
      ) : null}

      {importJob.error ? (
        <Alert
          variant="danger"
          className="mt-4"
          title="This job link could not be imported"
        >
          {describeError(importJob.error)}
        </Alert>
      ) : null}

      {importJob.isSuccess ? (
        <Alert variant="success" className="mt-4" title="Job imported">
          {importJob.data.reused
            ? "This posting was already stored, so its existing record was reused."
            : "The posting is stored."}{" "}
          Opening the workspace…
        </Alert>
      ) : null}

      <p className="mt-4 flex items-start gap-1.5 text-[11px] leading-relaxed text-ink-subtle">
        <ShieldCheck
          aria-hidden="true"
          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"
        />
        Only public Glassdoor listing URLs are accepted. Private, internal and
        look-alike hosts are refused before anything is stored.
      </p>
    </div>
  );
}
