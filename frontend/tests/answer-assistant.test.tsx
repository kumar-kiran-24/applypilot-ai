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

import { AnswerAssistant } from "@/components/assistant/answer-assistant";
import type {
  AnswerDraft,
  ApplicationAnswer,
  ApplicationPreview,
  ApplicationSummary,
} from "@/types/api";

const APP_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const QUESTION_ID = "33333333-3333-4333-8333-333333333333";
const KEY = "company.cover_letter";

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
    questions_total: 2,
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
      id: "44444444-4444-4444-8444-444444444444",
      filename: "backend-strong.pdf",
      reason: "Recommended",
      score: 82.5,
    },
    sections: [
      {
        section: "company",
        label: "Company questions",
        questions: [
          {
            id: QUESTION_ID,
            question_key: KEY,
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
    missing_required: [
      { question_key: KEY, question_text: "Why do you want to work here?" },
    ],
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

function makeDraft(overrides: Partial<AnswerDraft> = {}): AnswerDraft {
  return {
    application_id: APP_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    question_id: QUESTION_ID,
    question_key: KEY,
    question_text: "Why do you want to work here?",
    question_type: "textarea",
    answer_type: "text",
    is_sensitive: false,
    is_legal: false,
    status: "drafted",
    draft_answer:
      "I build FastAPI services and want to work on Acme's backend platform.",
    confidence: 0.72,
    confidence_label: "medium",
    evidence: [
      {
        id: "profile.experience.0",
        kind: "profile",
        label: "Experience: Backend Engineer at Acme",
        detail: "Built FastAPI services and improved reliability.",
        source: "candidate_profile",
        verified: true,
        confidence: "high",
        used: true,
      },
      {
        id: "job.description",
        kind: "job",
        label: "Posting: Senior Backend Engineer",
        detail: "Build Python services with FastAPI and PostgreSQL.",
        source: "job_record",
        verified: true,
        confidence: "high",
        used: true,
      },
    ],
    missing_information: [],
    conflicts: [],
    inferred_claims: ["Interest in Acme's backend platform."],
    warnings: [],
    save_reusable_allowed: true,
    llm_status: "used",
    llm_model: "fake-answer-model",
    disclaimer:
      "This draft only uses evidence from your verified profile, your resume and the job posting.",
    ...overrides,
  };
}

function makeSavedAnswer(): ApplicationAnswer {
  return {
    id: "55555555-5555-4555-8555-555555555555",
    candidate_id: "66666666-6666-4666-8666-666666666666",
    question_key: "contact.phone",
    question_text: "What is your phone number?",
    answer: "+1 555 0100",
    answer_type: "text",
    verification_status: "verified",
    is_sensitive: false,
    is_legal: false,
    verified_at: "2026-11-01T09:00:00Z",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-02T09:00:00Z",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();
let draftResponse: AnswerDraft;
let savedAnswers: ApplicationAnswer[];

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
}

function installFetch() {
  fetchMock.mockImplementation(async (input, init) => {
    const path = pathOf(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (path === "applications") {
      return json({ total: 1, applications: [makeSummary()] });
    }
    if (path === `applications/${APP_ID}`) {
      return json(makePreview());
    }
    if (path === "assistant/answers/draft" && method === "POST") {
      return json(draftResponse);
    }
    if (path === `applications/${APP_ID}/questions/${QUESTION_ID}` && method === "PATCH") {
      return json(makePreview());
    }
    if (path === "profile/answers" && method === "GET") {
      return json(savedAnswers);
    }
    if (path.startsWith("profile/answers/") && method === "PUT") {
      return json({ ...makeSavedAnswer(), question_key: KEY });
    }
    return json({ detail: "Not mocked." }, 404);
  });
}

function renderAssistant() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AnswerAssistant />
    </QueryClientProvider>,
  );
}

async function selectQuestion() {
  await screen.findByLabelText("Question");
  fireEvent.change(screen.getByLabelText("Question"), {
    target: { value: QUESTION_ID },
  });
}

describe("AnswerAssistant", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    draftResponse = makeDraft();
    savedAnswers = [];
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("generates an evidence-backed draft and saves it to the application and answer bank", async () => {
    renderAssistant();
    await selectQuestion();

    fireEvent.click(screen.getByRole("button", { name: /generate answer/i }));

    expect(
      await screen.findByDisplayValue(
        /I build FastAPI services and want to work on Acme/,
      ),
    ).toBeTruthy();
    expect(screen.getByText("72% · medium confidence")).toBeTruthy();
    expect(screen.getByText("Experience: Backend Engineer at Acme")).toBeTruthy();
    expect(screen.getByText("Posting: Senior Backend Engineer")).toBeTruthy();
    expect(screen.getByText("Inferred, not stated")).toBeTruthy();
    expect(screen.getByText(/fake-answer-model/)).toBeTruthy();

    const generate = fetchMock.mock.calls.find(
      ([input, init]) =>
        pathOf(input) === "assistant/answers/draft" &&
        (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse(String(generate?.[1]?.body))).toEqual({
      application_id: APP_ID,
      question_id: QUESTION_ID,
    });

    fireEvent.click(
      screen.getByRole("button", { name: /save to application/i }),
    );
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) ===
            `applications/${APP_ID}/questions/${QUESTION_ID}` &&
          (init?.method ?? "GET") === "PATCH",
      );
      expect(patch).toBeTruthy();
      expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
        answer: draftResponse.draft_answer,
        confirmed: false,
      });
    });
    expect(
      await screen.findByText(/saved as a draft on the application/i),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: /save to reusable answers/i }),
    );
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input) === `profile/answers/${KEY}` &&
          (init?.method ?? "GET") === "PUT",
      );
      expect(put).toBeTruthy();
      const body = JSON.parse(String(put?.[1]?.body));
      expect(body.answer_source).toBe("ai_suggestion");
      expect(body.answer).toBe(draftResponse.draft_answer);
    });
  });

  it("shows missing-information warnings instead of inventing an answer", async () => {
    draftResponse = makeDraft({
      status: "insufficient_information",
      draft_answer: null,
      confidence: 0,
      confidence_label: "low",
      evidence: [],
      missing_information: [
        {
          field: "candidate_evidence",
          question: "What experience should this answer draw on?",
          reason: "Your profile and resume do not contain usable evidence.",
          sensitive: false,
        },
      ],
      save_reusable_allowed: false,
    });

    renderAssistant();
    await selectQuestion();
    fireEvent.click(screen.getByRole("button", { name: /generate answer/i }));

    expect(await screen.findByText("Information still needed")).toBeTruthy();
    expect(
      screen.getByText("What experience should this answer draw on?"),
    ).toBeTruthy();
    expect(
      (screen.getByLabelText("Draft answer") as HTMLTextAreaElement).value,
    ).toBe("");
    expect(
      (
        screen.getByRole("button", {
          name: /save to reusable answers/i,
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("reports conflicts and a provider failure without a draft", async () => {
    draftResponse = makeDraft({
      status: "unavailable",
      draft_answer: null,
      confidence: 0,
      confidence_label: "low",
      llm_status: "failed",
      llm_model: null,
      warnings: [
        "The language model did not return a usable draft. Your evidence is shown so you can answer directly.",
      ],
      conflicts: [
        {
          question_key: "contact.phone",
          question_text: "Phone number",
          category: "contact.phone",
          stored_answer: "+1 555 9999",
          conflicting_answer: "+1 555 0100",
          conflicting_source: "profile",
          reason: "Your verified profile now says something different.",
          resolution_options: [],
        },
      ],
      save_reusable_allowed: false,
    });

    renderAssistant();
    await selectQuestion();
    fireEvent.click(screen.getByRole("button", { name: /generate answer/i }));

    expect(await screen.findByText("Conflicting stored information")).toBeTruthy();
    expect(screen.getByText("Check before using")).toBeTruthy();
    expect(screen.getByText(/stored: \+1 555 9999/i)).toBeTruthy();
  });

  it("loads a reusable answer into the editor", async () => {
    savedAnswers = [makeSavedAnswer()];

    renderAssistant();

    // Wait for the default application to finish loading before editing.
    expect(await screen.findByText(/Using backend-strong.pdf/)).toBeTruthy();
    expect(await screen.findByText("What is your phone number?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /load into editor/i }));

    await waitFor(() => expect(screen.getByText("Review and edit")).toBeTruthy());
    expect(
      (screen.getByLabelText("Draft answer") as HTMLTextAreaElement).value,
    ).toBe("+1 555 0100");
    expect(screen.getByText("Reused answer")).toBeTruthy();
  });

  it("shows a safe error when the draft request fails", async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const path = pathOf(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (path === "applications") {
        return json({ total: 1, applications: [makeSummary()] });
      }
      if (path === `applications/${APP_ID}`) {
        return json(makePreview());
      }
      if (path === "profile/answers") {
        return json([]);
      }
      if (path === "assistant/answers/draft" && method === "POST") {
        return json({ detail: "Drafting is unavailable." }, 503);
      }
      return json({ detail: "Not mocked." }, 404);
    });

    renderAssistant();
    await selectQuestion();
    fireEvent.click(screen.getByRole("button", { name: /generate answer/i }));

    expect(await screen.findByText("Drafting is unavailable.")).toBeTruthy();
    expect(screen.queryByLabelText("Draft answer")).toBeNull();
  });
});
