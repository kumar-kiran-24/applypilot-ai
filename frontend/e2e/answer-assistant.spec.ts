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
const DRAFT = "I build FastAPI services and want to work on Acme's backend.";
const base = `applications/${APPLICATION_ID}`;

const summary = {
  id: APPLICATION_ID,
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
    missing_required: [
      {
        question_key: "company.cover_letter",
        question_text: "Why do you want to work here?",
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

function draft(overrides: Record<string, unknown> = {}) {
  return {
    application_id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Senior Backend Engineer",
    company: "Acme",
    question_id: QUESTION_ID,
    question_key: "company.cover_letter",
    question_text: "Why do you want to work here?",
    question_type: "textarea",
    answer_type: "text",
    is_sensitive: false,
    is_legal: false,
    status: "drafted",
    draft_answer: DRAFT,
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
    ],
    missing_information: [],
    conflicts: [],
    inferred_claims: [],
    warnings: [],
    save_reusable_allowed: true,
    llm_status: "used",
    llm_model: "openai/gpt-oss-120b",
    disclaimer:
      "This draft only uses evidence from your verified profile, your resume and the job posting.",
    ...overrides,
  };
}

test.describe("AI answer assistant", () => {
  test("drafts with evidence, edits, saves and reuses without submitting", async ({
    page,
  }) => {
    const calls = await installClientFixtures(page, {
      "GET applications": () => ({ total: 1, applications: [summary] }),
      [`GET ${base}`]: () => previewWithQuestion(),
      "GET profile/answers": () => [],
      "POST assistant/answers/draft": () => draft(),
      [`PATCH ${base}/questions/${QUESTION_ID}`]: (body: unknown) => {
        expect(body).toMatchObject({ confirmed: false });
        return previewWithQuestion();
      },
      "PUT profile/answers/company.cover_letter": (body: unknown) => {
        expect(body).toMatchObject({ answer_source: "ai_suggestion" });
        return {
          id: "77777777-7777-4777-8777-777777777777",
          candidate_id: "88888888-8888-4888-8888-888888888888",
          question_key: "company.cover_letter",
          question_text: "Why do you want to work here?",
          answer: DRAFT,
          answer_type: "text",
          verification_status: "unknown",
          is_sensitive: false,
          is_legal: false,
          verified_at: null,
          created_at: "2026-11-12T09:00:00Z",
          updated_at: "2026-11-12T09:00:00Z",
        };
      },
    });

    await signIn(page);
    await page.goto("/assistant");

    await expect(
      page.getByRole("heading", { name: "Draft an answer" }),
    ).toBeVisible();
    await page.getByLabel("Question", { exact: true }).selectOption(QUESTION_ID);
    await page.getByRole("button", { name: "Generate answer" }).click();

    await expect(page.getByLabel("Draft answer")).toHaveValue(DRAFT);
    await expect(page.getByText("72% · medium confidence")).toBeVisible();
    await expect(
      page.getByText("Experience: Backend Engineer at Acme"),
    ).toBeVisible();
    await expect(page.getByText(/openai\/gpt-oss-120b/)).toBeVisible();

    // The draft is editable before anything is persisted.
    await page.getByLabel("Draft answer").fill(`${DRAFT} Edited.`);
    await page.getByRole("button", { name: "Save to application" }).click();
    await expect(
      page.getByText(/saved as a draft on the application/i),
    ).toBeVisible();
    await expect(
      page.getByText(/saved to reusable answers/i),
    ).toHaveCount(0);

    await page.getByRole("button", { name: "Save to reusable answers" }).click();
    await expect(page.getByText(/saved to reusable answers/i)).toBeVisible();

    const draftCall = calls.find(
      (call) => call.method === "POST" && call.path === "assistant/answers/draft",
    );
    expect(draftCall?.body).toMatchObject({ question_id: QUESTION_ID });
    expect(
      calls.some(
        (call) =>
          call.method === "PATCH" &&
          call.path === `${base}/questions/${QUESTION_ID}`,
      ),
    ).toBe(true);
    expect(
      calls.some((call) => /approve|submit|mark-submitted/i.test(call.path)),
    ).toBe(false);
  });

  test("inserts a draft into the application question editor without confirming it", async ({
    page,
  }) => {
    const calls = await installClientFixtures(page, {
      [`GET ${base}`]: () => previewWithQuestion(),
      [`GET ${base}/workflow`]: () => makeWorkflow(),
      [`GET jobs/${JOB_ID}/recommendation`]: () => closeComparison(),
      "GET resumes": () => [
        makeResume(STRONG, "backend-strong.pdf"),
        makeResume(WEAK, "backend-weak.pdf"),
      ],
      "POST assistant/answers/draft": () => draft(),
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
    await page.getByRole("button", { name: "Draft with AI" }).click();

    await expect(
      page.getByText(/draft inserted \(medium confidence/i),
    ).toBeVisible();
    await expect(page.getByLabel("Why do you want to work here?")).toHaveValue(
      DRAFT,
    );
    // Inserting is local only; the user still confirms through the editor.
    expect(
      calls.some(
        (call) =>
          call.method === "PATCH" &&
          call.path === `${base}/questions/${QUESTION_ID}`,
      ),
    ).toBe(false);

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect
      .poll(() =>
        calls.filter(
          (call) =>
            call.method === "PATCH" &&
            call.path === `${base}/questions/${QUESTION_ID}`,
        ).length,
      )
      .toBe(1);
    expect(
      calls.some((call) => /approve|submit|mark-submitted/i.test(call.path)),
    ).toBe(false);
  });
});
