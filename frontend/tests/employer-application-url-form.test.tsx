// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EmployerApplicationUrlForm } from "@/components/applications/employer-application-url-form";
import type { ApplicationPreview } from "@/types/api";

const APPLICATION_ID = "11111111-1111-4111-8111-111111111111";
const LISTING = "https://www.glassdoor.com/job-listing/backend-JV_1.htm";
const FORM = "https://boards.greenhouse.io/acme/jobs/123";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function preview(overrides: Partial<ApplicationPreview> = {}): ApplicationPreview {
  return {
    application_id: APPLICATION_ID,
    job_id: "22222222-2222-4222-8222-222222222222",
    job_title: "Senior Engineer",
    company: "Acme",
    platform: "greenhouse",
    platform_name: "Greenhouse",
    application_url: FORM,
    job_listing_url: LISTING,
    employer_careers_url: "https://acme.example/careers",
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
    resume: { id: null, filename: null, reason: "", score: null },
    sections: [],
    missing_required: [],
    progress: {
      total: 0,
      confirmed: 0,
      required_total: 0,
      required_confirmed: 0,
      percent: 0,
    },
    ready_to_approve: false,
    can_submit: false,
    submission_mode: "manual",
    manual_steps: [],
    warnings: [],
    ...overrides,
  };
}

const fetchMock = vi.fn<typeof fetch>();

function installFetch(response: () => Response) {
  fetchMock.mockImplementation(async (input) => {
    const path = String(input).replace(/^.*\/api\/backend\//, "");
    if (path === `applications/${APPLICATION_ID}/inspect`) return response();
    return json({ detail: `Unhandled test route: ${path}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function renderForm(
  overrides: {
    isJobBoard?: boolean;
    initialApplicationUrl?: string;
    suggestedApplicationUrl?: string | null;
    onSaved?: (application: ApplicationPreview) => void;
  } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EmployerApplicationUrlForm
        applicationId={APPLICATION_ID}
        initialApplicationUrl={overrides.initialApplicationUrl ?? FORM}
        jobListingUrl={LISTING}
        employerCareersUrl="https://acme.example/careers"
        suggestedApplicationUrl={overrides.suggestedApplicationUrl ?? null}
        isJobBoard={overrides.isJobBoard ?? false}
        onSaved={overrides.onSaved}
      />
    </QueryClientProvider>,
  );
}

describe("EmployerApplicationUrlForm", () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("submits an accepted employer URL and refreshes the parent data", async () => {
    const saved = vi.fn();
    installFetch(() =>
      json(
        preview({
          application_url: FORM,
          application_url_is_job_board: false,
        }),
      ),
    );
    renderForm({ onSaved: saved });

    const input = screen.getByLabelText("Employer application URL");
    fireEvent.change(input, { target: { value: FORM } });
    fireEvent.click(screen.getByRole("button", { name: /save employer url/i }));

    await screen.findByText(/employer url saved/i);
    expect(saved).toHaveBeenCalledTimes(1);
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body).toEqual({
      application_url: FORM,
      employer_careers_url: "https://acme.example/careers",
    });
  });

  it("shows the backend's specific job-board rejection", async () => {
    installFetch(() =>
      json(
        {
          detail:
            "This link points to a job board. Add the employer's own application URL before starting a browser session.",
          code: "job_board_url_not_supported",
        },
        400,
      ),
    );
    const saved = vi.fn();
    renderForm({
      isJobBoard: true,
      initialApplicationUrl: LISTING,
      onSaved: saved,
    });

    // The listing is never pre-filled as if it were the application form.
    const input = screen.getByLabelText(
      "Employer application URL",
    ) as HTMLInputElement;
    expect(input.value).toBe("");
    expect(screen.getByText(/add the employer's application url/i)).toBeTruthy();

    fireEvent.change(input, { target: { value: LISTING } });
    fireEvent.click(
      screen.getByRole("button", { name: /save employer url/i }),
    );

    expect(
      await screen.findByText(/this link points to a job board/i),
    ).toBeTruthy();
    expect(saved).not.toHaveBeenCalled();
  });

  it("displays invalid-URL validation details instead of a generic 400", async () => {
    installFetch(() =>
      json(
        {
          detail: "The application URL must point to a public website.",
          code: "unsafe_application_url",
        },
        400,
      ),
    );
    renderForm();

    fireEvent.change(screen.getByLabelText("Employer application URL"), {
      target: { value: "http://127.0.0.1/apply" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /save employer url/i }),
    );

    expect(
      await screen.findByText(/must point to a public website/i),
    ).toBeTruthy();
  });

  it("reports an ownership failure and only uses a suggestion on request", async () => {
    installFetch(() =>
      json({ detail: "Application not found.", code: "application_not_found" }, 404),
    );
    renderForm({
      isJobBoard: true,
      initialApplicationUrl: LISTING,
      suggestedApplicationUrl: "https://acme.example/careers/apply?job=42",
    });

    const input = screen.getByLabelText(
      "Employer application URL",
    ) as HTMLInputElement;
    expect(input.value).toBe("");

    fireEvent.click(screen.getByRole("button", { name: /use suggestion/i }));
    expect(input.value).toBe("https://acme.example/careers/apply?job=42");
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", { name: /save employer url/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/application not found/i)).toBeTruthy(),
    );
  });
});
