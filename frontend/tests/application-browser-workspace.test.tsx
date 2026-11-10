// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationBrowserWorkspace } from "@/components/applications/application-browser-workspace";
import type {
  BrowserSession,
  BrowserSessionCapabilitiesResponse,
  BrowserViewTicketResponse,
  FillExecutionResponse,
} from "@/types/api";

/**
 * Only the transport and API boundaries are mocked here. The component's real
 * connection state machine, React Query hooks and rendering are exercised.
 */

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  readonly sent: string[] = [];

  constructor(url: string | URL) {
    this.url = String(url);
    this.readyState = MockWebSocket.OPEN;
    MockWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  /** Client-side close; the component intentionally ignores its own close. */
  close() {
    this.readyState = MockWebSocket.CLOSED;
  }

  deliver(payload: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  serverClose(code: number, reason = "") {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code, reason } as CloseEvent);
  }
}

const APPLICATION_ID = "11111111-1111-4111-8111-111111111111";

function openSession(): BrowserSession {
  return {
    session_id: "22222222-2222-4222-8222-222222222222",
    application_id: APPLICATION_ID,
    job_id: null,
    job_title: "Senior Engineer",
    company: "Acme",
    application_url: "https://jobs.example.com/apply",
    run_number: 1,
    state: "awaiting_user",
    browser_status: "open",
    current_url: "https://jobs.example.com/apply?token=secret",
    allowed_origins: ["https://jobs.example.com"],
    pause_reason: null,
    failure_code: null,
    failure_reason: null,
    checkpoint: {},
    active: true,
    can_pause: true,
    can_resume: false,
    can_cancel: true,
    started_at: null,
    last_activity_at: null,
    expires_at: null,
    stopped_at: null,
    view: capabilities().view,
  };
}

function capabilities(): BrowserSessionCapabilitiesResponse {
  return {
    view: {
      available: true,
      mode: "cdp_relay",
      user_can_interact: true,
      reason: "The employer's page is streamed live from your session.",
      required_component: null,
    },
    automation_enabled: true,
    session_ttl_seconds: 3600,
    final_submission_note: "ApplyPilot never clicks the final Submit/Apply button.",
  };
}

function ticket(path = "browser-sessions/view/ws"): BrowserViewTicketResponse {
  return {
    browser_session_id: "22222222-2222-4222-8222-222222222222",
    ticket: "single-use-ticket",
    websocket_path: `api/v1/${path}`.replace("api/v1/api/v1", "api/v1"),
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    mode: "cdp_relay",
    frame_format: "jpeg",
    can_interact: true,
    note: "Live view attached.",
  };
}

function fillExecution(): FillExecutionResponse {
  return {
    execution_id: "33333333-3333-4333-8333-333333333333",
    application_id: APPLICATION_ID,
    analysis_id: "44444444-4444-4444-8444-444444444444",
    analysis_version: 1,
    browser_session_id: "22222222-2222-4222-8222-222222222222",
    run_number: 1,
    status: "needs_user_input",
    total_fields: 3,
    ready_count: 1,
    succeeded_count: 1,
    failed_count: 0,
    skipped_count: 2,
    verified_count: 1,
    pending_confirmation_count: 0,
    pending_human_input_count: 1,
    selected_resume_id: null,
    selected_resume_filename: null,
    results: [],
    warnings: [],
    pause_reason: null,
    failure_code: null,
    failure_reason: null,
    can_pause: false,
    can_resume: false,
    can_cancel: false,
    started_at: null,
    paused_at: null,
    completed_at: null,
    last_activity_at: null,
    final_submission_note:
      "ApplyPilot never clicks the final Submit/Apply button.",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function socketAt(index: number): MockWebSocket {
  const socket = MockWebSocket.instances[index];
  if (!socket) throw new Error(`WebSocket ${index} was not opened`);
  return socket;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const fetchMock = vi.fn<typeof fetch>();
let firstTicket: Deferred<BrowserViewTicketResponse>;
let ticketRequests = 0;
let startRequests = 0;
let inspectRequests = 0;

function installFetch() {
  firstTicket = deferred<BrowserViewTicketResponse>();
  ticketRequests = 0;
  startRequests = 0;
  inspectRequests = 0;

  fetchMock.mockImplementation(async (input) => {
    const url = String(input);
    const path = url.replace(/^.*\/api\/backend\//, "");

    if (path === "browser-sessions/capabilities") {
      return json(capabilities());
    }
    if (path === `browser-sessions/by-application/${APPLICATION_ID}`) {
      return json(openSession());
    }
    if (path === "browser-sessions") {
      startRequests += 1;
      return json(openSession());
    }
    if (path === `applications/${APPLICATION_ID}/inspect`) {
      inspectRequests += 1;
      return json({
        application_url: "https://jobs.example.com/apply",
        job_listing_url: "https://www.glassdoor.com/job-listing/x.htm",
        employer_careers_url: null,
        application_url_is_job_board: false,
        suggested_application_url: null,
      });
    }
    if (
      path === `browser-sessions/by-application/${APPLICATION_ID}/view-ticket`
    ) {
      ticketRequests += 1;
      if (ticketRequests === 1) return firstTicket.promise.then((body) => json(body));
      return json(ticket());
    }
    if (path === `browser-sessions/by-application/${APPLICATION_ID}/fill`) {
      return json(fillExecution());
    }
    if (
      path ===
      `browser-sessions/by-application/${APPLICATION_ID}/interventions`
    ) {
      return json({
        application_id: APPLICATION_ID,
        application_url: "https://jobs.example.com/apply",
        browser_session_id: openSession().session_id,
        view: capabilities().view,
        total: 0,
        pending_count: 0,
        user_working_count: 0,
        blocking_count: 0,
        interventions: [],
        final_submission_note:
          "ApplyPilot never clicks the final Submit/Apply button.",
      });
    }
    if (
      path === `browser-sessions/by-application/${APPLICATION_ID}/final-review`
    ) {
      return json({
        application_id: APPLICATION_ID,
        execution_id: null,
        execution_status: "planned",
        analysis_version: null,
        ready_for_final_review: false,
        generated_at: new Date().toISOString(),
        resume: { resume_id: null, filename: null },
        sections: [],
        filled_field_labels: [],
        verified_field_labels: [],
        skipped_field_labels: [],
        confirmation_fields: [],
        blocking_interventions: [],
        missing_optional_fields: [],
        filled_count: 0,
        verified_count: 0,
        skipped_count: 0,
        blocker_count: 0,
        view: capabilities().view,
        manual_submission_required: true,
        final_submission_note:
          "ApplyPilot never clicks the final Submit/Apply button.",
      });
    }
    return json({ detail: `Unhandled test route: ${path}` }, 404);
  });

  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("WebSocket", MockWebSocket);
}

function renderWorkspace(
  props: {
    applicationUrlIsJobBoard?: boolean;
    jobListingUrl?: string | null;
  } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApplicationBrowserWorkspace
        applicationId={APPLICATION_ID}
        company="Acme"
        applicationUrl="https://jobs.example.com/apply"
        jobListingUrl={props.jobListingUrl ?? null}
        applicationUrlIsJobBoard={props.applicationUrlIsJobBoard ?? false}
      />
    </QueryClientProvider>,
  );
}

describe("ApplicationBrowserWorkspace live view", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    fetchMock.mockReset();
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("connects, renders a frame, reports a disconnect and reconnects", async () => {
    renderWorkspace();

    const start = await screen.findByRole("button", {
      name: /open live view/i,
    });
    await waitFor(() =>
      expect((start as HTMLButtonElement).disabled).toBe(false),
    );

    // 1. A ticket request is in flight, so the workspace shows connecting.
    act(() => {
      start.click();
    });
    expect(
      await screen.findByText(/attaching to your existing browser session/i),
    ).toBeTruthy();

    // 2. The ticket succeeds and the socket opens with that ticket.
    act(() => {
      firstTicket.resolve(ticket());
    });
    await waitFor(() =>
      expect(MockWebSocket.instances).toHaveLength(1),
    );
    const socket = socketAt(0);
    expect(socket.url).toContain("ws://127.0.0.1:8000/api/v1/");
    expect(socket.url).toContain("/browser-sessions/view/ws?ticket=single-use-ticket");

    // 3. The server reports the viewport; the workspace becomes connected.
    act(() => {
      socket.deliver({ type: "ready", width: 1280, height: 900, note: null });
    });
    expect(await screen.findByText("Connected")).toBeTruthy();

    // 4. A JPEG frame arrives and is rendered as the employer page.
    act(() => {
      socket.deliver({
        type: "frame",
        format: "jpeg",
        data: "ZmFrZS1mcmFtZQ==",
        width: 1280,
        height: 900,
      });
    });
    const firstImage = await screen.findByAltText(
      "Live employer application page",
    );
    expect(firstImage.getAttribute("src")).toBe(
      "data:image/jpeg;base64,ZmFrZS1mcmFtZQ==",
    );

    // 5. An unexpected transport close is explained precisely.
    act(() => {
      socket.serverClose(1006, "abnormal_closure");
    });
    expect(
      await screen.findByText("Live view connection failed"),
    ).toBeTruthy();
    expect(
      screen.getByText(/closed with code 1006/i),
    ).toBeTruthy();

    // 6. Reconnect requests a fresh ticket and attaches a new socket.
    const reconnect = screen.getAllByRole("button", {
      name: /reconnect/i,
    })[0];
    if (!reconnect) throw new Error("Reconnect button is missing");
    act(() => {
      reconnect.click();
    });
    await waitFor(() =>
      expect(MockWebSocket.instances).toHaveLength(2),
    );
    const secondSocket = socketAt(1);
    expect(ticketRequests).toBe(2);

    act(() => {
      secondSocket.deliver({ type: "ready", width: 1280, height: 900 });
      secondSocket.deliver({
        type: "frame",
        format: "jpeg",
        data: "c2Vjb25kLWZyYW1l",
        width: 1280,
        height: 900,
      });
    });
    const secondImage = await screen.findByAltText(
      "Live employer application page",
    );
    expect(secondImage.getAttribute("src")).toBe(
      "data:image/jpeg;base64,c2Vjb25kLWZyYW1l",
    );
  });

  it("asks for the employer URL and retries the browser after saving it", async () => {
    renderWorkspace({
      applicationUrlIsJobBoard: true,
      jobListingUrl: "https://www.glassdoor.com/job-listing/x.htm",
    });

    expect(
      await screen.findByText(/this application still points at a job listing/i),
    ).toBeTruthy();
    const input = screen.getByLabelText(
      "Employer application URL",
    ) as HTMLInputElement;
    expect(input.value).toBe("");

    fireEvent.change(input, {
      target: { value: "https://jobs.example.com/apply" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /save url and retry browser/i }),
    );

    await waitFor(() => expect(inspectRequests).toBe(1));
    await waitFor(() => expect(startRequests).toBe(1), { timeout: 4000 });
    act(() => {
      firstTicket.resolve(ticket());
    });
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1), {
      timeout: 4000,
    });

    act(() => {
      socketAt(0).deliver({ type: "ready", width: 1280, height: 900 });
    });
    expect(await screen.findByText("Connected")).toBeTruthy();
  });

  it("explains a disabled stream instead of showing a generic message", async () => {
    fetchMock.mockImplementation(async (input) => {
      const path = String(input).replace(/^.*\/api\/backend\//, "");
      if (path === "browser-sessions/capabilities") {
        return json({
          ...capabilities(),
          automation_enabled: false,
          view: {
            available: false,
            mode: "none",
            user_can_interact: false,
            reason: "Browser streaming is disabled on this deployment.",
            required_component: "APPLICATION_BROWSER_STREAMING_ENABLED=true",
          },
        });
      }
      if (path.startsWith("browser-sessions/by-application/")) {
        return json(openSession());
      }
      return json({}, 200);
    });

    renderWorkspace();

    expect(
      await screen.findByText(
        /live browser view is disabled on this deployment/i,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/browser streaming is disabled on this deployment/i),
    ).toBeTruthy();
    expect(
      screen.getByText(/APPLICATION_BROWSER_STREAMING_ENABLED=true/i),
    ).toBeTruthy();
    const start = screen.getByRole("button", { name: /open live view/i });
    expect((start as HTMLButtonElement).disabled).toBe(true);
  });
});
