import { expect, test } from "@playwright/test";

import {
  gmailConnected,
  gmailDisconnected,
  installClientFixtures,
  jobList,
  signIn,
} from "./support";

test.describe("Gmail connection UI", () => {
  test("shows a connected read-only mailbox and disconnect control", async ({
    page,
  }) => {
    await signIn(page);
    await installClientFixtures(page, {
      "GET gmail/status": () => gmailConnected,
      "GET jobs": () => jobList(),
    });

    await page.goto("/gmail");

    await expect(
      page.getByRole("heading", { name: "Gmail integration" }),
    ).toBeVisible();
    await expect(
      page.locator("#main-content").getByText("pilot.tester@example.com"),
    ).toBeVisible();
    await expect(page.getByText("Granted scopes")).toBeVisible();
    await expect(page.getByText("gmail:gmail.readonly")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Reconnect$/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Disconnect$/ }),
    ).toBeVisible();
  });

  test("offers an explicit connect action when Gmail is disconnected", async ({
    page,
  }) => {
    await signIn(page);
    await installClientFixtures(page, {
      "GET gmail/status": () => gmailDisconnected,
      "GET jobs": () => jobList(),
    });

    await page.goto("/gmail");

    await expect(
      page.getByRole("button", { name: "Connect Gmail" }).first(),
    ).toBeVisible();
    await expect(page.getByText("Not connected")).toBeVisible();
    await expect(page.getByText("Read-only by design")).toBeVisible();
  });
});
