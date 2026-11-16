import { expect, test } from "@playwright/test";

import {
  SCANNED,
  STRONG,
  httpError,
  installClientFixtures,
  makeResume,
  matchingSummary,
  resumeIntelligence,
  scannedResume,
  signIn,
} from "./support";

test.describe("resume intelligence", () => {
  test("saves an analysis and never invents content for a scanned PDF", async ({
    page,
  }) => {
    let analyzed = false;

    const calls = await installClientFixtures(page, {
      "GET matching/summary": () => matchingSummary(),
      "GET resumes": () => [
        makeResume(STRONG, "backend-strong.pdf"),
        scannedResume(),
      ],
      [`GET resumes/${STRONG}/intelligence`]: () =>
        resumeIntelligence({
          extraction: analyzed
            ? { analysis_state: "ready", cached: true }
            : { analysis_state: "missing", cached: false },
          message: analyzed
            ? "Cached analysis reused. Send force=true to re-run it."
            : "No resume analysis has been stored yet. This reading was computed for this response only.",
        }),
      [`POST resumes/${STRONG}/analyze`]: () => {
        analyzed = true;
        return resumeIntelligence({
          extraction: { analysis_state: "ready", cached: false },
        });
      },
      // An OCR-only resume must not be analyzed or inferred from.
      [`GET resumes/${SCANNED}/intelligence`]: () =>
        httpError(500, "A scanned resume must never be analyzed."),
    });

    await signIn(page);
    await page.goto("/resumes");

    await expect(
      page.getByRole("heading", { name: "Resume manager" }),
    ).toBeVisible();
    await expect(page.getByText("backend-strong.pdf").first()).toBeVisible();
    await expect(page.getByText("scanned-resume.pdf").first()).toBeVisible();

    await expect(page.getByText("Not analyzed yet")).toBeVisible();
    await expect(
      page.getByText(/No resume analysis has been stored yet/i),
    ).toBeVisible();
    await page.getByRole("button", { name: "Analyze resume" }).click();
    await expect(page.getByText("Analysis ready")).toBeVisible();

    expect(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.path === `resumes/${STRONG}/analyze` &&
          JSON.stringify(call.body) === JSON.stringify({ force: false }),
      ),
    ).toBe(true);

    await expect(
      page.getByText("OCR required before analysis"),
    ).toBeVisible();
    expect(
      calls.some((call) => call.path.startsWith(`resumes/${SCANNED}/`)),
    ).toBe(false);
    expect(calls.some((call) => /approve|submit/i.test(call.path))).toBe(false);
  });
});
