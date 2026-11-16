// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerLibrary } from "@/components/answers/answer-library";
import type {
  ApplicationPreview,
  ApplicationSummary,
  CandidateKnowledge,
  ReusableAnswerSuggestions,
  SavedAnswer,
  SavedAnswerDetail,
} from "@/types/api";

const APP_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const QUESTION_ID = "33333333-3333-4333-8333-333333333333";

function makeAnswer(overrides: Partial<SavedAnswer> = {}): SavedAnswer {
  return {
    id: "44444444-4444-4444-8444-444444444444",
    candidate_id: "55555555-5555-4555-8555-555555555555",
    question_key: "custom.intro",
    question_text: "Tell us about yourself.",
    answer: "I am a backend engineer.",
    answer_type: "text",
    verification_status: "verified",
    is_sensitive: false,
    is_legal: false,
    verified_at: "2026-11-01T09:00:00Z",
    answer_source: "previously_approved",
    confidence: 1,
    source_record_id: null,
    last_confirmed_at: "2026-11-01T09:00:00Z",
    freshness_days: null,
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-02T09:00:00Z",
    category: "personal_introduction",
    category_label: "Personal introduction",
    trust_level: "user_reviewed",
    trust_label: "User-reviewed answer",
    trust_reason: "You reviewed and confirmed this answer.",
    needs_review: false,
    needs_information: false,
    is_stale: false,
    freshness_reason: null,
    ...overrides,
  };
}

function makeDraftAnswer(): SavedAnswer {
  return makeAnswer({
    id: "66666666-6666-4666-8666-666666666666",
    question_key: "custom.skills",
    question_text: "What technical skills do you have?",
    answer: "Python and FastAPI.",
    verification_status: "unknown",
    verified_at: null,
    answer_source: "ai_suggestion",
    confidence: 0.4,
    last_confirmed_at: null,
    category: "technical_skills",
    category_label: "Technical skills",
    trust_level: "ai_draft",
    trust_label: "AI-generated draft",
    trust_reason: "Drafted by the assistant. It is a suggestion, not a fact.",
    needs_review: true,
  });
}

function makeKnowledge(): CandidateKnowledge {
  return {
    total_answers: 2,
    verified_answers: 1,
    needs_review: 1,
    missing_information: [
      {
        field: "skills",
        label: "Technical skills",
        section: "skills",
        required: true,
        kind: "profile_field",
        question_key: null,
        reason:
          "This required profile detail is missing. Add it on your profile; ApplyPilot will not guess it.",
      },
    ],
    conflicts: [
      {
        question_key: "contact.phone",
        question_text: "Phone number",
        category: "contact.phone",
        stored_answer: "+1 555 0111",
        conflicting_answer: "+1 555 0100",
        conflicting_source: "profile",
        reason: "Your verified profile now says something different.",
        resolution_options: ["keep_stored", "use_profile", "edited"],
      },
    ],
    outdated: [],
    unverified: [
      {
        question_key: "custom.skills",
        question_text: "What technical skills do you have?",
        trust_level: "ai_draft",
        trust_label: "AI-generated draft",
        reason: "Drafted by the assistant. Review and confirm it.",
      },
    ],
    summary:
      "1 of 2 saved answers are confirmed; 1 conflict(s), 0 outdated and 1 unverified answer(s) need your review.",
    disclaimer:
      "This report never changes verified information and never fills a missing detail with an assumption.",
  };
}

function makeSummary(): ApplicationSummary {
  return {
    id: APP_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    platform: "greenhouse",
    platform_name: "Greenhouse",
    application_url: "https://boards.greenhouse.io/acme/jobs/1",
    status: "awaiting_user_information",
    stage: null,
    automation_mode: "assisted",
    requires_account: false,
    resume_filename: "backend-strong.pdf",
    questions_total: 1,
    questions_confirmed: 0,
    missing_required_count: 1,
    created_at: "2026-11-10T09:00:00Z",
    updated_at: "2026-11-11T09:00:00Z",
    approved_at: null,
    submitted_at: null,
  };
}

function makePreview(): ApplicationPreview {
  return {
    application_id: APP_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    platform: "greenhouse",
    platform_name: "Greenhouse",
    application_url: "https://boards.greenhouse.io/acme/jobs/1",
    job_listing_url: null,
    employer_careers_url: null,
    application_url_is_job_board: false,
    suggested_application_url: null,
    automation_mode: "assisted",
    inspection_mode: "automated",
    status: "awaiting_user_information",
    stage: null,
    approved_at: null,
    submitted_at: null,
    submission_reference: null,
    failure_reason: null,
    requires_account: false,
    notes: [],
    resume: {
      id: "77777777-7777-4777-8777-777777777777",
      filename: "backend-strong.pdf",
      reason: "Recommended by resume comparison.",
      score: 82.5,
    },
    sections: [
      {
        section: "company",
        label: "Company questions",
        questions: [
          {
            id: QUESTION_ID,
            question_key: "company.cover_letter",
            field_identifier: "cover_letter",
            section: "company",
            question_text: "Why do you want to work here?",
            help_text: null,
            field_type: "textarea",
            required: true,
            options: [],
            suggested_answer: null,
            answer: null,
            answer_source: "user_input",
            confidence: 0,
            requires_confirmation: true,
            is_sensitive: false,
            is_legal: false,
            status: "missing",
            note: null,
          },
        ],
      },
    ],
    missing_required: [],
    progress: {
      total: 1,
      confirmed: 0,
      required_total: 1,
      required_confirmed: 0,
      percent: 0,
    },
    ready_to_approve: false,
    can_submit: false,
    submission_mode: "manual",
    manual_steps: [],
    warnings: [],
  };
}

function makeDetail(answer: SavedAnswer, conflicts = []): SavedAnswerDetail {
  return {
    answer,
    evidence: [
      {
        id: `answer:${answer.question_key}`,
        kind: "answer",
        label: "Saved answer",
        detail: answer.answer ?? "",
        source: `application_answer:${answer.question_key}`,
        verified: !answer.needs_review,
      },
      {
        id: "source:record",
        kind: "application",
        label: "Recorded source",
        detail: "application:demo:question:custom.intro",
        source: answer.answer_source ?? "user_input",
        verified: false,
      },
    ],
    conflicts,
  };
}

function makeSuggestions(answer: SavedAnswer): ReusableAnswerSuggestions {
  return {
    application_id: APP_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    question_key: "company.cover_letter",
    question_text: "Why do you want to work here?",
    suggestions: [
      {
        question_key: answer.question_key,
        question_text: answer.question_text,
        answer: answer.answer ?? "",
        answer_source: "previously_approved",
        verification_status: "verified",
        category: "other",
        category_label: "Other",
        trust_level: "user_reviewed",
        trust_label: "User-reviewed answer",
        trust_reason: "You reviewed and confirmed this answer.",
        relevance: 0.9,
        reason: "Answers the same field (company.cover_letter).",
        last_confirmed_at: "2026-11-01T09:00:00Z",
        updated_at: "2026-11-02T09:00:00Z",
        requires_review: false,
        safe_to_insert: true,
        is_stale: false,
        freshness_reason: null,
        conflicts: [],
      },
    ],
    message:
      "Suggestions only. Nothing is inserted into the application or the employer's form until you choose it and review the value.",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();
let savedAnswers: SavedAnswer[];
let knowledge: CandidateKnowledge;

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
}

function applyFilters(path: string): SavedAnswer[] {
  const url = new URL(path, "http://test.local");
  const q = url.searchParams.get("q")?.toLowerCase();
  const category = url.searchParams.get("category");
  const trust = url.searchParams.get("trust");
  return savedAnswers.filter((answer) => {
    if (q) {
      const haystack = [answer.question_text, answer.question_key, answer.answer ?? ""]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    if (category && answer.category !== category) return false;
    if (trust && answer.trust_level !== trust) return false;
    return true;
  });
}

function installFetch() {
  fetchMock.mockImplementation(async (input, init) => {
    const path = pathOf(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;

    if (path.startsWith("profile/answers?") && method === "GET") {
      return json(applyFilters(path));
    }
    if (path === "profile/answers" && method === "GET") {
      return json(savedAnswers);
    }
    if (path === "profile/answers/categories" && method === "GET") {
      return json({
        total: savedAnswers.length,
        categories: savedAnswers.map((answer) => ({
          category: answer.category,
          label: answer.category_label,
          count: 1,
        })),
        trust_levels: {},
      });
    }
    if (path === "profile/answers/knowledge" && method === "GET") {
      return json(knowledge);
    }
    if (path.endsWith("/resolve") && method === "POST") {
      return json(savedAnswers[0]);
    }
    if (path.startsWith("profile/answers/")) {
      const key = decodeURIComponent(path.split("/")[2] ?? "");
      const index = savedAnswers.findIndex((item) => item.question_key === key);
      const current = savedAnswers[index];

      if (method === "GET" && current) {
        return json(makeDetail(current));
      }
      if (method === "DELETE") {
        savedAnswers = savedAnswers.filter((item) => item.question_key !== key);
        return json({ question_key: key, deleted: true });
      }
      if (method === "PUT" && current) {
        const verified = body?.verification_status === "verified";
        const updated: SavedAnswer = {
          ...current,
          answer: body?.answer ?? current.answer,
          question_text: body?.question_text ?? current.question_text,
          verification_status: verified ? "verified" : "unknown",
          verified_at: verified ? "2026-11-12T09:00:00Z" : null,
          last_confirmed_at: verified ? "2026-11-12T09:00:00Z" : null,
          answer_source: verified ? "previously_approved" : "user_input",
          trust_level: verified ? "user_reviewed" : "user_written",
          trust_label: verified
            ? "User-reviewed answer"
            : "User-written, not yet reviewed",
          trust_reason: verified
            ? "You reviewed and confirmed this answer."
            : "Written by you, but not yet reviewed and confirmed.",
          needs_review: !verified,
        };
        savedAnswers = savedAnswers.map((item, position) =>
          position === index ? updated : item,
        );
        return json(updated);
      }
    }
    if (path === "applications" && method === "GET") {
      return json({ total: 1, applications: [makeSummary()] });
    }
    if (path === `applications/${APP_ID}` && method === "GET") {
      return json(makePreview());
    }
    if (
      path === `applications/${APP_ID}/questions/${QUESTION_ID}` &&
      method === "PATCH"
    ) {
      return json(makePreview());
    }
    if (
      path.endsWith("/reusable-answers") &&
      method === "GET"
    ) {
      const answer = savedAnswers.find(
        (item) => item.question_key !== "custom.skills",
      );
      return json(makeSuggestions(answer ?? makeAnswer()));
    }
    return json({ detail: "Not mocked." }, 404);
  });
}

function renderLibrary() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AnswerLibrary />
    </QueryClientProvider>,
  );
}

describe("AnswerLibrary", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    savedAnswers = [makeAnswer(), makeDraftAnswer()];
    knowledge = makeKnowledge();
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("lists saved answers with derived categories and trust levels", async () => {
    renderLibrary();

    expect(await screen.findByText("Tell us about yourself.")).toBeTruthy();
    expect(await screen.findByText("Personal introduction")).toBeTruthy();
    expect(screen.getAllByText("Technical skills").length).toBeGreaterThan(0);
    expect(screen.getAllByText("User-reviewed answer").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AI-generated draft").length).toBeGreaterThan(0);
    expect(await screen.findByText("Candidate knowledge center")).toBeTruthy();
    expect(screen.getByText("Conflicts with verified data")).toBeTruthy();
    expect(screen.getByText("Not verified")).toBeTruthy();
    expect(await screen.findByText("Add on profile")).toBeTruthy();
    expect(screen.getByText(/never changes verified information/)).toBeTruthy();
  });

  it("searches and filters through the answer API", async () => {
    renderLibrary();
    await screen.findByText("Tell us about yourself.");

    fireEvent.change(screen.getByLabelText("Search saved answers"), {
      target: { value: "fastapi" },
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          pathOf(input).includes("profile/answers?q=fastapi"),
        ),
      ).toBe(true);
    });
    await waitFor(() =>
      expect(screen.queryByText("Tell us about yourself.")).toBeNull(),
    );

    fireEvent.change(screen.getByLabelText("Search saved answers"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Filter by trust level"), {
      target: { value: "ai_draft" },
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          pathOf(input).includes("trust=ai_draft"),
        ),
      ).toBe(true);
    });
  });

  it("edits without keeping a stale verification and confirms drafts", async () => {
    renderLibrary();
    await screen.findByText("Tell us about yourself.");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const editor = screen.getByLabelText(
      "Edit answer: Tell us about yourself.",
    ) as HTMLTextAreaElement;
    fireEvent.change(editor, {
      target: { value: "I am a platform engineer." },
    });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) === "profile/answers/custom.intro" &&
          (init?.method ?? "GET") === "PUT",
      );
      expect(put).toBeTruthy();
      const body = JSON.parse(String(put?.[1]?.body));
      expect(body.answer).toBe("I am a platform engineer.");
      expect(body.verification_status).toBeUndefined();
    });
    expect(
      await screen.findByText(/changed wording is unverified/i),
    ).toBeTruthy();
    expect(
      screen.getAllByText("User-written, not yet reviewed").length,
    ).toBeGreaterThan(0);

    fireEvent.click(
      screen.getAllByRole("button", { name: /confirm answer/i })[0]!,
    );
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) === "profile/answers/custom.intro" &&
          (init?.method ?? "GET") === "PUT" &&
          String(init?.body).includes('"verified"'),
      );
      expect(put).toBeTruthy();
    });
    expect(await screen.findByText(/confirmed as a reviewed answer/i)).toBeTruthy();
  });

  it("shows evidence, provenance and conflicts in the details panel", async () => {
    savedAnswers = [
      makeAnswer({
        question_key: "contact.phone",
        question_text: "Phone number",
        answer: "+1 555 0111",
        category: "other",
        category_label: "Other",
        trust_level: "verified_fact",
        trust_label: "Verified candidate fact",
        answer_source: "profile",
      }),
    ];
    knowledge = { ...makeKnowledge(), unverified: [] };

    renderLibrary();
    await screen.findByRole("button", { name: "Details" });
    fireEvent.click(screen.getByRole("button", { name: "Details" }));

    expect(await screen.findByText("Evidence and source")).toBeTruthy();
    expect(screen.getByText("Saved answer")).toBeTruthy();
    expect(screen.getByText("Recorded source")).toBeTruthy();
    expect(
      screen.getByText(/application:demo:question:custom.intro/),
    ).toBeTruthy();
    expect(
      screen.getByText(/You reviewed and confirmed this answer/),
    ).toBeTruthy();
  });

  it("reuses a saved answer into an application question as an unconfirmed draft", async () => {
    savedAnswers = [
      makeAnswer({
        question_key: "company.cover_letter",
        question_text: "Cover letter",
        answer: "I build reliable backend services.",
        category: "career_goals",
        category_label: "Career goals",
      }),
    ];
    knowledge = { ...makeKnowledge(), unverified: [] };

    renderLibrary();
    await screen.findByRole("button", { name: "Reuse" });
    fireEvent.click(screen.getByRole("button", { name: "Reuse" }));

    expect(await screen.findByText(/inserted into applypilot/i)).toBeTruthy();
    await screen.findByRole("option", {
      name: "Why do you want to work here?",
    });
    fireEvent.change(screen.getByLabelText("Question"), {
      target: { value: QUESTION_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: /insert as draft/i }));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) ===
            `applications/${APP_ID}/questions/${QUESTION_ID}` &&
          (init?.method ?? "GET") === "PATCH",
      );
      expect(patch).toBeTruthy();
      expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
        answer: "I build reliable backend services.",
        confirmed: false,
        reused_answer_key: "company.cover_letter",
      });
    });
    expect(await screen.findByText(/inserted as an unconfirmed draft/i)).toBeTruthy();
  });

  it("deletes an answer only after an explicit confirmation", async () => {
    renderLibrary();
    await screen.findByText("Tell us about yourself.");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(screen.getByText("Delete this answer?")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([input, init]) =>
      pathOf(input) === "profile/answers/custom.intro" &&
      (init?.method ?? "GET") === "DELETE",
    )).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Delete answer" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            pathOf(input) === "profile/answers/custom.intro" &&
            (init?.method ?? "GET") === "DELETE",
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(screen.queryByText("Tell us about yourself.")).toBeNull(),
    );
  });
});
