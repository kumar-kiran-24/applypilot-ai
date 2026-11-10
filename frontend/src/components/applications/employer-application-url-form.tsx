"use client";

import { ArrowUpRight, CheckCircle2, Lightbulb, RefreshCw } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useInspectApplication } from "@/features/applications/hooks";
import { describeError } from "@/lib/api/errors";
import { safeExternalUrl } from "@/lib/utils/url";
import type { ApplicationPreview } from "@/types/api";

const inputClass =
  "h-10 w-full rounded-xl border border-white/10 bg-navy-950/60 px-3 text-[13px] text-ink placeholder:text-ink-subtle/60 focus:border-brand-blue/50 focus:outline-none";

/**
 * Three URLs are deliberately kept distinct:
 * - the job listing the posting was discovered from (reference only),
 * - the employer's careers page (reference only),
 * - the employer's actual application form (what the browser opens).
 *
 * Only the last one is submitted to the validating update endpoint.
 */
export function EmployerApplicationUrlForm({
  applicationId,
  initialApplicationUrl,
  jobListingUrl,
  employerCareersUrl,
  suggestedApplicationUrl,
  isJobBoard,
  disabled = false,
  submitLabel = "Save employer URL",
  onSaved,
}: {
  applicationId: string;
  initialApplicationUrl: string;
  jobListingUrl: string | null;
  employerCareersUrl: string | null;
  suggestedApplicationUrl?: string | null;
  isJobBoard: boolean;
  disabled?: boolean;
  submitLabel?: string;
  onSaved?: (application: ApplicationPreview) => void;
}) {
  const inspect = useInspectApplication(applicationId);
  // Never pre-fill the form with the job-board link: that is exactly the URL
  // the user must replace, and leaving it looks like a valid application form.
  const [applicationUrl, setApplicationUrl] = useState(
    isJobBoard ? "" : initialApplicationUrl,
  );
  const [careersUrl, setCareersUrl] = useState(employerCareersUrl ?? "");

  useEffect(() => {
    setApplicationUrl(isJobBoard ? "" : initialApplicationUrl);
  }, [initialApplicationUrl, isJobBoard]);

  useEffect(() => {
    setCareersUrl(employerCareersUrl ?? "");
  }, [employerCareersUrl]);

  const listingLink = safeExternalUrl(jobListingUrl);
  const saved = inspect.isSuccess && !inspect.isPending;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = applicationUrl.trim();
    if (!trimmed) return;
    try {
      const updated = await inspect.mutateAsync({
        applicationUrl: trimmed,
        employerCareersUrl: careersUrl.trim() || null,
      });
      setApplicationUrl(updated.application_url);
      setCareersUrl(updated.employer_careers_url ?? "");
      onSaved?.(updated);
    } catch {
      // The mutation error is rendered below with the backend's exact reason.
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      {isJobBoard ? (
        <Alert variant="warning" title="Add the employer's application URL">
          <p>
            The saved link is a job listing. ApplyPilot opens the employer&apos;s
            own form, so paste the application URL from the company&apos;s site
            or careers portal below. Job boards are never scraped or opened by
            the browser.
          </p>
        </Alert>
      ) : null}

      {listingLink ? (
        <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
          <p className="text-[11px] uppercase tracking-[0.1em] text-ink-subtle">
            Job listing URL (reference)
          </p>
          <a
            href={listingLink}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-flex items-center gap-1.5 break-all text-[12px] text-sky-300 hover:underline"
          >
            {listingLink}
            <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          </a>
          <p className="mt-1 text-[11px] text-ink-subtle">
            Preserved for reference. It is not used to open a browser session.
          </p>
        </div>
      ) : null}

      <div className="space-y-1.5">
        <label
          htmlFor={`employer-careers-${applicationId}`}
          className="block text-[12px] font-medium text-ink"
        >
          Employer careers URL (optional)
        </label>
        <input
          id={`employer-careers-${applicationId}`}
          value={careersUrl}
          onChange={(event) => setCareersUrl(event.target.value)}
          className={inputClass}
          disabled={disabled || inspect.isPending}
          placeholder="https://company.com/careers"
        />
        <p className="text-[11px] text-ink-subtle">
          The employer&apos;s careers site. Reference only; never opened
          automatically.
        </p>
      </div>

      <div className="space-y-1.5">
        <label
          htmlFor={`employer-application-${applicationId}`}
          className="block text-[12px] font-medium text-ink"
        >
          Application form URL
        </label>
        <input
          id={`employer-application-${applicationId}`}
          value={applicationUrl}
          onChange={(event) => setApplicationUrl(event.target.value)}
          className={inputClass}
          disabled={disabled || inspect.isPending}
          aria-label="Employer application URL"
          placeholder="https://boards.greenhouse.io/company/jobs/123"
        />
        <p className="text-[11px] text-ink-subtle">
          The employer&apos;s own application form. It is validated against the
          public-site policy before the browser may open it.
        </p>
      </div>

      {suggestedApplicationUrl ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-sky-400/20 bg-sky-500/[0.06] p-3">
          <Lightbulb
            aria-hidden="true"
            className="h-3.5 w-3.5 shrink-0 text-sky-300"
          />
          <div className="min-w-0 flex-1">
            <p className="text-[11px] text-ink-subtle">
              Found in the saved listing text. Review it before using:
            </p>
            <p className="break-all text-[12px] text-ink-muted">
              {suggestedApplicationUrl}
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={disabled || inspect.isPending}
            onClick={() => setApplicationUrl(suggestedApplicationUrl)}
          >
            Use suggestion
          </Button>
        </div>
      ) : null}

      {inspect.error ? (
        <Alert variant="danger" title="The employer URL was not accepted">
          {describeError(inspect.error)}
        </Alert>
      ) : null}

      {saved ? (
        <p className="flex items-center gap-1.5 text-[11px] text-emerald-200/90">
          <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
          Employer URL saved. The application data has been refreshed.
        </p>
      ) : null}

      {!disabled ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="submit"
            variant="secondary"
            disabled={!applicationUrl.trim() || inspect.isPending}
          >
            {inspect.isPending ? (
              <>
                <Spinner className="h-4 w-4" />
                Validating and inspecting…
              </>
            ) : (
              <>
                <RefreshCw aria-hidden="true" className="h-4 w-4" />
                {submitLabel}
              </>
            )}
          </Button>
          <span className="text-[11px] text-ink-subtle">
            Never submits, signs in or bypasses CAPTCHA/OTP/MFA.
          </span>
        </div>
      ) : null}
    </form>
  );
}
