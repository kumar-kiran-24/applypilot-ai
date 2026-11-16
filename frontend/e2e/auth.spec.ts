import { expect, test } from "@playwright/test";

import {
  gmailConnected,
  httpError,
  installClientFixtures,
  matchingSummary,
  signIn,
} from "./support";

test.describe("authentication UI", () => {
  test("shows Google sign-in, expired and signed-out states", async ({ page }) => {
    await page.goto("/login?expired=1");

    await expect(
      page.getByRole("heading", { name: "Sign in to ApplyPilot" }),
    ).toBeVisible();
    await expect(page.getByText("Your session expired")).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Continue with Google/ }),
    ).toHaveAttribute("href", /\/api\/v1\/auth\/google\/login$/);

    const browserStorage = await page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    }));
    expect(browserStorage).toEqual({ local: 0, session: 0 });

    await page.goto("/login?signedOut=1");
    await expect(page.getByText("You have been signed out")).toBeVisible();
  });

  test("redirects a protected page when there is no session", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
    await expect(
      page.getByRole("heading", { name: "Sign in to ApplyPilot" }),
    ).toBeVisible();
  });

  test("explains an expired backend session", async ({ page }) => {
    await signIn(page, "expired-token");
    await page.goto("/dashboard");

    await expect(page).toHaveURL(/\/login\?expired=1/);
    await expect(page.getByText("Your session expired")).toBeVisible();
  });

  test("an in-page 401 clears the session and shows the expired login", async ({
    page,
  }) => {
    await signIn(page);
    const calls = await installClientFixtures(page, {
      "GET gmail/status": () => gmailConnected,
      "GET jobs": async () => {
        // Mirrors the BFF: a rejected token drops the session cookie before
        // the browser is sent to the login page.
        await page.context().clearCookies();
        return httpError(401, "Your session has expired.");
      },
      "GET matching/summary": () => matchingSummary(),
    });

    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login\?expired=1/);
    await expect(page.getByText("Your session expired")).toBeVisible();
    // The redirected page must not render the stale recommendation payload.
    await expect(page.getByText("Senior Backend Engineer")).toHaveCount(0);
    expect(calls.some((call) => call.method === "GET" && call.path === "jobs")).toBe(
      true,
    );
  });
});
