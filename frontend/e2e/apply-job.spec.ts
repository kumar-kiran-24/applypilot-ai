import { expect, test } from "@playwright/test";

import {
  JOB_ID,
  installClientFixtures,
  jobIntelligence,
  makeHandlers,
  makeJob,
  resumeComparison,
  signIn,
} from "./support";

const LISTING_URL =
  "https://www.glassdoor.com/job-listing/senior-backend-engineer-acme-JV_IC123.htm?jl=100";

function importResponse() {
  return {
    job: makeJob(),
    created: true,
    reused: false,
    extraction: {
      source: "glassdoor",
      title_source: "url_slug",
      description_available: false,
      missing_fields: ["company", "location", "description"],
      notes: [
        "Glassdoor was not scraped. The stored title was derived from the listing URL.",
      ],
    },
  };
}

function handlers(overrides = {}) {
  return makeHandlers({
    "POST jobs/import": () => importResponse(),
    [`GET jobs/${JOB_ID}`]: () => makeJob(),
    [`GET jobs/${JOB_ID}/intelligence`]: () => jobIntelligence(),
    [`GET jobs/${JOB_ID}/recommendation`]: () => resumeComparison(),
    "GET applications": () => ({ total: 0, applications: [] }),
    ...overrides,
  });
}

test.describe("direct job URL import", () => {
  test("imports a pasted Glassdoor link from the dashboard and opens its workspace", async ({
    page,
  }) => {
    const calls = await installClientFixtures(page, handlers());
    await signIn(page);
    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", {
        name: "Have a job link? Start from the listing.",
      }),
    ).toBeVisible();

    await page.getByLabel("Glassdoor job listing URL").fill(LISTING_URL);
    await page.getByRole("button", { name: "Import Job" }).click();

    await page.waitForURL(`**/jobs/${JOB_ID}`);
    await expect(
      page.getByRole("heading", { name: "Senior Backend Engineer" }),
    ).toBeVisible();

    const importCall = calls.find(
      (call) => call.method === "POST" && call.path === "jobs/import",
    );
    expect(importCall?.body).toEqual({ url: LISTING_URL });
  });

  test("the /apply route imports the URL it was opened with", async ({ page }) => {
    const calls = await installClientFixtures(page, handlers());
    await signIn(page);

    await page.goto(`/apply?url=${encodeURIComponent(LISTING_URL)}`);
    await page.waitForURL(`**/jobs/${JOB_ID}`);

    expect(
      calls.some(
        (call) => call.method === "POST" && call.path === "jobs/import",
      ),
    ).toBe(true);
  });

  test("keeps the apply URL in a browser-only cookie while signing in", async ({
    page,
    context,
  }) => {
    await page.goto(`/apply?url=${encodeURIComponent(LISTING_URL)}`);

    await page.waitForURL(/\/login/);
    const cookie = (await context.cookies()).find(
      (item) => item.name === "ap_return_to",
    );
    expect(cookie).toBeTruthy();
    expect(decodeURIComponent(cookie!.value)).toContain("/apply?url=");
    // No credentials, code or token are stored in that hint.
    expect(cookie!.value).not.toContain("token");
  });
});

