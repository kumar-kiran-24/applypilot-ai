"""Phase 2F.2.4 recommendation across already-computed resume comparisons.

The recommendation reads the persisted match rows produced by the Phase 2F.2.3
engine; it never re-scores a resume and never invents evidence. It answers one
narrow question: *of the resumes on file, which one best matches this posting,
and how confident can that comparison be?*

Close scores are handled explicitly. When the top two qualifying resumes are
within ``close_score_threshold`` percentage points, the result is marked as a
close call, both resumes are described as viable, and the dimensions that
distinguish them are named. When no resume has usable evidence, no
recommendation is returned at all.

Ranking order is decided by :class:`ResumeComparisonService`; this service
preserves the order it receives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.services.advanced_matching_engine import (
    STATE_INSUFFICIENT,
    STATE_PARTIAL,
    STATE_RESUME_FAILED,
)
from app.services.matching_engine import SCORE_DISCLAIMER

if TYPE_CHECKING:  # pragma: no cover - import cycle avoided for typing only
    from app.services.resume_comparison_service import ComparisonEntry

#: Absolute overall-score difference (percentage points) at or below which two
#: resumes are considered equally viable. Configurable on the service.
DEFAULT_CLOSE_SCORE_THRESHOLD = 3.0

#: A dimension difference below this many points is not called a distinction.
DISTINGUISHING_DIMENSION_DELTA = 5.0

#: Match states where the engine found no usable resume evidence.
_NO_EVIDENCE_MATCH_STATES = frozenset({STATE_INSUFFICIENT, STATE_RESUME_FAILED})

MAX_RECOMMENDATION_REASONS = 5
MAX_EVIDENCE = 6
MAX_GAPS = 6
MAX_ALTERNATIVES = 3

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _confidence_rank(value: str | None) -> int:
    return _CONFIDENCE_RANK.get((value or "").lower(), 0)


def _details(entry: ComparisonEntry) -> dict[str, Any]:
    if entry.row is None or not isinstance(entry.row.details, dict):
        return {}
    return entry.row.details


def _is_suitable(entry: ComparisonEntry) -> bool:
    """Whether a ranked resume carries enough evidence for a recommendation."""
    if entry.row is None:
        return False
    score = entry.overall_score
    if score is None or score <= 0:
        return False
    return entry.match_processing_state not in _NO_EVIDENCE_MATCH_STATES


def _score_of(entry: ComparisonEntry, dimension: str) -> float | None:
    row = entry.row
    if row is None:
        return None
    return getattr(row, f"{dimension}_score", None)


def _distinguishing_factors(
    best: ComparisonEntry, other: ComparisonEntry
) -> list[str]:
    """Name the dimensions that actually separate two ranked resumes."""
    factors: list[str] = []

    if (
        best.required_skills_total
        and other.required_skills_total
        and (best.required_skills_matched or 0) != (other.required_skills_matched or 0)
    ):
        factors.append(
            "Required-skill coverage differs: "
            f"{best.required_skills_matched} of {best.required_skills_total} "
            f"versus {other.required_skills_matched} of "
            f"{other.required_skills_total}."
        )

    labels = {
        "experience": "Experience",
        "project": "Project",
        "education": "Education",
        "title": "Title alignment",
    }
    for dimension, label in labels.items():
        left = _score_of(best, dimension)
        right = _score_of(other, dimension)
        if left is None or right is None:
            continue
        if abs(left - right) >= DISTINGUISHING_DIMENSION_DELTA:
            factors.append(
                f"{label} scores differ by {abs(left - right):.1f} points "
                f"({left:.1f} versus {right:.1f})."
            )

    left_conf = _confidence_rank(best.evidence_confidence)
    right_conf = _confidence_rank(other.evidence_confidence)
    if left_conf != right_conf:
        factors.append(
            "Evidence confidence differs: "
            f"{best.evidence_confidence} versus {other.evidence_confidence}."
        )

    if not factors:
        factors.append(
            "The scores are effectively tied on the recorded evidence; the "
            "remaining difference is within the ranking tolerance."
        )
    return factors


@dataclass(slots=True, frozen=True)
class RecommendationAlternative:
    """One other qualifying resume, relative to the recommendation."""

    resume_id: UUID
    resume_title: str
    overall_score: float
    score_difference: float
    distinguishing_factors: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class RecommendationOutcome:
    """The recommendation (or the documented reason none was made)."""

    recommended: ComparisonEntry | None
    confidence: str | None
    reasons: tuple[str, ...] = ()
    supporting_evidence: tuple[dict[str, Any], ...] = ()
    important_gaps: tuple[str, ...] = ()
    alternatives: tuple[RecommendationAlternative, ...] = ()
    close_call: bool = False
    close_call_threshold: float = DEFAULT_CLOSE_SCORE_THRESHOLD
    close_call_explanation: str | None = None
    unavailable_reason: str | None = None
    disclaimer: str = SCORE_DISCLAIMER


class ResumeRecommendationService:
    """Selects the best resume from an already ranked comparison."""

    def __init__(
        self, *, close_score_threshold: float = DEFAULT_CLOSE_SCORE_THRESHOLD
    ) -> None:
        if close_score_threshold < 0:
            raise ValueError("close_score_threshold must not be negative")
        self.close_score_threshold = close_score_threshold

    def recommend(
        self, ranked: list[ComparisonEntry]
    ) -> RecommendationOutcome:
        """Return a recommendation, or an explicit reason there is none."""
        suitable = [entry for entry in ranked if _is_suitable(entry)]
        if not suitable:
            return RecommendationOutcome(
                recommended=None,
                confidence=None,
                close_call_threshold=self.close_score_threshold,
                unavailable_reason=self._unavailable_reason(ranked),
            )

        best = suitable[0]
        best_score = best.overall_score or 0.0
        close_call = len(suitable) > 1 and (
            best_score - (suitable[1].overall_score or 0.0)
        ) <= self.close_score_threshold

        confidence = self._confidence(best, close_call=close_call)
        alternatives = tuple(
            RecommendationAlternative(
                resume_id=entry.resume_id,
                resume_title=entry.resume_title,
                overall_score=entry.overall_score or 0.0,
                score_difference=round(best_score - (entry.overall_score or 0.0), 1),
                distinguishing_factors=tuple(_distinguishing_factors(best, entry)),
            )
            for entry in suitable[1 : MAX_ALTERNATIVES + 1]
        )
        close_explanation = (
            self._close_call_explanation(best, suitable[1]) if close_call else None
        )

        return RecommendationOutcome(
            recommended=best,
            confidence=confidence,
            reasons=tuple(self._reasons(best, close_call=close_call)),
            supporting_evidence=tuple(self._evidence(best)),
            important_gaps=tuple(self._gaps(best)),
            alternatives=alternatives,
            close_call=close_call,
            close_call_threshold=self.close_score_threshold,
            close_call_explanation=close_explanation,
        )

    # --- Narrative ----------------------------------------------------------
    @staticmethod
    def _confidence(entry: ComparisonEntry, *, close_call: bool) -> str:
        """Recommendation confidence derived from evidence, not the score alone.

        A close call can never be presented as high confidence; when the match
        itself has low evidence confidence the recommendation stays low.
        """
        match_confidence = (entry.evidence_confidence or "low").lower()
        if match_confidence == "low":
            return "low"
        return "medium" if close_call else match_confidence

    def _reasons(
        self, best: ComparisonEntry, *, close_call: bool
    ) -> list[str]:
        score = best.overall_score or 0.0
        reasons = [f"Highest overall compatibility score ({score:.1f} of 100)."]
        if best.required_skills_total:
            reasons.append(
                f"Evidences {best.required_skills_matched} of "
                f"{best.required_skills_total} required skills"
                f" ({round((best.required_skill_coverage or 0.0) * 100)}% coverage)."
            )
        if best.evidence_confidence:
            reasons.append(
                f"Evidence confidence for this match is {best.evidence_confidence}."
            )
        if best.match_processing_state == STATE_PARTIAL:
            reasons.append(
                "Some analysis inputs are missing; the score uses the available "
                "resume and posting data."
            )
        if best.row is not None:
            strengths = [
                str(item) for item in (best.row.strengths or []) if isinstance(item, str)
            ]
            reasons.extend(strengths[:2])
        if close_call:
            reasons.append(
                "Another resume is within the close-call threshold, so this is a "
                "preference rather than a clear winner."
            )
        return reasons[:MAX_RECOMMENDATION_REASONS]

    @staticmethod
    def _evidence(best: ComparisonEntry) -> list[dict[str, Any]]:
        raw = _details(best).get("evidence")
        if not isinstance(raw, list):
            return []
        evidence = [item for item in raw if isinstance(item, dict)]
        return evidence[:MAX_EVIDENCE]

    @staticmethod
    def _gaps(best: ComparisonEntry) -> list[str]:
        details = _details(best)
        raw = details.get("gaps")
        gaps = [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
        if best.row is not None:
            missing = [
                skill
                for skill in (best.row.missing_required_skills or [])
                if isinstance(skill, str)
            ]
            if missing:
                gaps.append(
                    "Required skills not stated on this resume: "
                    + ", ".join(missing[:5])
                    + "."
                )
        return gaps[:MAX_GAPS]

    def _close_call_explanation(
        self, best: ComparisonEntry, runner_up: ComparisonEntry
    ) -> str:
        difference = round(
            (best.overall_score or 0.0) - (runner_up.overall_score or 0.0), 1
        )
        factors = _distinguishing_factors(best, runner_up)
        return (
            f"{best.resume_title} leads {runner_up.resume_title} by "
            f"{difference:g} points, within the {self.close_score_threshold:g}-point "
            "close-call threshold. " + " ".join(factors)
        )

    @staticmethod
    def _unavailable_reason(ranked: list[ComparisonEntry]) -> str:
        if not ranked:
            return (
                "None of the resumes on file could be compared, so no "
                "recommendation can be made."
            )
        states = sorted({entry.state for entry in ranked})
        return (
            "None of the compared resumes has enough usable evidence for a "
            "definitive recommendation (states: " + ", ".join(states) + "). "
            "Review or update the resumes and run resume analysis where it is "
            "still required."
        )


__all__ = [
    "DEFAULT_CLOSE_SCORE_THRESHOLD",
    "DISTINGUISHING_DIMENSION_DELTA",
    "RecommendationAlternative",
    "RecommendationOutcome",
    "ResumeRecommendationService",
]
