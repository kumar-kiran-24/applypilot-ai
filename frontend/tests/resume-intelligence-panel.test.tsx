// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResumeIntelligencePanel } from "@/components/intelligence/resume-intelligence-panel";
import { resumeIntelligence } from "./fixtures/intelligence";
import type { ResumeParsingStatus } from "@/types/api";

const RESUME_ID = "11111111-1111-4111-8111-111111111111";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

function renderPanel(parsingStatus: ResumeParsingStatus = "completed") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ResumeIntelligencePanel
        resumeId={RESUME_ID}
        parsingStatus={parsingStatus}
      />
    </QueryClientProvider>,
  );
}

function pathOf(input: RequestInfo | URL): string {
  return String(input).replace(/^.*\/api\/backend\//, "");
}

describe("ResumeIntelligencePanel", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders extracted sections, evidence and confidence for a ready analysis", async () => {
    fetchMock.mockResolvedValue(json(resumeIntelligence()));
    renderPanel();

    expect(await screen.findByText("Analysis ready")).toBeTruthy();
    expect(screen.getByText("4 yrs 6 mos of dated experience")).toBeTruthy();
    expect(screen.getByText("Languages")).toBeTruthy();
    expect(screen.getAllByText("Python").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Senior Backend Engineer · Acme"),
    ).toBeTruthy();
    expect(screen.getByText(/Senior Backend Engineer at Acme/)).toBeTruthy();
    expect(screen.getByText("Payments API")).toBeTruthy();
    expect(screen.getByText("Cut latency 40%")).toBeTruthy();
    expect(
      screen.getByText("BSc Computer Science · State University"),
    ).toBeTruthy();
    expect(screen.getByText("Graduated 2014")).toBeTruthy();
    expect(screen.getByText("AWS Certified Developer")).toBeTruthy();
    expect(screen.getByText(/only evidence found in the uploaded resume/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /show skill evidence/i }));
    expect(screen.getAllByText(/Python, FastAPI/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("High confidence").length).toBeGreaterThan(0);
  });

  it("refreshes a stored analysis with force=true", async () => {
    fetchMock.mockImplementation(async (input, init) => {
      if (pathOf(input) === `resumes/${RESUME_ID}/analyze`) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({ force: true });
        return json(resumeIntelligence());
      }
      return json(resumeIntelligence());
    });
    renderPanel();

    await screen.findByText("Analysis ready");
    fireEvent.click(screen.getByRole("button", { name: /refresh analysis/i }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some((call) => pathOf(call[0]).endsWith("/analyze"))).toBe(
        true,
      ),
    );
  });

  it("labels an unsaved reading and saves it explicitly", async () => {
    let saved = false;
    const unsaved = resumeIntelligence({
      extraction: { analysis_state: "missing", cached: false, status: "completed" },
      message:
        "No resume analysis has been stored yet. This reading was computed for this response only; send an analyze request to save it.",
    });

    fetchMock.mockImplementation(async (input, init) => {
      if (pathOf(input) === `resumes/${RESUME_ID}/analyze`) {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({ force: false });
        saved = true;
        return json(resumeIntelligence());
      }
      return json(saved ? resumeIntelligence() : unsaved);
    });

    renderPanel();

    expect(await screen.findByText("Not analyzed yet")).toBeTruthy();
    expect(screen.getByText(/this reading is not saved/i)).toBeTruthy();
    expect(screen.getByText(/No resume analysis has been stored yet/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /analyze resume/i }));
    expect(await screen.findByText("Analysis ready")).toBeTruthy();
  });

  it("asks to save a stale reading", async () => {
    fetchMock.mockResolvedValue(
      json(
        resumeIntelligence({
          extraction: { analysis_state: "stale", cached: false },
          message:
            "The stored resume analysis is out of date. This reading was computed for this response only; send an analyze request to save it.",
        }),
      ),
    );
    renderPanel();

    expect(await screen.findByText("Analysis out of date")).toBeTruthy();
    expect(
      screen.getByText(/the stored resume analysis is out of date/i),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /save updated analysis/i }),
    ).toBeTruthy();
  });

  it("reports a failed analysis without presenting extracted content", async () => {
    fetchMock.mockResolvedValue(
      json(
        resumeIntelligence({
          fields: {
            summary: null,
            skills: [],
            experience: [],
            projects: [],
            education: [],
            certifications: [],
          },
          extraction: {
            status: "failed",
            analysis_state: "failed",
            cached: true,
            confidence: "low",
            error_code: "resume_text_unavailable",
          },
          message:
            "Resume analysis failed (resume_text_unavailable). Nothing was inferred from the missing content.",
        }),
      ),
    );
    renderPanel();

    expect((await screen.findAllByText("Analysis failed")).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/Resume analysis failed \(resume_text_unavailable\)/i),
    ).toBeTruthy();
    expect(screen.getByText(/Reason code: resume_text_unavailable/)).toBeTruthy();
    expect(screen.queryByText("Analysis ready")).toBeNull();
    expect(screen.queryByText("Languages")).toBeNull();
  });

  it("does not claim analysis for a scanned PDF that needs OCR", async () => {
    renderPanel("ocr_required");

    expect(screen.getByText(/OCR required before analysis/i)).toBeTruthy();
    expect(screen.getByText(/no structured analysis was produced/i)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Analysis ready")).toBeNull();
  });

  it("shows a loading skeleton before the reading arrives", async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockImplementation(
      () => new Promise<Response>((done) => (resolve = done)),
    );
    const { container } = renderPanel();

    await waitFor(() =>
      expect(container.querySelector(".animate-pulse")).not.toBeNull(),
    );
    expect(screen.queryByText("Analysis ready")).toBeNull();

    resolve(json(resumeIntelligence()));
    expect(await screen.findByText("Analysis ready")).toBeTruthy();
  });

  it("surfaces a safe error with a retry action", async () => {
    fetchMock.mockResolvedValue(
      json({ detail: "Resume analysis is unavailable.", code: "unavailable" }, 503),
    );
    renderPanel();

    expect(
      await screen.findByText(/could not load resume intelligence/i),
    ).toBeTruthy();
    expect(screen.getByText(/resume analysis is unavailable/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });
});
