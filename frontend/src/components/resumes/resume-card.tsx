"use client";

import {
  Award,
  Briefcase,
  ChevronDown,
  Download,
  Eye,
  FileText,
  GraduationCap,
  Mail,
  Phone,
  Star,
  Trash2,
  User,
} from "lucide-react";
import { useState } from "react";

import { ResumeIntelligencePanel } from "@/components/intelligence/resume-intelligence-panel";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { resumeFileUrl } from "@/features/resumes/api";
import { cn } from "@/lib/utils/cn";
import { formatDate, formatFileSize } from "@/lib/utils/format";
import type {
  Resume,
  ResumeMatchStat,
  ResumeParsingStatus,
} from "@/types/api";

const STATUS: Record<
  ResumeParsingStatus,
  { label: string; variant: "neutral" | "success" | "warning" | "danger" }
> = {
  pending: { label: "Queued", variant: "neutral" },
  processing: { label: "Parsing…", variant: "neutral" },
  completed: { label: "Parsed", variant: "success" },
  ocr_required: { label: "OCR required", variant: "warning" },
  failed: { label: "Parsing failed", variant: "danger" },
};

function dateRange(
  start: string | null,
  end: string | null,
  isCurrent = false,
): string {
  if (isCurrent) {
    return start ? `${start} – Present` : "Present";
  }
  if (start && end) {
    return `${start} – ${end}`;
  }
  return start ?? end ?? "";
}

function DetailSection({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Award;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        <Icon aria-hidden="true" className="h-3.5 w-3.5" />
        {title}
      </p>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

export function ResumeCard({
  resume,
  matchStat,
  onSetDefault,
  onDelete,
  isSettingDefault,
  isDeleting,
}: {
  resume: Resume;
  matchStat?: ResumeMatchStat;
  onSetDefault: (id: string) => void;
  onDelete: (id: string) => void;
  isSettingDefault: boolean;
  isDeleting: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const data = resume.parsed_data;
  const status = STATUS[resume.parsing_status];
  const hasDetails =
    Boolean(data.full_name || data.email || data.phone) ||
    Boolean(data.professional_summary) ||
    resume.skills.length > 0 ||
    data.education.length > 0 ||
    data.experience.length > 0 ||
    data.projects.length > 0 ||
    data.certifications.length > 0;

  return (
    <Card
      className={cn(
        "overflow-hidden",
        resume.is_default && "border-brand-blue/30",
      )}
    >
      <div className="flex flex-wrap items-start gap-3 border-b border-white/[0.06] px-5 py-4 sm:px-6">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-sky-300">
          <FileText aria-hidden="true" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-[13px] font-semibold text-ink">
              {resume.original_filename}
            </p>
            {resume.is_default ? (
              <Badge variant="brand">
                <Star aria-hidden="true" className="h-3 w-3" />
                Default
              </Badge>
            ) : null}
            {matchStat && matchStat.recommended_jobs > 0 ? (
              <Badge variant="success">
                Recommended for {matchStat.recommended_jobs} job
                {matchStat.recommended_jobs === 1 ? "" : "s"}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-ink-subtle">
            {formatDate(resume.uploaded_at)} ·{" "}
            {formatFileSize(resume.file_size)}
            {matchStat && matchStat.matches_count > 0
              ? ` · ${matchStat.average_score ?? 0}% average match across ${matchStat.matches_count} job${matchStat.matches_count === 1 ? "" : "s"}`
              : ""}
          </p>
        </div>
        <Badge variant={status.variant}>
          <span
            aria-hidden="true"
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              resume.parsing_status === "completed" && "bg-emerald-400",
              resume.parsing_status === "ocr_required" && "bg-amber-400",
              resume.parsing_status === "failed" && "bg-red-400",
              (resume.parsing_status === "pending" ||
                resume.parsing_status === "processing") &&
                "bg-slate-400",
            )}
          />
          {status.label}
        </Badge>
      </div>

      <CardContent className="space-y-4">
        {resume.parsing_status === "ocr_required" ? (
          <Alert variant="warning" title="OCR required">
            This PDF looks like a scan without selectable text. ApplyPilot kept
            the file but could not extract its content. Upload a text-based PDF
            or run OCR on the scan, then upload it again.
          </Alert>
        ) : null}

        {resume.parsing_status === "failed" ? (
          <Alert variant="danger" title="Parsing failed">
            The parser could not read this PDF. It is still stored and
            downloadable, but no candidate information was extracted.
          </Alert>
        ) : null}

        {resume.parsing_status === "pending" ||
        resume.parsing_status === "processing" ? (
          <Alert variant="info" title="Parsing in progress">
            ApplyPilot is reading this resume. Refresh in a moment to see the
            extracted details.
          </Alert>
        ) : null}

        <ResumeIntelligencePanel
          resumeId={resume.id}
          parsingStatus={resume.parsing_status}
        />

        {hasDetails || resume.parsing_status === "completed" ? (
          <div>
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
            >
              <ChevronDown
                aria-hidden="true"
                className={cn(
                  "h-3.5 w-3.5 transition-transform",
                  expanded && "rotate-180",
                )}
              />
              {expanded ? "Hide extracted details" : "Show extracted details"}
            </Button>
          </div>
        ) : null}

        {expanded ? (
          <div className="space-y-5 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
            {hasDetails ? (
              <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                      <User aria-hidden="true" className="h-3.5 w-3.5" />
                      Name
                    </p>
                    <p className="mt-1 truncate text-[13px] text-ink">
                      {data.full_name ?? "Not found"}
                    </p>
                  </div>
                  <div>
                    <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                      <Mail aria-hidden="true" className="h-3.5 w-3.5" />
                      Email
                    </p>
                    <p className="mt-1 truncate text-[13px] text-ink">
                      {data.email ?? "Not found"}
                    </p>
                  </div>
                  <div>
                    <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
                      <Phone aria-hidden="true" className="h-3.5 w-3.5" />
                      Phone
                    </p>
                    <p className="mt-1 truncate text-[13px] text-ink">
                      {data.phone ?? "Not found"}
                    </p>
                  </div>
                </div>

                {data.professional_summary ? (
                  <DetailSection icon={FileText} title="Professional summary">
                    <p className="text-[13px] leading-relaxed text-ink-muted">
                      {data.professional_summary}
                    </p>
                  </DetailSection>
                ) : null}

                <DetailSection icon={Star} title="Skills">
                  {resume.skills.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {resume.skills.map((skill) => (
                        <Badge key={skill} variant="brand">
                          {skill}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[12px] text-ink-subtle">
                      No skills detected.
                    </p>
                  )}
                </DetailSection>

                {data.experience.length > 0 ? (
                  <DetailSection icon={Briefcase} title="Experience">
                    {data.experience.map((entry, index) => (
                      <div
                        key={`${entry.title ?? "role"}-${index}`}
                        className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                      >
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <p className="text-[13px] font-medium text-ink">
                            {[entry.title, entry.company]
                              .filter(Boolean)
                              .join(" · ") || "Unlabelled role"}
                          </p>
                          <p className="text-[11px] text-ink-subtle">
                            {dateRange(
                              entry.start_date,
                              entry.end_date,
                              entry.is_current,
                            )}
                          </p>
                        </div>
                        {entry.location ? (
                          <p className="mt-0.5 text-[11px] text-ink-subtle">
                            {entry.location}
                          </p>
                        ) : null}
                        {entry.description ? (
                          <p className="mt-1 whitespace-pre-line text-[12px] leading-relaxed text-ink-muted">
                            {entry.description}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </DetailSection>
                ) : null}

                {data.education.length > 0 ? (
                  <DetailSection icon={GraduationCap} title="Education">
                    {data.education.map((entry, index) => (
                      <div
                        key={`${entry.institution ?? "education"}-${index}`}
                        className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                      >
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <p className="text-[13px] font-medium text-ink">
                            {[entry.degree, entry.institution]
                              .filter(Boolean)
                              .join(" · ") || "Education entry"}
                          </p>
                          <p className="text-[11px] text-ink-subtle">
                            {dateRange(entry.start_date, entry.end_date)}
                          </p>
                        </div>
                        {entry.details ? (
                          <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">
                            {entry.details}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </DetailSection>
                ) : null}

                {data.projects.length > 0 ? (
                  <DetailSection icon={FileText} title="Projects">
                    {data.projects.map((project, index) => (
                      <div
                        key={`${project.name}-${index}`}
                        className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                      >
                        <p className="text-[13px] font-medium text-ink">
                          {project.name}
                        </p>
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
                        {project.url ? (
                          <a
                            href={project.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="mt-1 inline-block text-[12px] text-sky-300 hover:underline"
                          >
                            {project.url}
                          </a>
                        ) : null}
                      </div>
                    ))}
                  </DetailSection>
                ) : null}

                {data.certifications.length > 0 ? (
                  <DetailSection icon={Award} title="Certifications">
                    <ul className="space-y-1 text-[12px] text-ink-muted">
                      {data.certifications.map((certification) => (
                        <li key={certification} className="flex gap-2">
                          <span aria-hidden="true" className="text-ink-subtle">
                            •
                          </span>
                          {certification}
                        </li>
                      ))}
                    </ul>
                  </DetailSection>
                ) : null}
              </>
            ) : (
              <p className="text-[12px] leading-relaxed text-ink-subtle">
                No structured details were extracted from this PDF. The original
                file is unchanged and available to download.
              </p>
            )}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <a
            href={resumeFileUrl(resume.id, "attachment")}
            className="inline-flex h-9 items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm font-medium text-ink transition-colors hover:border-white/20 hover:bg-white/[0.08]"
          >
            <Download aria-hidden="true" className="h-3.5 w-3.5" />
            Download
          </a>
          <a
            href={resumeFileUrl(resume.id, "inline")}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-9 items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm font-medium text-ink transition-colors hover:border-white/20 hover:bg-white/[0.08]"
          >
            <Eye aria-hidden="true" className="h-3.5 w-3.5" />
            Preview
          </a>
          {resume.is_default ? null : (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onSetDefault(resume.id)}
              disabled={isSettingDefault}
            >
              {isSettingDefault ? (
                <>
                  <Spinner className="h-3.5 w-3.5" />
                  Setting…
                </>
              ) : (
                <>
                  <Star aria-hidden="true" className="h-3.5 w-3.5" />
                  Set default
                </>
              )}
            </Button>
          )}
          <Button
            variant="danger"
            size="sm"
            onClick={() => setConfirming((value) => !value)}
            disabled={isDeleting}
            className="ml-auto"
          >
            <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
            Delete
          </Button>
        </div>

        {confirming ? (
          <div className="rounded-xl border border-red-500/25 bg-red-500/[0.06] p-4">
            <p className="text-[13px] font-medium text-red-200">
              Delete “{resume.original_filename}”?
            </p>
            <p className="mt-1 text-[12px] leading-relaxed text-red-200/80">
              The PDF and its extracted details are removed permanently.
              {resume.is_default
                ? " Your most recent remaining resume becomes the default."
                : ""}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                variant="danger"
                size="sm"
                onClick={() => onDelete(resume.id)}
                disabled={isDeleting}
              >
                {isDeleting ? (
                  <>
                    <Spinner className="h-3.5 w-3.5" />
                    Deleting…
                  </>
                ) : (
                  <>
                    <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                    Yes, delete
                  </>
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirming(false)}
                disabled={isDeleting}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
