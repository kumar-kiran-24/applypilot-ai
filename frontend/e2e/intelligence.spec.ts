import { expect, test } from "@playwright/test";

import {
  JOB_ID,
  closeComparison,
  emptyComparison,
  httpError,
  installClientFixtures,
  jobIntelligence,
  jobList,
  makeHandlers,
  signIn,
} from "./support";

test.describe("job intelligence and resume comparison", () => {
  test("analyzes the posting, compares resumes and explains a close call", async ({
    page,
  }) => {
    let saved = false;
    let compared = false;

    const calls = await installClientFixtures(
      page,
      makeHandlers({
        "GET jobs": () => jobList(),
        [`GET jobs/${JOB_ID}/intelligence`]: () =>
          jobIntelligence({ extraction: { cached: saved } }),
        [`POST jobs/${JOB_ID}/analyze`]: () => {
          saved = true;
          return jobIntelligence({ extraction: { cached: true } });
        },
        [`GET jobs/${JOB_ID}/recommendation`]: () =>
          compared ? closeComparison() : emptyComparison(),
        [`POST jobs/${JOB_ID}/compare-resumes`]: () => {
          compared = true;
          return closeComparison();
        },
      }),
    );

    await signIn(page);
    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Recent job recommendations" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Senior Backend Engineer" }),
    ).toBeVisible();
    await expect(page.getByText("82.5% match")).toBeVisible();

    // Opening the comparison is a read; it must not analyze or compare yet.
    await page.getByRole("button", { name: "Compare resumes" }).click();
    await expect(page.getByText("Not saved yet")).toBeVisible();
    await expect(page.getByText("Required skills")).toBeVisible();
    await expect(
      page.getByText("Python", { exact: true }).first(),
    ).toBeVisible();
    expect(
      calls.some((call) => call.method === "POST"),
    ).toBe(false);

    await page.getByRole("button", { name: "Analyze and save" }).click();
    await expect(page.getByText("Saved analysis")).toBeVisible();
    expect(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.path === `jobs/${JOB_ID}/analyze` &&
          JSON.stringify(call.body) ===
            JSON.stringify({ force: false, use_llm: true }),
      ),
    ).toBe(true);

    // The comparison still needs an explicit click and never auto-selects.
    await expect(page.getByText("No comparison yet")).toBeVisible();
    await page
      .getByRole("button", { name: "Compare resumes" })
      .last()
      .click();

    await expect(page.getByText("Recommended resume")).toBeVisible();
    await expect(
      page.getByText("82.5% compatibility", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Scores are close")).toBeVisible();
    await expect(
      page.getByText(/within 3 points; required-skill coverage/i),
    ).toBeVisible();
    await expect(
      page
        .getByText(/measure overlap with the posting, not hiring probability/i)
        .first(),
    ).toBeVisible();
    await expect(page.getByText("backend-weak.pdf").first()).toBeVisible();
    await expect(page.getByText("scanned.pdf")).toBeVisible();

    expect(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.path === `jobs/${JOB_ID}/compare-resumes` &&
          JSON.stringify(call.body) === JSON.stringify({ force: false }),
      ),
    ).toBe(true);

    // This workflow never approves or submits an application.
    expect(
      calls.some((call) => /approve|submit/i.test(call.path)),
    ).toBe(false);
  });

  test("renders safe errors when the intelligence APIs fail", async ({ page }) => {
    const calls = await installClientFixtures(
      page,
      makeHandlers({
        "GET jobs": () => jobList(),
        [`GET jobs/${JOB_ID}/intelligence`]: () =>
          httpError(503, "Intelligence service unavailable."),
        [`GET jobs/${JOB_ID}/recommendation`]: () =>
          httpError(503, "Comparison service unavailable."),
      }),
    );

    await signIn(page);
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Compare resumes" }).click();

    await expect(
      page.getByText(/could not load job intelligence/i),
    ).toBeVisible();
    await expect(
      page.getByText(/could not load the comparison/i),
    ).toBeVisible();
    await expect(
      page.getByText("Intelligence service unavailable."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" }).first()).toBeVisible();

    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/traceback|storage_key|storage_backend|checksum/i);
    expect(calls.some((call) => /approve|submit/i.test(call.path))).toBe(false);
  });
});
