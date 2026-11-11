// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResumeComparisonPanel } from "@/components/intelligence/resume-comparison-panel";
import { resumeComparison } from "./fixtures/intelligence";

const JOB_ID = "22222222-2222-4222-8222-222222222222";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ResumeComparisonPanel jobId={JOB_ID} />
    </QueryClientProvider>,
  );
}

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
}

describe("ResumeComparisonPanel", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the backend ranking, recommendation and failures without recomputing", async () => {
    fetchMock.mockResolvedValue(json(resumeComparison()));
    renderPanel();

    expect(await screen.findByText("2 of 3 compared")).toBeTruthy();
    expect(screen.getByText("Recommended resume")).toBeTruthy();
    expect(
      screen.getByText("82.5% compatibility"),
    ).toBeTruthy();
    expect(screen.getAllByText("backend-strong.pdf").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Highest overall compatibility score."),
    ).toBeTruthy();
    expect(
      screen.getAllByText("PostgreSQL is not evidenced").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("backend-weak.pdf").length).toBeGreaterThan(0);
    expect(screen.getByText("71% · 11.5 points lower")).toBeTruthy();
    expect(
      screen.getByText(/differs on required-skill coverage, project evidence/i),
    ).toBeTruthy();

    // The recommended resume is selected by default and its scores are shown.
    expect(screen.getByText("Required-skill coverage (2/3)")).toBeTruthy();
    expect(screen.getByText("Experience")).toBeTruthy();
    expect(screen.getByText("Projects")).toBeTruthy();
    expect(screen.getByText("Education")).toBeTruthy();
    expect(screen.getByText(/Rank #1/)).toBeTruthy();
    expect(
      screen.getAllByText(/Python listed in the skills section/).length,
    ).toBeGreaterThan(0);

    // Partial/failed resumes are reported rather than failing the comparison.
    expect(screen.getByText("scanned.pdf")).toBeTruthy();
    expect(
      screen.getByText(/Resume analysis failed and no usable content remains/i),
    ).toBeTruthy();
    expect(
      screen.getAllByText(/measure overlap with the posting, not hiring probability/i)
        .length,
    ).toBeGreaterThan(0);

    // Opening the panel only reads the stored comparison.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(pathOf(fetchMock.mock.calls[0]![0])).toBe(
      `jobs/${JOB_ID}/recommendation`,
    );
  });

  it("switches the detail to another resume and shows its analysis status", async () => {
    fetchMock.mockResolvedValue(json(resumeComparison()));
    renderPanel();

    await screen.findByText("2 of 3 compared");
    const weak = screen.getAllByRole("button", { name: /backend-weak\.pdf/i })[0];
    if (!weak) throw new Error("weak resume button missing");
    fireEvent.click(weak);

    expect(await screen.findByText(/Rank #2/)).toBeTruthy();
    expect(screen.getByText(/No resume analysis is stored/i)).toBeTruthy();
    expect(
      screen.getAllByText("Analysis required").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Match inputs: partial")).toBeTruthy();
    expect(screen.getByText("Required-skill coverage (1/3)")).toBeTruthy();
    expect(
      screen.getByText("FastAPI is not evidenced"),
    ).toBeTruthy();
  });

  it("communicates close scores as a preference, not a clear winner", async () => {
    const comparison = resumeComparison();
    fetchMock.mockResolvedValue(
      json({
        ...comparison,
        close_score: true,
        recommendation: {
          ...comparison.recommendation!,
          close_call: true,
          close_call_explanation:
            "The top two resumes are within 3 points; required-skill coverage and project evidence decide the preference.",
        },
      }),
    );
    renderPanel();

    expect(await screen.findByText("Scores are close")).toBeTruthy();
    expect(
      screen.getByText(/within 3 points; required-skill coverage/i),
    ).toBeTruthy();
  });

  it("shows the no-suitable-resume state without inventing a recommendation", async () => {
    fetchMock.mockResolvedValue(
      json(
        resumeComparison({
          status: "no_suitable_resume",
          recommendation: null,
          message:
            "No resume had enough usable evidence for a recommendation. Review your resumes and analyze them again.",
        }),
      ),
    );
    renderPanel();

    expect(await screen.findByText("No suitable resume")).toBeTruthy();
    expect(
      screen.getAllByText(/No resume had enough usable evidence/i).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Recommended resume")).toBeNull();
    expect(screen.getAllByText("backend-strong.pdf").length).toBeGreaterThan(0);
  });

  it("asks for a comparison when nothing is stored, then shows the result", async () => {
    let compared = false;
    fetchMock.mockImplementation(async (input, init) => {
      const path = pathOf(input);
      if (path === `jobs/${JOB_ID}/compare-resumes`) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({ force: false });
        compared = true;
        return json(resumeComparison());
      }
      return json(
        compared
          ? resumeComparison()
          : resumeComparison({
              status: "not_computed",
              total_resumes: 0,
              compared_resumes: 0,
              resumes: [],
              recommendation: null,
              processing_failures: [],
              message: "Compare your resumes to build a recommendation.",
            }),
      );
    });
    renderPanel();

    expect(await screen.findByText("No comparison yet")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /compare resumes/i }));

    expect(await screen.findByText("Recommended resume")).toBeTruthy();
    expect(
      screen.getByText("82.5% compatibility"),
    ).toBeTruthy();
  });

  it("explains an unavailable comparison and offers a retry", async () => {
    fetchMock.mockResolvedValue(
      json({ detail: "Comparison service unavailable.", code: "unavailable" }, 503),
    );
    renderPanel();

    expect(
      await screen.findByText(/could not load the comparison/i),
    ).toBeTruthy();
    expect(screen.getByText(/comparison service unavailable/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("surfaces a safe error when recomputing fails", async () => {
    fetchMock.mockImplementation(async (input) => {
      if (pathOf(input) === `jobs/${JOB_ID}/compare-resumes`) {
        return json({ detail: "Could not compare resumes." }, 500);
      }
      return json(resumeComparison());
    });
    renderPanel();

    await screen.findByText("Recommended resume");
    fireEvent.click(screen.getByRole("button", { name: /recompute/i }));

    expect(await screen.findByText("Comparison failed")).toBeTruthy();
    expect(screen.getByText(/could not compare resumes/i)).toBeTruthy();
  });
});
