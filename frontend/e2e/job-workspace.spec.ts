import { expect, test } from "@playwright/test";

import {
  APPLICATION_ID,
  JOB_ID,
  installClientFixtures,
  jobIntelligence,
  makeHandlers,
  makeJob,
  makePreview,
  resumeComparison,
  signIn,
} from "./support";

/**
 * The Phase 2I.1 journey in a real browser: list and filter stored jobs, open a
 * job workspace, read the analysis and resume recommendation, save it and start
 * an application. All data is fulfilled at the browser boundary, so no Google,
 * Gmail or employer call is made.
 */
test.describe("job workspace journey", () => {
  test("filters jobs, opens a workspace, saves it and starts an application", async ({
    page,
  }) => {
    let saved = false;

    const calls = await installClientFixtures(
      page,
      makeHandlers({
        [`GET jobs/${JOB_ID}`]: () => ({
          ...makeJob(),
          user_state: {
            saved,
            dismissed: false,
            saved_at: saved ? "2026-11-12T10:00:00Z" : null,
            dismissed_at: null,
          },
        }),
        [`GET jobs/${JOB_ID}/intelligence`]: () => jobIntelligence(),
        [`GET jobs/${JOB_ID}/recommendation`]: () => resumeComparison(),
        "GET applications": () => ({ total: 0, applications: [] }),
        [`POST jobs/${JOB_ID}/save`]: () => {
          saved = true;
          return {
            ...makeJob(),
            user_state: {
              saved: true,
              dismissed: false,
              saved_at: "2026-11-12T10:00:00Z",
              dismissed_at: null,
            },
          };
        },
        "POST applications/analyze": () => makePreview(),
      }),
    );

    await signIn(page);
    await page.goto("/jobs");

    await expect(
      page.getByRole("heading", { name: "Job workspace" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Senior Backend Engineer" }),
    ).toBeVisible();

    // Search and filters are sent through the BFF.
    await page.getByLabel("Search jobs").fill("backend");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await page.getByRole("button", { name: "Filters" }).click();
    await page.getByLabel("Company").fill("Acme");

    await expect(
      page.getByRole("heading", { name: "Senior Backend Engineer" }),
    ).toBeVisible();
    expect(calls.some((call) => call.path === "jobs")).toBe(true);

    // Open the workspace from the listing.
    await page
      .getByRole("link", { name: "Senior Backend Engineer" })
      .first()
      .click();

    await page.waitForURL(`**/jobs/${JOB_ID}`);
    await expect(
      page.getByText("Build Python services with FastAPI and PostgreSQL."),
    ).toBeVisible();
    await expect(page.getByText("Requirements at a glance")).toBeVisible();
    await expect(page.getByText("Job intelligence")).toBeVisible();
    await expect(
      page.getByText("Recommended resume", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Important gaps")).toBeVisible();

    // Saving is an explicit per-user decision through the state endpoint.
    await page.getByRole("button", { name: "Save job" }).click();
    await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();
    expect(
      calls.some(
        (call) => call.method === "POST" && call.path === `jobs/${JOB_ID}/save`,
      ),
    ).toBe(true);

    // Starting an application prepares the workflow; nothing is submitted.
    await page.getByRole("button", { name: "Start application" }).click();
    await page.waitForURL(`**/applications/${APPLICATION_ID}`);
    expect(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.path === "applications/analyze" &&
          JSON.stringify(call.body) === JSON.stringify({ job_id: JOB_ID }),
      ),
    ).toBe(true);
  });
});

test.describe("dashboard application pipeline", () => {
  test("shows stored counts and stages and opens a recent application", async ({
    page,
  }) => {
    await signIn(page);
    await installClientFixtures(
      page,
      makeHandlers({
        "GET applications/pipeline": () => ({
          total: 7,
          in_progress: 2,
          submitted: 3,
          failed: 1,
          cancelled: 1,
          by_stage: {
            applied: 1,
            screening: 0,
            interview: 1,
            offer: 0,
            rejected: 1,
            withdrawn: 0,
          },
          recent: [
            {
              id: APPLICATION_ID,
              job_id: JOB_ID,
              job_title: "Senior Backend Engineer",
              company: "Acme",
              platform: "greenhouse",
              platform_name: "Greenhouse",
              application_url: "https://boards.greenhouse.io/acme/jobs/1",
              status: "submitted",
              stage: "interview",
              automation_mode: "assisted",
              requires_account: false,
              resume_filename: "backend-strong.pdf",
              questions_total: 4,
              questions_confirmed: 4,
              missing_required_count: 0,
              created_at: "2026-11-10T09:00:00Z",
              updated_at: "2026-11-11T09:00:00Z",
              approved_at: "2026-11-11T08:00:00Z",
              submitted_at: "2026-11-11T09:00:00Z",
            },
          ],
        }),
      }),
    );

    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Application pipeline" }),
    ).toBeVisible();
    await expect(page.getByText("7 total")).toBeVisible();
    await expect(page.getByText("Interview · 1")).toBeVisible();
    await expect(page.getByText("Withdrawn · 0")).toBeVisible();

    await page.locator(`a[href="/applications/${APPLICATION_ID}"]`).click();
    await page.waitForURL(`**/applications/${APPLICATION_ID}`);
  });
});
