import { Badge } from "@/components/ui/badge";
import { ConfidenceBadge } from "@/components/intelligence/confidence-badge";
import type { MatchEvidence } from "@/types/api";

/**
 * Renders explainable match evidence. References point at resume sections
 * (for example "skills section"), never at storage paths or file contents.
 */
export function EvidenceList({
  evidence,
  title = "Supporting evidence",
  emptyMessage,
}: {
  evidence: MatchEvidence[];
  title?: string;
  emptyMessage?: string;
}) {
  if (evidence.length === 0) {
    if (!emptyMessage) return null;
    return (
      <div>
        <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
          {title}
        </p>
        <p className="mt-1.5 text-[12px] text-ink-subtle">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
        {title}
      </p>
      <ul className="mt-2 space-y-2">
        {evidence.map((item, index) => (
          <li
            key={`${item.category}-${item.description}-${index}`}
            className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2"
          >
            <div className="flex flex-wrap items-center gap-2">
              {item.category ? (
                <Badge variant="neutral">{item.category}</Badge>
              ) : null}
              <ConfidenceBadge confidence={item.confidence} />
              {item.resume_reference ? (
                <span className="text-[11px] text-ink-subtle">
                  {item.resume_reference}
                </span>
              ) : null}
            </div>
            <p className="mt-1.5 text-[12px] leading-relaxed text-ink-muted">
              {item.description}
            </p>
            {item.match_reason ? (
              <p className="mt-1 text-[11px] leading-relaxed text-ink-subtle">
                {item.match_reason}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
