// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationFillPanel } from "@/components/applications/application-fill-panel";
import type { FillExecutionResponse, FillPlanResponse } from "@/types/api";

/**
 * The preview table must be the backend fill plan, row for row. These tests
 * pin the plan-to-preview mapping: proposed values and provenance are shown for
 * every status, and filling is only started when the user clicks the button.
 */

const APPLICATION_ID = "11111111-1111-4111-8111-111111111111";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";

function planField(
  overrides: Partial<FillPlanResponse["fields"][number]> & {
    field_id: string;
    label: string;
    field_type: string;
    classification: string;
    required: boolean;
    action: FillPlanResponse["fields"][number]["action"];
    status: FillPlanResponse["fields"][number]["status"];
    proposed_value: string | null;
    source: string;
    confidence: number;
    options: string[];
    reason: string;
  },
): FillPlanResponse["fields"][number] {
  return {
    question_key: null,
    name: overrides.field_id,
    accessible_name: overrides.label,
    input_type: "text",
    visible: true,
    current_value: null,
    candidate_answer: null,
    provenance: {
      source: overrides.source,
      source_record_id: null,
      last_confirmed_at: null,
      confidence: overrides.confidence,
      stale: false,
      explanation: "",
    },
    verification_status:
      overrides.source === "verified_profile" ||
      overrides.source === "confirmed_reusable_answer"
        ? "verified"
        : overrides.source === "resume_suggestion"
          ? "derived"
          : "unverified",
    sensitivity: "public",
    fillable: overrides.status === "ready",
    requires_confirmation: overrides.status === "needs_confirmation",
    already_populated: overrides.status === "already_filled",
    conflicting: false,
    form_index: 0,
    page_url: "https://boards.greenhouse.io/acme/jobs/123",
    application_step: null,
    locator_ref: `field:${overrides.field_id}`,
    analyzed_at: "2026-11-13T09:00:00Z",
    duplicate_of: null,
    ...overrides,
  };
}

function fillPlan(): FillPlanResponse {
  return {
    application_id: APPLICATION_ID,
    analysis_id: "44444444-4444-4444-8444-444444444444",
    analysis_version: 2,
    browser_session_id: SESSION_ID,
    generated_at: "2026-11-13T09:00:00Z",
    page_url: "https://boards.greenhouse.io/acme/jobs/123",
    application_step: null,
    analyzed_at: "2026-11-13T09:00:00Z",
    structure_fingerprint: "b".repeat(64),
    approval_fingerprint: "a".repeat(64),
    total_fields: 4,
    eligible_count: 1,
    confirmation_count: 1,
    human_input_count: 1,
    already_filled_count: 1,
    skipped_count: 0,
    resume: {
      resume_id: "55555555-5555-4555-8555-555555555555",
      filename: "backend-strong.pdf",
      will_upload: true,
      file_field_ids: ["resume"],
      reason: "Best match for this posting.",
    },
    fields: [
      planField({
        field_id: "first_name",
        label: "First name",
        field_type: "text",
        classification: "personal_information",
        required: true,
        action: "fill",
        status: "ready",
        proposed_value: "Ada",
        source: "verified_profile",
        confidence: 0.98,
        options: [],
        reason: "Verified information is ready to fill.",
      }),
      planField({
        field_id: "skills",
        label: "Skills",
        field_type: "textarea",
        classification: "skills",
        required: false,
        action: "fill",
        status: "needs_confirmation",
        proposed_value: "Python, FastAPI, PostgreSQL",
        source: "resume_suggestion",
        confidence: 0.72,
        options: [],
        reason: "Resume-derived information must be confirmed before use.",
      }),
      planField({
        field_id: "work_authorization",
        label: "Work authorization",
        field_type: "select",
        classification: "legal_declaration",
        required: true,
        action: "none",
        status: "needs_human_input",
        proposed_value: null,
        source: "none",
        confidence: 0.9,
        options: ["Yes", "No"],
        reason: "Legal declarations are always answered by you.",
        sensitivity: "legal",
        requires_confirmation: true,
      }),
      planField({
        field_id: "email",
        label: "Email",
        field_type: "email",
        classification: "contact_information",
        required: true,
        action: "none",
        status: "already_filled",
        proposed_value: null,
        source: "none",
        confidence: 0.99,
        options: [],
        reason: "The field already has a value; it was not overwritten.",
        current_value: "typed@example.com",
        already_populated: true,
      }),
    ],
    warnings: [],
    executable: true,
    final_submission_note:
      "ApplyPilot never clicks Submit, Apply or Finish. The final review and submission remain under your control.",
  };
}

function fillExecution(): FillExecutionResponse {
  return {
    execution_id: "33333333-3333-4333-8333-333333333333",
    application_id: APPLICATION_ID,
    analysis_id: "44444444-4444-4444-8444-444444444444",
    analysis_version: 2,
    browser_session_id: SESSION_ID,
    run_number: 1,
    status: "planned",
    total_fields: 4,
    ready_count: 1,
    succeeded_count: 0,
    failed_count: 0,
    skipped_count: 2,
    verified_count: 0,
    pending_confirmation_count: 1,
    pending_human_input_count: 1,
    selected_resume_id: null,
    selected_resume_filename: null,
    results: [],
    warnings: [],
    pause_reason: null,
    failure_code: null,
    failure_reason: null,
    can_pause: false,
    can_resume: false,
    can_cancel: true,
    started_at: null,
    paused_at: null,
    completed_at: null,
    last_activity_at: null,
    final_submission_note:
      "ApplyPilot never clicks the final Submit/Apply button.",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();
let fillRequests = 0;
let fillBody: unknown = null;

function installFetch() {
  fillRequests = 0;
  fillBody = null;
  fetchMock.mockImplementation(async (input, init) => {
    const path = String(input).replace(/^.*\/api\/backend\//, "");
    const method = (init?.method ?? "GET").toUpperCase();

    if (path === `browser-sessions/by-application/${APPLICATION_ID}/fill-plan`) {
      return json(fillPlan());
    }
    if (path === `browser-sessions/by-application/${APPLICATION_ID}/fill`) {
      if (method === "POST") {
        fillRequests += 1;
        fillBody = init?.body ? JSON.parse(String(init.body)) : null;
      }
      return json(fillExecution());
    }
    return json({ detail: `Unhandled test route: ${path}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApplicationFillPanel applicationId={APPLICATION_ID} />
    </QueryClientProvider>,
  );
}

describe("ApplicationFillPanel preview", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the backend plan as field, value, source and status rows", async () => {
    renderPanel();

    expect(await screen.findByText("Fill preview")).toBeTruthy();
    for (const heading of ["Field", "Proposed value", "Source", "Status"]) {
      expect(
        screen.getByRole("columnheader", { name: heading }),
      ).toBeTruthy();
    }

    // A ready field shows its verified value and provenance.
    expect(screen.getByText("Ada")).toBeTruthy();
    expect(screen.getByText("Verified profile")).toBeTruthy();
    expect(screen.getByText("Ready to fill")).toBeTruthy();

    // A field needing confirmation still shows the proposed value: hiding it
    // would misrepresent the plan the user is approving.
    expect(screen.getByText("Python, FastAPI, PostgreSQL")).toBeTruthy();
    expect(screen.getByText("Resume suggestion")).toBeTruthy();
    expect(screen.getByText("Needs confirmation")).toBeTruthy();

    // A missing value is labelled as missing, never invented.
    const legalRow = screen.getByText("Work authorization").closest("tr");
    expect(legalRow).not.toBeNull();
    expect(within(legalRow as HTMLTableRowElement).getByText("Not available"))
      .toBeTruthy();
    expect(
      within(legalRow as HTMLTableRowElement).getByText("Your input required"),
    ).toBeTruthy();

    // An existing user value is shown and reported as untouched.
    expect(screen.getByText("Left unchanged")).toBeTruthy();
    expect(screen.getByText(/typed@example.com · kept as-is/)).toBeTruthy();

    // Provenance and safety classes are visible, not inferred by the client.
    expect(screen.getByText("Verified")).toBeTruthy();
    expect(screen.getByText("Derived · confirm")).toBeTruthy();
    expect(screen.getByText("Legal · manual")).toBeTruthy();
    expect(screen.getByText(/plan fingerprint/i)).toBeTruthy();
  });

  it("starts a safe fill only after the user approves, and never submits", async () => {
    renderPanel();

    const approve = await screen.findByRole("button", {
      name: /approve & fill these fields/i,
    });
    await waitFor(() =>
      expect((approve as HTMLButtonElement).disabled).toBe(false),
    );
    expect(fillRequests).toBe(0);

    fireEvent.click(approve);

    await waitFor(() => expect(fillRequests).toBe(1));
    expect(fillBody).toEqual({
      approval_fingerprint: "a".repeat(64),
      restart: false,
    });
    expect(
      screen.getByText(/ApplyPilot never clicks Submit, Apply or Finish/i),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /^(submit|apply|finish)/i }),
    ).toBeNull();
  });
});
