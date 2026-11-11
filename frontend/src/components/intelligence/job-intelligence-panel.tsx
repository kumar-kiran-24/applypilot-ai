"use client";

import { FileSearch, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";

import { ConfidenceBadge } from "@/components/intelligence/confidence-badge";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useAnalyzeJob, useJobIntelligence } from "@/features/jobs/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDateTime } from "@/lib/utils/format";
import type { JobIntelligenceFields } from "@/types/api";

const EMPLOYMENT_TYPE: Record<string, string> = {
  full_time: "Full time",
  part_time: "Part time",
  contract: "Contract",
  internship: "Internship",
  freelance: "Freelance",
  temporary: "Temporary",
};

const WORK_MODE: Record<string, string> = {
  onsite: "On-site",
  hybrid: "Hybrid",
  remote: "Remote",
};

const EXPERIENCE_LEVEL: Record<string, string> = {
  intern: "Intern",
  junior: "Junior",
  mid: "Mid-level",
  senior: "Senior",
  lead: "Lead",
  principal: "Principal",
  director: "Director",
};

/** Field label -> stored value, used for both rendering and "not stated". */
function fieldValue(
  fields: JobIntelligenceFields,
  key: string,
): string | null {
  switch (key) {
    case "company":
      return fields.company;
    case "work_location":
      return fields.work_location;
    case "experience_years":
      if (fields.experience_years === null && fields.experience_years_max === null) {
        return null;
      }
      if (fields.experience_years !== null && fields.experience_years_max !== null) {
        return `${fields.experience_years}–${fields.experience_years_max} years`;
      }
      return `${fields.experience_years ?? fields.experience_years_max}+ years`;
    case "education_requirement":
      return fields.education_requirement;
    case "employment_type":
      return fields.employment_type
        ? (EMPLOYMENT_TYPE[fields.employment_type] ?? fields.employment_type)
        : null;
    case "work_mode":
      return fields.work_mode
        ? (WORK_MODE[fields.work_mode] ?? fields.work_mode)
        : null;
    case "experience_level":
      return fields.experience_level && fields.experience_level !== "unknown"
        ? (EXPERIENCE_LEVEL[fields.experience_level] ?? fields.experience_level)
        : null;
    case "salary_range":
      return fields.salary_range;
    case "application_url":
      return fields.application_url;
    default:
      return null;
  }
}

const OVERVIEW_FIELDS = [
  "company",
  "work_location",
  "experience_level",
  "experience_years",
  "education_requirement",
  "employment_type",
  "work_mode",
  "salary_range",
] as const;

function FieldGrid({ fields }: { fields: JobIntelligenceFields }) {
  const present = OVERVIEW_FIELDS.map((key) => ({
    key,
    label: key.replace(/_/g, " "),
    value: fieldValue(fields, key),
  })).filter((item) => item.value);

  if (present.length === 0) {
    return null;
  }

  return (
    <dl className="grid gap-3 sm:grid-cols-2">
      {present.map((item) => (
        <div key={item.key}>
          <dt className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
            {item.label}
          </dt>
          <dd className="mt-1 text-[13px] text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function SkillGroup({
  title,
  skills,
  variant,
}: {
  title: string;
  skills: string[];
  variant: "brand" | "neutral" | "danger";
}) {
  if (skills.length === 0) return null;
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        {title}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {skills.map((skill) => (
          <Badge key={skill} variant={variant}>
            {skill}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function BulletList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        {title}
      </p>
      <ul className="mt-1.5 space-y-1">
        {items.map((item) => (
          <li
            key={item}
            className="flex gap-2 text-[12px] leading-relaxed text-ink-muted"
          >
            <span aria-hidden="true" className="text-ink-subtle">
              •
            </span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function JobIntelligencePanel({ jobId }: { jobId: string }) {
  const intelligence = useJobIntelligence(jobId);
  const analyze = useAnalyzeJob(jobId);
  const [showProvenance, setShowProvenance] = useState(false);

  const data = intelligence.data;
  const extraction = data?.extraction;
  const fields = data?.fields;
  const missing = fields
    ? OVERVIEW_FIELDS.filter((key) => !fieldValue(fields, key)).map((key) =>
        key.replace(/_/g, " "),
      )
    : [];

  return (
    <section className="space-y-4 rounded-xl border border-white/[0.07] bg-white/[0.015] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FileSearch aria-hidden="true" className="h-4 w-4 text-sky-300" />
          <p className="text-[13px] font-medium text-ink">Job intelligence</p>
          {extraction ? (
            <ConfidenceBadge confidence={extraction.confidence} />
          ) : null}
          {extraction ? (
            <Badge variant={extraction.cached ? "success" : "neutral"}>
              {extraction.cached ? "Saved analysis" : "Not saved yet"}
            </Badge>
          ) : null}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => analyze.mutate({ force: Boolean(extraction?.cached) })}
          disabled={analyze.isPending || intelligence.isPending}
        >
          {analyze.isPending ? (
            <Spinner className="h-3.5 w-3.5" />
          ) : (
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
          )}
          {extraction?.cached ? "Refresh analysis" : "Analyze and save"}
        </Button>
      </div>

      {analyze.error ? (
        <Alert variant="danger" title="Job analysis failed">
          {describeError(analyze.error)}
        </Alert>
      ) : null}

      {intelligence.isPending ? (
        <div className="space-y-3" aria-hidden="true">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : intelligence.isError ? (
        <>
          <Alert variant="danger" title="Could not load job intelligence">
            {describeError(intelligence.error)}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void intelligence.refetch()}
          >
            <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            Retry
          </Button>
        </>
      ) : !fields || !extraction ? (
        <EmptyState
          icon={Sparkles}
          title="No structured reading yet"
          description="Analyze the posting to extract requirements, qualifications and provenance. Nothing is inferred from text the posting does not contain."
          action={
            <Button
              size="sm"
              onClick={() => analyze.mutate({ force: false })}
              disabled={analyze.isPending}
            >
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
              Analyze posting
            </Button>
          }
        />
      ) : (
        <>
          {data.message ? (
            <p className="text-[11px] leading-relaxed text-ink-subtle">
              {data.message}
            </p>
          ) : null}

          {extraction.contradictions.length > 0 ? (
            <Alert variant="warning" title="The posting contradicts itself">
              <ul className="space-y-1">
                {extraction.contradictions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Alert>
          ) : null}

          {extraction.warnings.length > 0 ? (
            <Alert variant="warning" title="Extraction warnings">
              <ul className="space-y-1">
                {extraction.warnings.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Alert>
          ) : null}

          <FieldGrid fields={fields} />

          <div className="grid gap-4 sm:grid-cols-2">
            <SkillGroup
              title="Required skills"
              skills={fields.required_skills}
              variant="brand"
            />
            <SkillGroup
              title="Preferred skills"
              skills={fields.preferred_skills}
              variant="neutral"
            />
            <SkillGroup
              title="Certifications requested"
              skills={fields.required_certifications}
              variant="neutral"
            />
          </div>

          {fields.required_skills.length === 0 &&
          fields.preferred_skills.length === 0 ? (
            <Alert variant="info" title="No skills stated">
              The posting does not list required or preferred skills. Treat this
              as missing information, not as a job without requirements.
            </Alert>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <BulletList
              title="Qualifications"
              items={fields.required_qualifications}
            />
            <BulletList
              title="Responsibilities"
              items={fields.responsibilities}
            />
          </div>

          {missing.length > 0 ? (
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
              <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                Not stated in the posting
              </p>
              <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">
                {missing.join(", ")}. These were not mentioned, so they are
                unknown rather than confirmed requirements.
              </p>
            </div>
          ) : null}

          <div>
            <button
              type="button"
              onClick={() => setShowProvenance((value) => !value)}
              aria-expanded={showProvenance}
              className="text-[12px] text-sky-300 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-blue/60"
            >
              {showProvenance ? "Hide provenance" : "Show provenance"}
            </button>
            {showProvenance ? (
              <div className="mt-2 space-y-1.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
                {Object.entries(extraction.provenance).length === 0 ? (
                  <p className="text-[12px] text-ink-subtle">
                    No field-level provenance was recorded.
                  </p>
                ) : (
                  Object.entries(extraction.provenance).map(([field, source]) => (
                    <div
                      key={field}
                      className="flex flex-wrap items-baseline justify-between gap-2 text-[12px]"
                    >
                      <span className="text-ink-muted">
                        {field.replace(/_/g, " ")}
                      </span>
                      <span className="flex items-center gap-2">
                        <Badge variant="neutral">{source.replace(/_/g, " ")}</Badge>
                        {extraction.field_confidence[field] ? (
                          <span className="text-[11px] text-ink-subtle">
                            {extraction.field_confidence[field]}
                          </span>
                        ) : null}
                      </span>
                    </div>
                  ))
                )}
                <p className="pt-1 text-[11px] text-ink-subtle">
                  Extractor {extraction.extractor_version} ·{" "}
                  {extraction.cached ? "saved" : "computed for this view"} ·{" "}
                  {formatDateTime(extraction.computed_at)}
                  {extraction.llm_used ? " · model-assisted" : ""}
                </p>
              </div>
            ) : null}
          </div>

          <p className={cn("text-[11px] leading-relaxed text-ink-subtle")}>
            {data.disclaimer}
          </p>
        </>
      )}
    </section>
  );
}
