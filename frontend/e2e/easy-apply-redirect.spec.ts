import { expect, test } from "@playwright/test";

import {
  APPLICATION_ID,
  JOB_ID,
  LISTING_HOST_PATH,
  SMARTAPPLY_HOST_PATH,
  STRONG,
  browserCapabilities,
  closeComparison,
  eligibilityReport,
  emptyFinalReview,
  emptyInterventions,
  installClientFixtures,
  makeJobBoardPreview,
  makeRedirect,
  makeRedirectSession,
  makeResume,
  makeWorkflow,
  signIn,
  validatedSmartApplyRedirect,
} from "./support";

const base = `applications/${APPLICATION_ID}`;
const sessionBase = `browser-sessions/by-application/${APPLICATION_ID}`;

function workspaceHandlers() {
  return {
    [`GET ${base}`]: () => makeJobBoardPreview(),
    [`GET ${base}/workflow`]: () => makeWorkflow(),
    [`GET jobs/${JOB_ID}/recommendation`]: () => closeComparison(),
    "GET resumes": () => [makeResume(STRONG, "backend-strong.pdf")],
    [`GET auto-apply/eligibility/${APPLICATION_ID}`]: () => eligibilityReport(),
    "GET browser-sessions/capabilities": () => browserCapabilities,
    [`GET ${sessionBase}`]: () => makeRedirectSession(),
    [`GET ${sessionBase}/interventions`]: () => emptyInterventions(),
    [`GET ${sessionBase}/final-review`]: () => emptyFinalReview(),
    [`GET ${sessionBase}/fill-plan`]: () => null,
    [`GET ${sessionBase}/fill`]: () => null,
  };
}

function submissionCalls(calls: Array<{ method: string; path: string }>) {
  return calls.filter(
    (call) =>
      call.method === "POST" && /(submit|approve|mark-submitted)/i.test(call.path),
  );
}

function detectCalls(calls: Array<{ method: string; path: string }>) {
  return calls.filter(
    (call) => call.method === "POST" && call.path === `${sessionBase}/detect-destination`,
  );
}

test.describe("Glassdoor Easy Apply → Indeed SmartApply hand-off", () => {
  test("validates the destination, analyses the form and never submits", async ({
    page,
  }) => {
    let detections = 0;
    const calls = await installClientFixtures(page, {
      ...workspaceHandlers(),
      [`POST ${sessionBase}/detect-destination`]: () => {
        detections += 1;
        // The first look happens while the user is still on the listing.
        return detections === 1
          ? makeRedirect()
          : validatedSmartApplyRedirect();
      },
      [`POST ${sessionBase}/form-analysis`]: () => ({ fields: [], warnings: [] }),
    });

    await signIn(page);
    await page.goto(`/${base}`);

    const panel = page.locator("#easy-apply-workflow");
    await expect(
      panel.getByRole("heading", { name: "Easy Apply hand-off" }),
    ).toBeVisible();

    // The listing is tracked separately from the form, and opening the page
    // does not create a browser session by itself.
    await expect(panel.getByText("Original job listing")).toBeVisible();
    await expect(panel.getByText(LISTING_HOST_PATH, { exact: true })).toBeVisible();
    await expect(panel.getByText("Not detected yet")).toBeVisible();
    expect(
      calls.some(
        (call) => call.method === "POST" && call.path === "browser-sessions",
      ),
    ).toBe(false);

    // Polling notices the hand-off without the user pressing anything.
    await expect(panel.getByText("Destination validated")).toBeVisible({
      timeout: 20_000,
    });
    await expect(panel.getByText(SMARTAPPLY_HOST_PATH, { exact: true })).toBeVisible();
    await expect(
      panel.getByText("Indeed SmartApply detected (resume selection)"),
    ).toBeVisible();
    // The original listing link is preserved after validation.
    await expect(panel.getByText(LISTING_HOST_PATH, { exact: true })).toBeVisible();

    const analyse = panel.getByRole("button", { name: "Analyze form", exact: true });
    await expect(analyse).toBeEnabled();
    await analyse.click();
    await expect(
      panel.getByText(
        "Form analysis stored. Review the detected controls before filling.",
      ),
    ).toBeVisible();
    expect(
      calls.filter(
        (call) => call.method === "POST" && call.path === `${sessionBase}/form-analysis`,
      ),
    ).toHaveLength(1);

    // Validation stops the polling loop instead of hammering the API.
    const settled = calls.length;
    await page.waitForTimeout(6_500);
    expect(calls.length).toBe(settled);
    expect(detectCalls(calls).length).toBeLessThanOrEqual(3);

    // The manual-submission boundary is stated and never crossed.
    await expect(
      panel.getByText("The final submission is always yours."),
    ).toBeVisible();
    expect(submissionCalls(calls)).toHaveLength(0);
  });

  test("analyses a validated non-Indeed form without a SmartApply warning", async ({
    page,
  }) => {
    let detections = 0;
    const calls = await installClientFixtures(page, {
      ...workspaceHandlers(),
      [`POST ${sessionBase}/detect-destination`]: () => {
        detections += 1;
        return detections === 1
          ? makeRedirect()
          : makeRedirect({
              status: "validated",
              message: "Greenhouse application form detected and validated.",
              destination_url: "https://boards.greenhouse.io/acme/jobs/123",
              application_platform: "greenhouse",
              platform_name: "Greenhouse",
              application_step: null,
              // SmartApply-only gate: it must not block a normal form.
              step_supported: false,
              destination_validated: true,
              awaiting_user_action: false,
              can_analyze: true,
            });
      },
      [`POST ${sessionBase}/form-analysis`]: () => ({ fields: [], warnings: [] }),
    });

    await signIn(page);
    await page.goto(`/${base}`);

    const panel = page.locator("#easy-apply-workflow");
    await expect(panel.getByText("Greenhouse detected").first()).toBeVisible({
      timeout: 20_000,
    });
    await expect(panel.getByText("Destination validated")).toBeVisible();
    await expect(
      panel.getByText("boards.greenhouse.io/acme/jobs/123", { exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByText("This SmartApply step is not supported yet"),
    ).toHaveCount(0);

    const analyse = panel.getByRole("button", { name: "Analyze form", exact: true });
    await expect(analyse).toBeEnabled();
    await analyse.click();
    await expect(
      panel.getByText(
        "Form analysis stored. Review the detected controls before filling.",
      ),
    ).toBeVisible();
    expect(submissionCalls(calls)).toHaveLength(0);
  });

  test("stops polling and shows an unsupported destination", async ({ page }) => {
    const calls = await installClientFixtures(page, {
      ...workspaceHandlers(),
      [`POST ${sessionBase}/detect-destination`]: () =>
        makeRedirect({
          status: "unsupported",
          message:
            "That destination is not a supported application platform. ApplyPilot did not follow or store it.",
          awaiting_user_action: false,
        }),
    });

    await signIn(page);
    await page.goto(`/${base}`);

    const panel = page.locator("#easy-apply-workflow");
    await expect(panel.getByText("Unsupported destination")).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      panel.getByText("Unsupported application destination"),
    ).toBeVisible();
    await expect(
      panel.getByText(
        "That destination is not a supported application platform. ApplyPilot did not follow or store it.",
      ),
    ).toBeVisible();
    // No form is offered for analysis and no destination is displayed.
    await expect(
      panel.getByRole("button", { name: "Analyze form", exact: true }),
    ).toBeDisabled();
    await expect(panel.getByText("Not detected yet")).toBeVisible();

    const settled = calls.length;
    await page.waitForTimeout(6_500);
    expect(calls.length).toBe(settled);
    expect(submissionCalls(calls)).toHaveLength(0);
  });

  test("reports Indeed sign-in as a manual step", async ({ page }) => {
    const calls = await installClientFixtures(page, {
      ...workspaceHandlers(),
      [`POST ${sessionBase}/detect-destination`]: () =>
        makeRedirect({
          message:
            "This application platform requires you to sign in. Complete the sign-in yourself in the live browser; ApplyPilot never enters passwords, one-time codes or MFA details.",
          authentication_required: true,
        }),
    });

    await signIn(page);
    await page.goto(`/${base}`);

    const panel = page.locator("#easy-apply-workflow");
    await expect(panel.getByText("Sign in to Indeed required")).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      panel.getByText(
        /ApplyPilot never enters passwords, one-time codes or MFA details/,
      ),
    ).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "Analyze form", exact: true }),
    ).toBeDisabled();
    // Nothing about a sign-in is submitted, typed or stored.
    expect(submissionCalls(calls)).toHaveLength(0);
    expect(
      calls.some((call) => /password|credential|otp|mfa/i.test(call.path)),
    ).toBe(false);
  });

  test("does not poll an expired session and offers a restart", async ({ page }) => {
    const calls = await installClientFixtures(page, {
      ...workspaceHandlers(),
      [`GET ${sessionBase}`]: () =>
        makeRedirectSession({
          browser_status: "expired",
          state: "expired",
          active: false,
          can_pause: false,
          can_cancel: false,
          current_url: null,
        }),
      [`POST ${sessionBase}/detect-destination`]: () => makeRedirect(),
    });

    await signIn(page);
    await page.goto(`/${base}`);

    const panel = page.locator("#easy-apply-workflow");
    await expect(panel.getByText("The browser session expired")).toBeVisible();
    await page.waitForTimeout(4_000);
    expect(detectCalls(calls)).toHaveLength(0);
    await expect(
      panel.getByRole("button", { name: "Analyze form", exact: true }),
    ).toBeDisabled();
  });

  test("creates one browser session per explicit start", async ({ page }) => {
    let started = 0;
    const calls = await installClientFixtures(page, {
      ...workspaceHandlers(),
      "GET browser-sessions/capabilities": () => ({
        ...browserCapabilities,
        view: {
          ...browserCapabilities.view,
          available: true,
          mode: "stream",
          user_can_interact: true,
          reason: "The live browser view is enabled for this test run.",
        },
      }),
      [`GET ${sessionBase}`]: () => null,
      "POST browser-sessions": () => {
        started += 1;
        return makeRedirectSession();
      },
      [`POST ${sessionBase}/view-ticket`]: () => ({
        browser_session_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ticket: "one-time-ticket",
        websocket_path: "/api/v1/browser-sessions/ws",
        expires_at: "2026-11-10T09:05:00Z",
        mode: "stream",
        frame_format: "jpeg",
        can_interact: true,
        note: "One view is attached.",
      }),
    });

    await signIn(page);
    await page.goto(`/${base}`);

    // Opening the workspace never creates a browser on its own.
    expect(started).toBe(0);

    await page.getByRole("button", { name: "Start live browser" }).click();
    await expect(
      page.getByRole("button", { name: "End browser session" }),
    ).toBeVisible({ timeout: 20_000 });

    // The panel opens before the ticket call resolves, so wait for both.
    await expect
      .poll(() => started, { timeout: 15_000 })
      .toBe(1);
    await expect
      .poll(
        () =>
          calls.filter(
            (call) => call.method === "POST" && call.path === "browser-sessions",
          ).length,
        { timeout: 15_000 },
      )
      .toBe(1);
    await expect
      .poll(
        () =>
          calls.filter(
            (call) =>
              call.method === "POST" &&
              call.path === `${sessionBase}/view-ticket`,
          ).length,
        { timeout: 15_000 },
      )
      .toBe(1);

    // No second browser is created while the view is open.
    await page.waitForTimeout(1_500);
    expect(started).toBe(1);
    expect(submissionCalls(calls)).toHaveLength(0);
  });
});
