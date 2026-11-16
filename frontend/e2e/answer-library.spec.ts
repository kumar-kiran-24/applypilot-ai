import { expect, test } from "@playwright/test";

import {
  APPLICATION_ID,
  JOB_ID,
  STRONG,
  WEAK,
  browserCapabilities,
  closeComparison,
  eligibilityReport,
  emptyFinalReview,
  emptyInterventions,
  installClientFixtures,
  makePreview,
  makeResume,
  makeWorkflow,
  signIn,
} from "./support";

const QUESTION_ID = "44444444-4444-4444-8444-444444444444";
const ANSWER_TEXT = "I am a backend engineer who builds reliable services.";

function savedAnswer(overrides: Record<string, unknown> = {}) {
  return {
    id: "55555555-5555-4555-8555-555555555555",
    candidate_id: "66666666-6666-4666-8666-666666666666",
    question_key: "custom.intro",
    question_text: "Tell us about yourself.",
    answer: ANSWER_TEXT,
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

function knowledge() {
  return {
    total_answers: 1,
    verified_answers: 1,
    needs_review: 0,
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
    conflicts: [],
    outdated: [],
    unverified: [],
    summary: "1 of 1 saved answers are confirmed; nothing else needs review.",
    disclaimer:
      "This report never changes verified information and never fills a missing detail with an assumption.",
  };
}

function detail(answer: ReturnType<typeof savedAnswer>) {
  return {
    answer,
    evidence: [
      {
        id: "answer:custom.intro",
        kind: "answer",
        label: "Saved answer",
        detail: ANSWER_TEXT,
        source: "application_answer:custom.intro",
        verified: true,
      },
      {
        id: "source:custom.intro",
        kind: "application",
        label: "Recorded source",
        detail: "application:demo:question:custom.intro",
        source: "previously_approved",
        verified: false,
      },
    ],
    conflicts: [],
  };
}

function previewWithQuestion() {
  const preview = makePreview();
  return {
    ...preview,
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
    progress: {
      total: 1,
      confirmed: 0,
      required_total: 1,
      required_confirmed: 0,
      percent: 0,
    },
  };
}

function suggestion() {
  return {
    application_id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    question_key: "company.cover_letter",
    question_text: "Why do you want to work here?",
    suggestions: [
      {
        question_key: "custom.intro",
        question_text: "Tell us about yourself.",
        answer: ANSWER_TEXT,
        answer_source: "previously_approved",
        verification_status: "verified",
        category: "personal_introduction",
        category_label: "Personal introduction",
        trust_level: "user_reviewed",
        trust_label: "User-reviewed answer",
        trust_reason: "You reviewed and confirmed this answer.",
        relevance: 0.7,
        reason: "Wording overlaps strongly with this question.",
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

test.describe("Answer library", () => {
  test("searches, edits, re-confirms and shows evidence without changing facts", async ({
    page,
  }) => {
    let answers = [savedAnswer()];
    const calls = await installClientFixtures(page, {
      "GET profile/answers": () => answers,
      "GET profile/answers/categories": () => ({
        total: answers.length,
        categories: [
          {
            category: "personal_introduction",
            label: "Personal introduction",
            count: 1,
          },
        ],
        trust_levels: { user_reviewed: 1 },
      }),
      "GET profile/answers/knowledge": () => knowledge(),
      "GET profile/answers/custom.intro": () => detail(answers[0]!),
      "PUT profile/answers/custom.intro": (body: unknown) => {
        const payload = body as {
          answer?: string;
          verification_status?: string;
        };
        const verified = payload.verification_status === "verified";
        answers = [
          savedAnswer({
            answer: payload.answer ?? ANSWER_TEXT,
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
          }),
        ];
        return answers[0];
      },
    });

    await signIn(page);
    await page.goto("/answers");

    await expect(
      page.getByRole("heading", { name: "Answers & knowledge" }),
    ).toBeVisible();
    await expect(page.getByText("Candidate knowledge center")).toBeVisible();
    await expect(page.getByText("Tell us about yourself.")).toBeVisible();
    await expect(
      page.locator("span", { hasText: /^Personal introduction$/ }).first(),
    ).toBeVisible();

    const listCallsBefore = calls.filter(
      (call) => call.method === "GET" && call.path === "profile/answers",
    ).length;
    await page.getByLabel("Search saved answers").fill("backend");
    await expect
      .poll(
        () =>
          calls.filter(
            (call) =>
              call.method === "GET" && call.path === "profile/answers",
          ).length,
      )
      .toBeGreaterThan(listCallsBefore);

    await page.getByLabel("Filter by trust level").selectOption("user_reviewed");

    await page.getByRole("button", { name: "Edit" }).first().click();
    await page
      .getByLabel("Edit answer: Tell us about yourself.")
      .fill("I am a platform engineer.");
    await page.getByRole("button", { name: "Save changes" }).click();

    await expect(page.getByText(/changed wording is unverified/i)).toBeVisible();
    await expect(
      page
        .locator("span", { hasText: /^User-written, not yet reviewed$/ })
        .first(),
    ).toBeVisible();
    const editCall = calls.find(
      (call) =>
        call.method === "PUT" &&
        call.path === "profile/answers/custom.intro",
    );
    expect(editCall?.body).toMatchObject({
      answer: "I am a platform engineer.",
      question_text: "Tell us about yourself.",
    });
    expect(
      (editCall?.body as { verification_status?: string }).verification_status,
    ).toBeUndefined();

    await page.getByRole("button", { name: /confirm answer/i }).first().click();
    await expect(
      page.locator("span", { hasText: /^User-reviewed answer$/ }).first(),
    ).toBeVisible();
    const confirmCall = calls
      .filter(
        (call) =>
          call.method === "PUT" &&
          call.path === "profile/answers/custom.intro",
      )
      .at(-1);
    expect(confirmCall?.body).toMatchObject({ verification_status: "verified" });

    await page.getByRole("button", { name: "Details" }).first().click();
    await expect(page.getByText("Evidence and source")).toBeVisible();
    await expect(page.getByText("Recorded source")).toBeVisible();
    await expect(page.getByText(/never fills a missing detail/i)).toBeVisible();

    expect(
      calls.some((call) => /approve|submit|mark-submitted/i.test(call.path)),
    ).toBe(false);
  });

  test("reuses a saved answer into an application question as a draft", async ({
    page,
  }) => {
    const base = `applications/${APPLICATION_ID}`;
    const calls = await installClientFixtures(page, {
      [`GET ${base}`]: () => previewWithQuestion(),
      [`GET ${base}/workflow`]: () => makeWorkflow(),
      [`GET jobs/${JOB_ID}/recommendation`]: () => closeComparison(),
      "GET resumes": () => [
        makeResume(STRONG, "backend-strong.pdf"),
        makeResume(WEAK, "backend-weak.pdf"),
      ],
      [`GET ${base}/questions/${QUESTION_ID}/reusable-answers`]: () =>
        suggestion(),
      [`PATCH ${base}/questions/${QUESTION_ID}`]: () => previewWithQuestion(),
      [`GET auto-apply/eligibility/${APPLICATION_ID}`]: () => eligibilityReport(),
      "GET browser-sessions/capabilities": () => browserCapabilities,
      [`GET browser-sessions/by-application/${APPLICATION_ID}`]: () => null,
      [`GET browser-sessions/by-application/${APPLICATION_ID}/interventions`]:
        () => emptyInterventions(),
      [`GET browser-sessions/by-application/${APPLICATION_ID}/final-review`]:
        () => emptyFinalReview(),
      [`GET browser-sessions/by-application/${APPLICATION_ID}/fill-plan`]:
        () => null,
      [`GET browser-sessions/by-application/${APPLICATION_ID}/fill`]: () => null,
    });

    await signIn(page);
    await page.goto(`/applications/${APPLICATION_ID}`);

    await expect(page.getByLabel("Why do you want to work here?")).toBeVisible();
    await page.getByRole("button", { name: "Reuse saved answer" }).click();

    await expect(page.getByText("Reuse a saved answer")).toBeVisible();
    await expect(page.getByText("Tell us about yourself.")).toBeVisible();
    await expect(page.getByText("70% match")).toBeVisible();

    await page.getByRole("button", { name: "Use this answer" }).click();
    await expect(
      page.getByLabel("Why do you want to work here?"),
    ).toHaveValue(ANSWER_TEXT);
    await expect(
      page.getByText(/inserted from your saved answers/i),
    ).toBeVisible();

    // Inserting is local; saving is still an explicit user action.
    expect(
      calls.some(
        (call) =>
          call.method === "PATCH" &&
          call.path === `${base}/questions/${QUESTION_ID}`,
      ),
    ).toBe(false);

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect
      .poll(
        () =>
          calls.filter(
            (call) =>
              call.method === "PATCH" &&
              call.path === `${base}/questions/${QUESTION_ID}`,
          ).length,
      )
      .toBe(1);
    const saveCall = calls.find(
      (call) =>
        call.method === "PATCH" &&
        call.path === `${base}/questions/${QUESTION_ID}`,
    );
    expect(saveCall?.body).toMatchObject({
      answer: ANSWER_TEXT,
      confirmed: false,
      reused_answer_key: "custom.intro",
    });
    expect(
      calls.some((call) => /approve|submit|mark-submitted/i.test(call.path)),
    ).toBe(false);
  });
});
