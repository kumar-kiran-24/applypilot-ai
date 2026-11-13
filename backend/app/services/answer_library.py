"""Reusable-answer library: categories, trust, evidence and safe reuse.

The library is deliberately a *view* over the existing ``application_answers``
table. It adds no table, no duplicate answer store and no new lifecycle: every
value, timestamp and provenance field already exists on
:class:`app.models.application_answer.ApplicationAnswer`.

What it adds is interpretation that the user must be able to audit:

* a category derived deterministically from the question the answer belongs to;
* a trust level derived from the stored provenance (never invented facts);
* the evidence and source behind a saved answer;
* ranked reuse suggestions for a new application question, with conflicts
  surfaced rather than silently resolved;
* a candidate-knowledge report of missing, conflicting, outdated and unverified
  information.

Nothing here writes candidate facts. Suggestions are only offered; the
frontend inserts text into a local editor and the existing per-application
save/confirm controls remain in charge.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.application import AnswerSource
from app.models.application_answer import ApplicationAnswer, VerificationStatus
from app.models.candidate import Candidate
from app.services.answer_safety import is_forbidden_answer_storage
from app.services.application_answer_matching import (
    LEGAL_CATEGORIES,
    AnswerConflict,
    ApplicationAnswerMatcher,
    QuestionDescriptor,
    StaleAnswer,
)

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

#: Categories supported by the existing application data. They describe the
#: *question* an answer belongs to, so they are derived rather than stored.
CATEGORY_LABELS: dict[str, str] = {
    "personal_introduction": "Personal introduction",
    "work_experience": "Work experience",
    "projects": "Projects",
    "technical_skills": "Technical skills",
    "achievements": "Achievements",
    "strengths": "Strengths",
    "career_goals": "Career goals",
    "education": "Education",
    "other": "Other",
}

CATEGORY_ORDER: tuple[str, ...] = tuple(CATEGORY_LABELS)


def _phrase_pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    ordered = sorted({phrase for phrase in phrases if phrase}, key=len, reverse=True)
    body = "|".join(re.escape(phrase) for phrase in ordered)
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])")


#: Order matters: the most specific meaning wins, and "other" is the fallback.
_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "career_goals",
        _phrase_pattern(
            (
                "career goal",
                "career goals",
                "career objective",
                "career aspiration",
                "career aspirations",
                "career plan",
                "long term goal",
                "long term goals",
                "where do you see yourself",
                "five years from now",
                "why do you want",
                "why are you interested",
                "motivation for applying",
                "what motivates you",
            )
        ),
    ),
    (
        "personal_introduction",
        _phrase_pattern(
            (
                "tell us about yourself",
                "tell me about yourself",
                "about yourself",
                "introduce yourself",
                "personal introduction",
                "professional summary",
                "personal summary",
                "short bio",
                "who are you",
                "describe yourself",
                "summary of your background",
                "overview of your background",
            )
        ),
    ),
    (
        "strengths",
        _phrase_pattern(
            (
                "greatest strength",
                "greatest strengths",
                "key strength",
                "key strengths",
                "strength",
                "strengths",
                "greatest asset",
                "what are you good at",
                "best quality",
                "strong point",
                "strong points",
            )
        ),
    ),
    (
        "projects",
        _phrase_pattern(
            (
                "side project",
                "side projects",
                "portfolio project",
                "portfolio projects",
                "project",
                "projects",
                "portfolio",
            )
        ),
    ),
    (
        "achievements",
        _phrase_pattern(
            (
                "achievement",
                "achievements",
                "accomplishment",
                "accomplishments",
                "award",
                "awards",
                "recognition",
                "proud of",
                "patent",
                "publication",
                "honor",
                "honour",
                "certification",
                "certifications",
                "certificate",
            )
        ),
    ),
    (
        "technical_skills",
        _phrase_pattern(
            (
                "technical skill",
                "technical skills",
                "key skill",
                "key skills",
                "tech stack",
                "technology stack",
                "technologies",
                "programming language",
                "programming languages",
                "areas of expertise",
                "tools",
                "skills",
            )
        ),
    ),
    (
        "education",
        _phrase_pattern(
            (
                "education",
                "educational background",
                "academic background",
                "degree",
                "university",
                "college",
                "school",
                "gpa",
                "coursework",
                "graduation",
            )
        ),
    ),
    (
        "work_experience",
        _phrase_pattern(
            (
                "work experience",
                "professional experience",
                "employment history",
                "work history",
                "job history",
                "career history",
                "professional background",
                "current role",
                "current company",
                "current employer",
                "responsibilities",
                "experience",
            )
        ),
    ),
)

_KEY_PREFIX_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("experience.", "work_experience"),
    ("projects.", "projects"),
    ("skills.", "technical_skills"),
    ("education.", "education"),
    ("certifications.", "achievements"),
)

#: Canonical field prefixes that describe a literal detail rather than one of
#: the knowledge categories above. They stay in "other".
_LITERAL_PREFIXES: tuple[str, ...] = (
    "personal.",
    "contact.",
    "address.",
    "compensation.",
    "availability.",
    "authorization.",
    "documents.",
    "declarations.",
    "company.",
)

_WORD_SPLIT = re.compile(r"[^a-z0-9+#]+")


def normalise_text(value: str | None) -> str:
    """Casefold and reduce text to space-separated words for comparison."""
    return " ".join(
        part for part in _WORD_SPLIT.split((value or "").casefold()) if part
    )


def token_set(value: str | None) -> set[str]:
    return {token for token in normalise_text(value).split() if token}


def comparable_value(value: str | None) -> str:
    """Normalise a value for equality checks without inventing meaning."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


# ---------------------------------------------------------------------------
# Trust levels
# ---------------------------------------------------------------------------

#: Trust levels are a projection of the stored ``verification_status`` and
#: ``answer_source``. An AI draft is never reported as a verified fact.
TRUST_LABELS: dict[str, str] = {
    "verified_fact": "Verified candidate fact",
    "user_reviewed": "User-reviewed answer",
    "resume_suggestion": "Resume-derived suggestion",
    "ai_draft": "AI-generated draft",
    "user_written": "User-written, not yet reviewed",
    "needs_information": "Requires additional information",
}

TRUST_ORDER: tuple[str, ...] = tuple(TRUST_LABELS)

#: Validation patterns shared by the API query filters.
CATEGORY_PATTERN = "^(" + "|".join(CATEGORY_ORDER) + ")$"
TRUST_PATTERN = "^(" + "|".join(TRUST_ORDER) + ")$"


@dataclass(frozen=True)
class TrustAssessment:
    """How much a stored answer can be trusted, and why."""

    level: str
    label: str
    reason: str
    needs_review: bool

    @property
    def verified(self) -> bool:
        return self.level in {"verified_fact", "user_reviewed"}


@dataclass(frozen=True)
class AnswerEvidence:
    """One piece of provenance shown next to a saved answer."""

    id: str
    kind: str
    label: str
    detail: str
    source: str
    verified: bool


@dataclass(frozen=True)
class SavedAnswerView:
    """A saved answer plus its derived category, trust and freshness."""

    answer: ApplicationAnswer
    category: str
    category_label: str
    trust: TrustAssessment
    is_stale: bool
    freshness_reason: str | None
    conflicts: tuple[AnswerConflict, ...] = ()

    @property
    def needs_information(self) -> bool:
        return self.trust.level == "needs_information"


@dataclass(frozen=True)
class ReuseSuggestion:
    """A saved answer offered for a new application question."""

    answer: ApplicationAnswer
    relevance: float
    reason: str
    category: str
    category_label: str
    trust: TrustAssessment
    conflicts: tuple[AnswerConflict, ...] = ()
    is_stale: bool = False
    freshness_reason: str | None = None

    @property
    def requires_review(self) -> bool:
        return not self.trust.verified or self.is_stale

    @property
    def safe_to_insert(self) -> bool:
        """Conflicting or outdated facts are never offered as a safe value."""
        return not self.conflicts and not self.is_stale


@dataclass(frozen=True)
class KnowledgeMissingItem:
    """Information the candidate still needs to supply."""

    field: str
    label: str
    section: str
    required: bool
    kind: str
    question_key: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class KnowledgeUnverifiedItem:
    """A saved answer that cannot be treated as a verified fact."""

    question_key: str
    question_text: str
    trust_level: str
    trust_label: str
    reason: str


@dataclass(frozen=True)
class KnowledgeReport:
    """Missing, conflicting, outdated and unverified candidate information."""

    total_answers: int
    verified_answers: int
    needs_review: int
    missing_information: tuple[KnowledgeMissingItem, ...] = ()
    conflicts: tuple[AnswerConflict, ...] = ()
    outdated: tuple[StaleAnswer, ...] = ()
    unverified: tuple[KnowledgeUnverifiedItem, ...] = ()
    summary: str = ""


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


class AnswerLibrary:
    """Deterministic interpretation and reuse of saved answers."""

    def __init__(self, matcher: ApplicationAnswerMatcher | None = None) -> None:
        self.matcher = matcher or ApplicationAnswerMatcher()

    # --- Classification -----------------------------------------------------
    def category_for(
        self,
        question_key: str | None = None,
        question_text: str | None = None,
    ) -> str:
        """Return the supported category for a question.

        A canonical key prefix wins, then the question wording. Anything the
        existing data cannot support is reported as ``other`` rather than
        squeezed into a category it does not belong to.
        """
        key = (question_key or "").strip().casefold()
        for prefix, category in _KEY_PREFIX_CATEGORIES:
            if key == prefix.rstrip(".") or key.startswith(prefix):
                return category

        text = normalise_text(question_text or "")
        if text:
            for category, pattern in _CATEGORY_PATTERNS:
                if pattern.search(text):
                    return category

        if any(key.startswith(prefix) for prefix in _LITERAL_PREFIXES):
            return "other"
        return "other"

    def category_label(self, category: str) -> str:
        return CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])

    # --- Trust and freshness ------------------------------------------------
    def trust_for(self, answer: ApplicationAnswer) -> TrustAssessment:
        """Project stored provenance onto an explicit, honest trust level."""
        value = (answer.answer or "").strip()
        if not value:
            return TrustAssessment(
                level="needs_information",
                label=TRUST_LABELS["needs_information"],
                reason="No value is saved for this question yet.",
                needs_review=True,
            )
        source = answer.answer_source
        verified = answer.verification_status == VerificationStatus.VERIFIED
        if source == AnswerSource.NEEDS_CONFIRMATION:
            return TrustAssessment(
                level="needs_information",
                label=TRUST_LABELS["needs_information"],
                reason=(
                    "This value was carried over from an older answer and still "
                    "needs your confirmation."
                ),
                needs_review=True,
            )
        if verified and source == AnswerSource.VERIFIED_PROFILE:
            return TrustAssessment(
                level="verified_fact",
                label=TRUST_LABELS["verified_fact"],
                reason="Taken from a profile value you verified.",
                needs_review=False,
            )
        if verified:
            return TrustAssessment(
                level="user_reviewed",
                label=TRUST_LABELS["user_reviewed"],
                reason="You reviewed and confirmed this answer.",
                needs_review=False,
            )
        if source == AnswerSource.AI_DRAFT:
            return TrustAssessment(
                level="ai_draft",
                label=TRUST_LABELS["ai_draft"],
                reason=(
                    "Drafted by the assistant. It is a suggestion, not a fact, "
                    "until you review and confirm it."
                ),
                needs_review=True,
            )
        if source == AnswerSource.RESUME_SUGGESTION:
            return TrustAssessment(
                level="resume_suggestion",
                label=TRUST_LABELS["resume_suggestion"],
                reason=(
                    "Parsed from a resume. Verify it against the actual document "
                    "before relying on it."
                ),
                needs_review=True,
            )
        return TrustAssessment(
            level="user_written",
            label=TRUST_LABELS["user_written"],
            reason="Written by you, but not yet reviewed and confirmed.",
            needs_review=True,
        )

    def freshness(
        self, answer: ApplicationAnswer, *, now: datetime | None = None
    ) -> tuple[bool, str | None]:
        """Return ``(is_stale, reason)`` for a verified answer only."""
        if answer.verification_status != VerificationStatus.VERIFIED:
            return False, None
        assessment = self.matcher.validate_answer_freshness(answer, now=now)
        if assessment.fresh:
            return False, None
        return True, assessment.reason

    def is_forbidden(self, answer: ApplicationAnswer) -> bool:
        return bool(answer.is_legal) or is_forbidden_answer_storage(
            answer.question_key, answer.question_text
        )

    # --- Description --------------------------------------------------------
    def describe(
        self,
        answer: ApplicationAnswer,
        *,
        conflicts: Sequence[AnswerConflict] = (),
        now: datetime | None = None,
    ) -> SavedAnswerView:
        category = self.category_for(answer.question_key, answer.question_text)
        is_stale, reason = self.freshness(answer, now=now)
        related = tuple(
            item
            for item in conflicts
            if item.question_key == answer.question_key
        )
        return SavedAnswerView(
            answer=answer,
            category=category,
            category_label=self.category_label(category),
            trust=self.trust_for(answer),
            is_stale=is_stale,
            freshness_reason=reason,
            conflicts=related,
        )

    def describe_many(
        self,
        answers: Sequence[ApplicationAnswer],
        *,
        candidate: Candidate | None = None,
        profile_values: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> tuple[SavedAnswerView, ...]:
        conflicts = (
            self.matcher.detect_answer_conflicts(
                candidate, answers, profile_values=profile_values
            )
            if answers
            else ()
        )
        return tuple(
            self.describe(answer, conflicts=conflicts, now=now) for answer in answers
        )

    # --- Evidence -----------------------------------------------------------
    def evidence_for(
        self,
        answer: ApplicationAnswer,
        *,
        profile_values: Mapping[str, str] | None = None,
    ) -> tuple[AnswerEvidence, ...]:
        """Return the stored evidence and source behind one answer."""
        value = (answer.answer or "").strip()
        trust = self.trust_for(answer)
        items: list[AnswerEvidence] = [
            AnswerEvidence(
                id=f"answer:{answer.question_key}",
                kind="answer",
                label="Saved answer",
                detail=value[:400] if value else "No value saved yet.",
                source=f"application_answer:{answer.question_key}",
                verified=trust.verified,
            )
        ]

        record = (answer.source_record_id or "").strip()
        if record:
            kind = "source"
            if record.startswith(("candidate", "profile")):
                kind = "profile"
            elif record.startswith("resume"):
                kind = "resume"
            elif record.startswith("application"):
                kind = "application"
            items.append(
                AnswerEvidence(
                    id=f"source:{answer.question_key}",
                    kind=kind,
                    label="Recorded source",
                    detail=record,
                    source=answer.answer_source.value,
                    verified=trust.verified and kind == "profile",
                )
            )

        canonical = self.matcher.identify_field_category(
            QuestionDescriptor(
                question_key=answer.question_key,
                question_text=answer.question_text,
            )
        )
        profile_value = (profile_values or {}).get(canonical or "")
        if canonical and profile_value:
            items.append(
                AnswerEvidence(
                    id=f"profile:{canonical}",
                    kind="profile",
                    label=f"Verified profile · {canonical}",
                    detail=profile_value,
                    source="candidate_profile",
                    verified=True,
                )
            )
        return tuple(items)

    # --- Listing and filtering ---------------------------------------------
    @staticmethod
    def matches(
        answer: ApplicationAnswer,
        *,
        query: str | None = None,
        category: str | None = None,
        trust: str | None = None,
        library: AnswerLibrary | None = None,
    ) -> bool:
        if library is None:
            library = AnswerLibrary()
        if query:
            needle = query.strip().casefold()
            haystack = " ".join(
                (
                    answer.question_text or "",
                    answer.question_key or "",
                    answer.answer or "",
                )
            ).casefold()
            if needle not in haystack:
                return False
        if category and library.category_for(
            answer.question_key, answer.question_text
        ) != category:
            return False
        if trust:
            return library.trust_for(answer).level == trust
        return True

    def category_counts(
        self, answers: Sequence[ApplicationAnswer]
    ) -> tuple[dict[str, int], tuple[str, ...]]:
        """Return per-category counts and the categories actually present."""
        counts: dict[str, int] = {}
        for answer in answers:
            category = self.category_for(answer.question_key, answer.question_text)
            counts[category] = counts.get(category, 0) + 1
        present = tuple(
            category for category in CATEGORY_ORDER if counts.get(category)
        )
        return counts, present

    # --- Reuse suggestions --------------------------------------------------
    def reuse_suggestions(
        self,
        *,
        question_key: str,
        question_text: str,
        answers: Sequence[ApplicationAnswer],
        candidate: Candidate | None,
        profile_values: Mapping[str, str] | None = None,
        job_title: str | None = None,
        job_skills: Sequence[str] = (),
        now: datetime | None = None,
    ) -> tuple[ReuseSuggestion, ...]:
        """Rank saved answers that genuinely relate to one new question.

        Matching is conservative and explainable: an exact key or question
        wording wins, a shared canonical field is next, then the same derived
        category with real word overlap. Two questions that merely share
        words across different categories never match. Job title and required
        skills only reorder genuine matches; they never create one.
        """
        target = QuestionDescriptor(
            question_key=question_key, question_text=question_text
        )
        target_canonical = self.matcher.identify_field_category(target)
        target_category = self.category_for(question_key, question_text)
        target_tokens = token_set(question_text)
        target_text = normalise_text(question_text)
        job_tokens = token_set(
            " ".join(part for part in (job_title, *job_skills) if part)
        )

        conflicts = (
            self.matcher.detect_answer_conflicts(
                candidate, answers, profile_values=profile_values
            )
            if answers
            else ()
        )
        profile_lookup = profile_values or {}

        suggestions: list[ReuseSuggestion] = []
        for answer in answers:
            value = (answer.answer or "").strip()
            if not value or self.is_forbidden(answer):
                continue

            saved = QuestionDescriptor(
                question_key=answer.question_key, question_text=answer.question_text
            )
            saved_canonical = self.matcher.identify_field_category(saved)
            saved_category = self.category_for(
                answer.question_key, answer.question_text
            )
            saved_text = normalise_text(answer.question_text)
            saved_tokens = token_set(answer.question_text)
            union = target_tokens | saved_tokens
            overlap = (
                len(target_tokens & saved_tokens) / len(union) if union else 0.0
            )

            score = 0.0
            reason = ""
            if (
                target_canonical
                and saved_canonical
                and target_canonical == saved_canonical
            ):
                score = 0.9
                reason = f"Answers the same field ({target_canonical})."
            elif question_key and (
                answer.question_key.strip().casefold()
                == question_key.strip().casefold()
            ):
                score = 1.0
                reason = "Saved for the same question key."
            elif target_text and saved_text == target_text:
                score = 0.95
                reason = "Saved for the same question wording."
            elif (
                target_category == saved_category
                and target_category != "other"
                and overlap > 0
            ):
                score = 0.55 + (0.4 * overlap)
                reason = (
                    f"Same category ({self.category_label(target_category)}) "
                    "with overlapping wording."
                )
            elif overlap >= 0.5:
                score = 0.45 + (0.4 * overlap)
                reason = "Wording overlaps strongly with this question."
            else:
                continue

            answer_tokens = token_set(value)
            job_overlap = job_tokens & answer_tokens
            if job_overlap:
                score = min(0.98, score + 0.05)
                reason = f"{reason} Mentions skills from this posting."

            is_stale, freshness_reason = self.freshness(answer, now=now)
            related_conflicts = tuple(
                item
                for item in conflicts
                if item.question_key == answer.question_key
            )
            canonical_for_target = target_canonical or saved_canonical
            profile_value = profile_lookup.get(canonical_for_target or "")
            already_reported = any(
                conflict.conflicting_source == "profile"
                for conflict in related_conflicts
            )
            if (
                canonical_for_target
                and profile_value
                and not already_reported
                and comparable_value(profile_value) != comparable_value(value)
            ):
                related_conflicts = (*related_conflicts, AnswerConflict(
                        question_key=answer.question_key,
                        question_text=answer.question_text,
                        category=canonical_for_target,
                        stored_answer=value,
                        conflicting_answer=profile_value,
                        conflicting_source="profile",
                        reason=(
                            "Your verified profile now says something different "
                            "from this saved answer."
                        ),
                        resolution_options=("keep_stored", "use_profile", "edited"),
                    ),
                )

            suggestions.append(
                ReuseSuggestion(
                    answer=answer,
                    relevance=round(min(1.0, score), 3),
                    reason=reason,
                    category=saved_category,
                    category_label=self.category_label(saved_category),
                    trust=self.trust_for(answer),
                    conflicts=related_conflicts,
                    is_stale=is_stale,
                    freshness_reason=freshness_reason,
                )
            )

        suggestions.sort(
            key=lambda item: (
                -item.relevance,
                -item.answer.updated_at.timestamp()
                if item.answer.updated_at
                else 0.0,
            )
        )
        return tuple(suggestions)

    # --- Candidate knowledge ------------------------------------------------
    def knowledge_report(
        self,
        answers: Sequence[ApplicationAnswer],
        *,
        candidate: Candidate | None,
        profile_values: Mapping[str, str] | None = None,
        profile_fields: Sequence[Mapping[str, Any]] = (),
        now: datetime | None = None,
    ) -> KnowledgeReport:
        """Report gaps, conflicts, outdated values and unverified claims.

        The report is read-only. It never edits a verified fact and never
        fills a missing detail with an assumption.
        """
        views = self.describe_many(
            answers,
            candidate=candidate,
            profile_values=profile_values,
            now=now,
        )

        missing: list[KnowledgeMissingItem] = [
            KnowledgeMissingItem(
                field=str(field.get("field") or ""),
                label=str(field.get("label") or field.get("field") or ""),
                section=str(field.get("section") or ""),
                required=bool(field.get("required")),
                kind="profile_field",
                reason=(
                    "This required profile detail is missing. Add it on your "
                    "profile; ApplyPilot will not guess it."
                ),
            )
            for field in profile_fields
            if not field.get("complete")
        ]
        for view in views:
            if view.needs_information:
                missing.append(
                    KnowledgeMissingItem(
                        field=view.answer.question_key,
                        label=view.answer.question_text,
                        section=view.category_label,
                        required=False,
                        kind="answer",
                        question_key=view.answer.question_key,
                        reason=view.trust.reason,
                    )
                )

        conflicts = tuple(
            conflict for view in views for conflict in view.conflicts
        )
        outdated_items: list[StaleAnswer] = []
        for view in views:
            if not view.is_stale:
                continue
            assessment = self.matcher.validate_answer_freshness(
                view.answer, now=now
            )
            outdated_items.append(
                StaleAnswer(
                    question_key=view.answer.question_key,
                    question_text=view.answer.question_text,
                    last_confirmed_at=view.answer.last_confirmed_at
                    or view.answer.verified_at,
                    age_days=assessment.age_days,
                    max_age_days=assessment.max_age_days,
                    reason=view.freshness_reason or "This answer is outdated.",
                )
            )
        outdated = tuple(outdated_items)
        unverified = tuple(
            KnowledgeUnverifiedItem(
                question_key=view.answer.question_key,
                question_text=view.answer.question_text,
                trust_level=view.trust.level,
                trust_label=view.trust.label,
                reason=view.trust.reason,
            )
            for view in views
            if not view.trust.verified
        )

        verified = sum(1 for view in views if view.trust.verified)
        needs_review = sum(1 for view in views if view.trust.needs_review)
        summary = (
            f"{verified} of {len(views)} saved answers are confirmed; "
            f"{len(conflicts)} conflict(s), {len(outdated)} outdated and "
            f"{len(unverified)} unverified answer(s) need your review."
        )
        return KnowledgeReport(
            total_answers=len(views),
            verified_answers=verified,
            needs_review=needs_review,
            missing_information=tuple(missing),
            conflicts=conflicts,
            outdated=outdated,
            unverified=unverified,
            summary=summary,
        )


__all__ = [
    "CATEGORY_LABELS",
    "CATEGORY_ORDER",
    "CATEGORY_PATTERN",
    "TRUST_LABELS",
    "TRUST_ORDER",
    "TRUST_PATTERN",
    "AnswerEvidence",
    "AnswerLibrary",
    "KnowledgeMissingItem",
    "KnowledgeReport",
    "KnowledgeUnverifiedItem",
    "ReuseSuggestion",
    "SavedAnswerView",
    "TrustAssessment",
    "comparable_value",
    "normalise_text",
    "token_set",
]

# Legal categories are never offered for reuse; imported here so callers can
# assert the same rule without reaching into the matcher module.
NEVER_REUSED_CATEGORIES = LEGAL_CATEGORIES
