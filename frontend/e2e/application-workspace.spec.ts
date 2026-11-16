import { expect, test } from "@playwright/test";

import {
  APPLICATION_ID,
  JOB_ID,
  MANUAL_REASON,
  STRONG,
  WEAK,
  browserCapabilities,
  closeComparison,
  eligibilityReport,
  emptyFinalReview,
  emptyInterventions,
  httpError,
  installClientFixtures,
  makePreview,
  makeResume,
  makeWorkflow,
  signIn,
} from "./support";

const base = `applications/${APPLICATION_ID}`;

function workspaceHandlers(
  getManual: () => boolean,
  setManual: (value: boolean) => void,
) {
  return {
    [`GET ${base}`]: () => makePreview(getManual()),
    [`GET ${base}/workflow`]: () => makeWorkflow(),
    [`GET jobs/${JOB_ID}/recommendation`]: () => closeComparison(),
    "GET resumes": () => [
      makeResume(STRONG, "backend-strong.pdf"),
      makeResume(WEAK, "backend-weak.pdf"),
    ],
    [`PATCH ${base}/resume`]: (body: unknown) => {
      expect(body).toEqual({ resume_id: WEAK });
      setManual(true);
      return makePreview(true);
    },
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
  };
}

test.describe("application workspace", () => {
  test("keeps an explicit resume choice across reload without approving", async ({
    page,
  }) => {
    let manual = false;
    const calls = await installClientFixtures(
      page,
      workspaceHandlers(
        () => manual,
        (value) => {
          manual = value;
        },
      ),
    );

    await signIn(page);
    await page.goto(`/applications/${APPLICATION_ID}`);

    await expect(
      page.getByRole("heading", { name: "Senior Backend Engineer" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Resume for this application" }),
    ).toBeVisible();
    await expect(
      page.getByText("This is the recommended resume for the job."),
    ).toBeVisible();
    await expect(
      page.getByText("82.5% compatibility", { exact: true }),
    ).toBeVisible();

    // Opening the workspace never changes the resume by itself.
    expect(
      calls.some(
        (call) =>
          call.method === "PATCH" && call.path === `${base}/resume`,
      ),
    ).toBe(false);

    await page.getByLabel("Choose a resume").selectOption(WEAK);
    await page.getByRole("button", { name: "Use this resume" }).click();

    await expect(page.getByText("Selected by you")).toBeVisible();
    await expect(page.getByText("Your selection is kept")).toBeVisible();
    await expect(page.getByText(MANUAL_REASON)).toBeVisible();
    await expect(page.getByLabel("Choose a resume")).toHaveValue(WEAK);

    expect(
      calls.some(
        (call) =>
          call.method === "PATCH" &&
          call.path === `${base}/resume` &&
          JSON.stringify(call.body) === JSON.stringify({ resume_id: WEAK }),
      ),
    ).toBe(true);

    // A fresh page load reads the persisted selection and must not replace it
    // with the recommendation.
    const callsBeforeReload = calls.length;
    await page.reload();
    await expect(page.getByText("Selected by you")).toBeVisible();
    await expect(page.getByText(MANUAL_REASON)).toBeVisible();
    await expect(page.getByLabel("Choose a resume")).toHaveValue(WEAK);
    await expect(
      page.getByText("This is the recommended resume for the job."),
    ).toHaveCount(0);
    expect(
      calls
        .slice(callsBeforeReload)
        .some((call) => call.method === "PATCH"),
    ).toBe(false);
    expect(calls.some((call) => /approve|submit/i.test(call.path))).toBe(false);
  });

  test("shows a safe error when the application cannot be loaded", async ({
    page,
  }) => {
    await signIn(page);
    await installClientFixtures(page, {
      [`GET ${base}`]: () =>
        httpError(404, "Application not found.", "application_not_found"),
    });

    await page.goto(`/applications/${APPLICATION_ID}`);

    await expect(
      page.getByText(/could not load this application/i),
    ).toBeVisible();
    await expect(page.getByText("Application not found.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

    const text = await page.locator("body").innerText();
    expect(text).not.toMatch(/traceback|storage_key|storage_backend|checksum/i);
    expect(text).not.toMatch(/submitted successfully/i);
  });
});
