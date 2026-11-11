"use client";

import {
  Award,
  Briefcase,
  FileSearch,
  FolderGit2,
  GraduationCap,
  RefreshCw,
  Save,
  Sparkles,
} from "lucide-react";
import { useState } from "react";

import { ConfidenceBadge } from "@/components/intelligence/confidence-badge";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useAnalyzeResume, useResumeIntelligence } from "@/features/resumes/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import { formatDateTime } from "@/lib/utils/format";
import type {
  ResumeAnalysisState,
  ResumeExperienceFact,
  ResumeIntelligenceFields,
  ResumeParsingStatus,
  ResumeSkillFact,
} from "@/types/api";

const STATE: Record<
  ResumeAnalysisState,
  { label: string; variant: "success" | "warning" | "neutral" | "danger" }
> = {
  ready: { label: "Analysis ready", variant: "success" },
  stale: { label: "Analysis out of date", variant: "warning" },
  missing: { label: "Not analyzed yet", variant: "neutral" },
  failed: { label: "Analysis failed", variant: "danger" },
};

const CATEGORY_LABEL: Record<string, string> = {
  language: "Languages",
  framework: "Frameworks",
  library: "Libraries",
  database: "Databases",
  cloud: "Cloud",
  tool: "Tools",
  ai_ml: "AI / ML",
  soft_skill: "Soft skills",
  other: "Other skills",
};

function monthsLabel(months: number | null): string | null {
  if (months === null || months <= 0) return null;
  const years = Math.floor(months / 12);
  const remaining = months % 12;
  const parts: string[] = [];
  if (years > 0) parts.push(`${years} yr${years === 1 ? "" : "s"}`);
  if (remaining > 0) parts.push(`${remaining} mo${remaining === 1 ? "" : "s"}`);
  return parts.join(" ");
}

function SectionTitle({
  icon: Icon,
  title,
  count,
}: {
  icon: typeof Award;
  title: string;
  count?: number;
}) {
  return (
    <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
      <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      {title}
      {count !== undefined ? (
        <span className="text-ink-subtle/70">({count})</span>
      ) : null}
    </p>
  );
}

function Evidence({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p className="mt-1 text-[11px] leading-relaxed text-ink-subtle">
      Evidence: “{text}”
    </p>
  );
}

function dateRange(
  start: string | null,
  end: string | null,
  isCurrent = false,
): string {
  if (isCurrent) return start ? `${start} – Present` : "Present";
  if (start && end) return `${start} – ${end}`;
  return start ?? end ?? "";
}

function Skills({ skills }: { skills: ResumeSkillFact[] }) {
  const [showEvidence, setShowEvidence] = useState(false);
  if (skills.length === 0) {
    return (
      <div>
        <SectionTitle icon={Sparkles} title="Skills" />
        <p className="mt-1.5 text-[12px] text-ink-subtle">
          The resume did not state any recognisable skills.
        </p>
      </div>
    );
  }

  const groups = skills.reduce<Record<string, ResumeSkillFact[]>>((acc, skill) => {
    const key = skill.category || "other";
    (acc[key] ??= []).push(skill);
    return acc;
  }, {});
  const withEvidence = skills.filter((skill) => skill.evidence);

  return (
    <div>
      <SectionTitle icon={Sparkles} title="Skills" count={skills.length} />
      <div className="mt-2 space-y-2">
        {Object.entries(groups).map(([category, items]) => (
          <div key={category}>
            <p className="text-[11px] text-ink-subtle">
              {CATEGORY_LABEL[category] ?? category}
            </p>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {items.map((skill) => (
                <Badge
                  key={`${skill.name}-${skill.category}`}
                  variant={skill.explicit ? "brand" : "neutral"}
                >
                  {skill.name}
                  {skill.explicit ? "" : " (implied)"}
                </Badge>
              ))}
            </div>
          </div>
        ))}
      </div>
      {withEvidence.length > 0 ? (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setShowEvidence((value) => !value)}
            aria-expanded={showEvidence}
            className="text-[12px] text-sky-300 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-blue/60"
          >
            {showEvidence ? "Hide skill evidence" : "Show skill evidence"}
          </button>
          {showEvidence ? (
            <ul className="mt-1.5 space-y-1.5">
              {withEvidence.map((skill) => (
                <li
                  key={`evidence-${skill.name}-${skill.category}`}
                  className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[12px] font-medium text-ink">
                      {skill.name}
                    </span>
                    <ConfidenceBadge confidence={skill.confidence} />
                  </div>
                  <Evidence text={skill.evidence} />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Experience({ roles }: { roles: ResumeExperienceFact[] }) {
  if (roles.length === 0) return null;
  return (
    <div>
      <SectionTitle icon={Briefcase} title="Experience" count={roles.length} />
      <ul className="mt-2 space-y-2">
        {roles.map((role, index) => (
          <li
            key={`${role.company ?? "role"}-${role.role ?? index}`}
            className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-[13px] font-medium text-ink">
                {[role.role, role.company].filter(Boolean).join(" · ") ||
                  "Unlabelled role"}
              </p>
              <p className="text-[11px] text-ink-subtle">
                {dateRange(role.start_date, role.end_date, role.is_current)}
                {role.duration_months ? ` · ${monthsLabel(role.duration_months)}` : ""}
              </p>
            </div>
            {role.location ? (
              <p className="mt-0.5 text-[11px] text-ink-subtle">{role.location}</p>
            ) : null}
            {role.responsibilities.length > 0 ? (
              <ul className="mt-1.5 space-y-1">
                {role.responsibilities.slice(0, 4).map((item) => (
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
            ) : null}
            {role.technologies.length > 0 ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {role.technologies.map((technology) => (
                  <Badge key={technology} variant="neutral">
                    {technology}
                  </Badge>
                ))}
              </div>
            ) : null}
            <Evidence text={role.evidence} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Projects({ fields }: { fields: ResumeIntelligenceFields }) {
  if (fields.projects.length === 0) return null;
  return (
    <div>
      <SectionTitle
        icon={FolderGit2}
        title="Projects"
        count={fields.projects.length}
      />
      <ul className="mt-2 space-y-2">
        {fields.projects.map((project, index) => (
          <li
            key={`${project.title}-${index}`}
            className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
          >
            <p className="text-[13px] font-medium text-ink">{project.title}</p>
            {project.description ? (
              <p className="mt-0.5 whitespace-pre-line text-[12px] leading-relaxed text-ink-muted">
                {project.description}
              </p>
            ) : null}
            {project.technologies.length > 0 ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {project.technologies.map((technology) => (
                  <Badge key={technology} variant="neutral">
                    {technology}
                  </Badge>
                ))}
              </div>
            ) : null}
            {project.measurable_outcomes.length > 0 ? (
              <ul className="mt-1.5 space-y-1">
                {project.measurable_outcomes.map((outcome) => (
                  <li key={outcome} className="text-[12px] text-ink-muted">
                    {outcome}
                  </li>
                ))}
              </ul>
            ) : null}
            <Evidence text={project.evidence} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Education({ fields }: { fields: ResumeIntelligenceFields }) {
  if (fields.education.length === 0) return null;
  return (
    <div>
      <SectionTitle
        icon={GraduationCap}
        title="Education"
        count={fields.education.length}
      />
      <ul className="mt-2 space-y-2">
        {fields.education.map((entry, index) => (
          <li
            key={`${entry.institution ?? "education"}-${index}`}
            className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-[13px] font-medium text-ink">
                {[entry.degree, entry.institution].filter(Boolean).join(" · ") ||
                  "Education entry"}
              </p>
              <p className="text-[11px] text-ink-subtle">
                {entry.graduation_year
                  ? `Graduated ${entry.graduation_year}`
                  : dateRange(entry.start_date, entry.end_date)}
              </p>
            </div>
            {entry.specialization ? (
              <p className="mt-0.5 text-[12px] text-ink-muted">
                {entry.specialization}
              </p>
            ) : null}
            <Evidence text={entry.evidence} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Certifications({ fields }: { fields: ResumeIntelligenceFields }) {
  if (fields.certifications.length === 0) return null;
  return (
    <div>
      <SectionTitle
        icon={Award}
        title="Certifications"
        count={fields.certifications.length}
      />
      <ul className="mt-2 space-y-1.5">
        {fields.certifications.map((certification, index) => (
          <li
            key={`${certification.name}-${index}`}
            className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-[12px] font-medium text-ink">
                {certification.name}
              </p>
              {certification.issued_on ? (
                <p className="text-[11px] text-ink-subtle">
                  {certification.issued_on}
                </p>
              ) : null}
            </div>
            {certification.issuing_organization ? (
              <p className="mt-0.5 text-[11px] text-ink-subtle">
                {certification.issuing_organization}
              </p>
            ) : null}
            <Evidence text={certification.evidence} />
          </li>
        ))}
      </ul>
    </div>
  );
}

type ActionKind = "analyze" | "save" | "refresh" | "retry";

function actionFor(state: ResumeAnalysisState): {
  kind: ActionKind;
  label: string;
  force: boolean;
} {
  switch (state) {
    case "ready":
      return { kind: "refresh", label: "Refresh analysis", force: true };
    case "stale":
      return { kind: "save", label: "Save updated analysis", force: false };
    case "failed":
      return { kind: "retry", label: "Retry analysis", force: true };
    default:
      return { kind: "analyze", label: "Analyze resume", force: false };
  }
}

export function ResumeIntelligencePanel({
  resumeId,
  parsingStatus,
}: {
  resumeId: string;
  parsingStatus: ResumeParsingStatus;
}) {
  const parseable =
    parsingStatus !== "pending" &&
    parsingStatus !== "processing" &&
    parsingStatus !== "ocr_required" &&
    parsingStatus !== "failed";
  const intelligence = useResumeIntelligence(resumeId, { enabled: parseable });
  const analyze = useAnalyzeResume(resumeId);
  const [showProvenance, setShowProvenance] = useState(false);

  if (!parseable) {
    return (
      <section className="space-y-2 rounded-xl border border-white/[0.07] bg-white/[0.015] p-4">
        <p className="text-[13px] font-medium text-ink">Resume intelligence</p>
        <Alert
          variant={parsingStatus === "ocr_required" ? "warning" : "info"}
          title={
            parsingStatus === "ocr_required"
              ? "OCR required before analysis"
              : parsingStatus === "failed"
                ? "This resume could not be parsed"
                : "Parsing in progress"
          }
        >
          {parsingStatus === "ocr_required"
            ? "This PDF has no selectable text, so no structured analysis was produced. Upload a text-based PDF or run OCR, then analyze it again. Nothing was inferred from the scan."
            : parsingStatus === "failed"
              ? "The stored file could not be parsed, so there is no resume evidence to analyze. Upload a readable PDF to enable intelligence."
              : "ApplyPilot is still reading this resume. Analysis becomes available once parsing finishes."}
        </Alert>
      </section>
    );
  }

  const data = intelligence.data;
  const extraction = data?.extraction;
  const fields = data?.fields;
  const action = extraction ? actionFor(extraction.analysis_state) : null;
  const state = extraction?.analysis_state ?? "missing";
  const status = STATE[state];
  const totalExperience = fields
    ? monthsLabel(fields.total_experience_months)
    : null;

  return (
    <section className="space-y-4 rounded-xl border border-white/[0.07] bg-white/[0.015] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <FileSearch aria-hidden="true" className="h-4 w-4 text-sky-300" />
          <p className="text-[13px] font-medium text-ink">Resume intelligence</p>
          {extraction ? <Badge variant={status.variant}>{status.label}</Badge> : null}
          {extraction ? <ConfidenceBadge confidence={extraction.confidence} /> : null}
        </div>
        {action ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => analyze.mutate({ force: action.force })}
            disabled={analyze.isPending || intelligence.isPending}
          >
            {analyze.isPending ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : action.kind === "save" ? (
              <Save aria-hidden="true" className="h-3.5 w-3.5" />
            ) : (
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
            )}
            {action.label}
          </Button>
        ) : null}
      </div>

      {analyze.error ? (
        <Alert variant="danger" title="Resume analysis failed">
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
          <Alert variant="danger" title="Could not load resume intelligence">
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
        <Alert variant="info" title="No analysis available">
          Analyze this resume to extract evidence. Only content the resume states
          is reported.
        </Alert>
      ) : state === "failed" ? (
        <>
          <Alert variant="danger" title="Analysis failed">
            {data.message ??
              "The stored resume content could not be analyzed. Nothing was inferred from the missing content."}
            {extraction.error_code ? (
              <span className="mt-1 block text-[11px] opacity-80">
                Reason code: {extraction.error_code}
              </span>
            ) : null}
          </Alert>
          {extraction.warnings.length > 0 ? (
            <ul className="space-y-1 text-[12px] text-amber-200">
              {extraction.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <>
          {state !== "ready" ? (
            <Alert variant="info" title="This reading is not saved">
              {data.message ??
                "This analysis was computed for this view only. Save it to reuse the reading in matching and comparisons."}
            </Alert>
          ) : data.message ? (
            <p className="text-[11px] leading-relaxed text-ink-subtle">
              {data.message}
            </p>
          ) : null}

          {extraction.warnings.length > 0 ? (
            <Alert variant="warning" title="Extraction warnings">
              <ul className="space-y-1">
                {extraction.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </Alert>
          ) : null}

          {fields.summary ? (
            <div>
              <SectionTitle icon={FileSearch} title="Summary" />
              <p className="mt-1.5 text-[13px] leading-relaxed text-ink-muted">
                {fields.summary}
              </p>
            </div>
          ) : null}

          {totalExperience || fields.has_dated_experience ? (
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-muted">
              <Badge variant="neutral">
                {totalExperience
                  ? `${totalExperience} of dated experience`
                  : "Dated experience available"}
              </Badge>
              {fields.sections_found.length > 0 ? (
                <span className="text-[11px] text-ink-subtle">
                  Sections found: {fields.sections_found.join(", ")}
                </span>
              ) : null}
            </div>
          ) : null}

          <Skills skills={fields.skills} />
          <Experience roles={fields.experience} />
          <Projects fields={fields} />
          <Education fields={fields} />
          <Certifications fields={fields} />

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
                {Object.entries(extraction.field_confidence).length === 0 ? (
                  <p className="text-[12px] text-ink-subtle">
                    No field-level provenance was recorded.
                  </p>
                ) : (
                  Object.entries(extraction.field_confidence).map(
                    ([field, confidence]) => (
                      <div
                        key={field}
                        className="flex flex-wrap items-baseline justify-between gap-2 text-[12px]"
                      >
                        <span className="text-ink-muted">
                          {field.replace(/_/g, " ")}
                        </span>
                        <span className="flex items-center gap-2">
                          {extraction.provenance[field] ? (
                            <Badge variant="neutral">
                              {extraction.provenance[field].replace(/_/g, " ")}
                            </Badge>
                          ) : null}
                          <span className="text-[11px] text-ink-subtle">
                            {confidence}
                          </span>
                        </span>
                      </div>
                    ),
                  )
                )}
                <p className="pt-1 text-[11px] text-ink-subtle">
                  Extractor {extraction.extractor_version} ·{" "}
                  {extraction.cached ? "saved" : "computed for this view"} ·{" "}
                  {formatDateTime(extraction.computed_at)}
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
