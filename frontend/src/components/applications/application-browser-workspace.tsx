"use client";

import {
  AlertTriangle,
  ArrowUpRight,
  ExternalLink,
  Keyboard,
  MonitorPlay,
  MousePointer2,
  Pause,
  Play,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  useBrowserCapabilities,
  useBrowserSession,
  useBrowserViewTicket,
  useCancelBrowserSession,
  useFillExecution,
  useFinalReview,
  useInterventions,
  usePauseBrowserSession,
  usePauseFieldFill,
  useReanalyzeHumanLoop,
  useResumeBrowserSession,
  useResumeHumanLoop,
  useStartBrowserSession,
} from "@/features/applications/hooks";
import {
  applicationKeys,
  fillKeys,
  humanLoopKeys,
} from "@/features/applications/api";
import { describeError } from "@/lib/api/errors";
import { apiBaseUrl } from "@/lib/constants";
import { cn } from "@/lib/utils/cn";
import { safeExternalUrl } from "@/lib/utils/url";
import type { BrowserViewInfo } from "@/types/api";

type ViewState = "idle" | "connecting" | "live" | "closed" | "error";

const EXECUTION_STATUS: Record<string, string> = {
  planned: "Planned",
  running: "Filling safely",
  needs_user_input: "Paused for you",
  waiting_for_confirmation: "Waiting for confirmation",
  paused: "Paused",
  ready_for_final_review: "Ready for final review",
  completed: "Completed",
  session_expired: "Browser session expired",
  cancelled: "Cancelled",
  failed: "Needs attention",
};

function websocketUrl(path: string, ticket: string): string {
  const base = new URL(apiBaseUrl());
  const origin = `${base.protocol === "https:" ? "wss:" : "ws:"}//${base.host}`;
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${origin}${suffix}?ticket=${encodeURIComponent(ticket)}`;
}

function displayUrl(raw: string | null | undefined): string {
  if (!raw) return "";
  try {
    const url = new URL(raw);
    return `${url.host}${url.pathname || "/"}`;
  } catch {
    return "";
  }
}

function modifiers(event: {
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}): number {
  return (
    (event.altKey ? 1 : 0) |
    (event.ctrlKey ? 2 : 0) |
    (event.metaKey ? 4 : 0) |
    (event.shiftKey ? 8 : 0)
  );
}

const SPECIAL_KEYS = new Set([
  "Enter",
  "Tab",
  "Backspace",
  "Escape",
  "Delete",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
  " ",
]);

function CapabilityNote({ view }: { view: BrowserViewInfo }) {
  if (view.available && view.user_can_interact) {
    return (
      <div className="flex items-start gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.04] p-3">
        <ShieldCheck
          aria-hidden="true"
          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"
        />
        <p className="text-[11px] leading-relaxed text-emerald-100/80">
          {view.reason}
        </p>
      </div>
    );
  }
  return (
    <Alert variant="info" title="Live browser view is unavailable">
      {view.reason}
      {view.required_component ? (
        <span className="mt-1 block text-ink-subtle">
          Required component: {view.required_component}.
        </span>
      ) : null}
    </Alert>
  );
}

export function ApplicationBrowserWorkspace({
  applicationId,
  company,
  applicationUrl,
}: {
  applicationId: string;
  company: string | null;
  applicationUrl: string;
}) {
  const queryClient = useQueryClient();
  const capabilities = useBrowserCapabilities();
  const session = useBrowserSession(applicationId);
  const startSession = useStartBrowserSession(applicationId);
  const ticket = useBrowserViewTicket(applicationId);
  const cancelSession = useCancelBrowserSession(applicationId);
  const pauseSession = usePauseBrowserSession(applicationId);
  const resumeSession = useResumeBrowserSession(applicationId);
  const execution = useFillExecution(applicationId);
  const interventions = useInterventions(applicationId);
  const review = useFinalReview(applicationId);
  const reanalyze = useReanalyzeHumanLoop(applicationId);
  const pauseFill = usePauseFieldFill(applicationId);
  const resumeFill = useResumeHumanLoop(applicationId);

  const [open, setOpen] = useState(false);
  const [state, setState] = useState<ViewState>("idle");
  const [frame, setFrame] = useState<string | null>(null);
  const [viewport, setViewport] = useState({ width: 1280, height: 900 });
  const [notice, setNotice] = useState<string | null>(null);
  const [inputNotice, setInputNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [endConfirm, setEndConfirm] = useState(false);

  const socketRef = useRef<WebSocket | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const lastMoveRef = useRef(0);

  useEffect(() => {
    return () => {
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, []);

  // React's wheel listener is passive, so attach a native non-passive one to
  // keep the outer page still while forwarding the scroll to the remote page.
  useEffect(() => {
    const element = viewportRef.current;
    if (!element || state !== "live") return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const image = imageRef.current;
      const socket = socketRef.current;
      if (!image || !socket || socket.readyState !== WebSocket.OPEN) return;
      const rect = image.getBoundingClientRect();
      const scale = Math.min(
        rect.width / viewport.width,
        rect.height / viewport.height,
      );
      if (!Number.isFinite(scale) || scale <= 0) return;
      const offsetX = (rect.width - viewport.width * scale) / 2;
      const offsetY = (rect.height - viewport.height * scale) / 2;
      const x = (event.clientX - rect.left - offsetX) / scale;
      const y = (event.clientY - rect.top - offsetY) / scale;
      if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) return;
      socket.send(
        JSON.stringify({
          type: "mouse",
          event: "wheel",
          x,
          y,
          deltaX: event.deltaX,
          deltaY: event.deltaY,
        }),
      );
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [state, viewport.width, viewport.height]);

  const disconnect = (next: ViewState = "idle") => {
    socketRef.current?.close();
    socketRef.current = null;
    setState(next);
    setFrame(null);
    setNotice(null);
    setInputNotice(null);
  };

  const connectSocket = (path: string, viewTicket: string) => {
    socketRef.current?.close();
    const socket = new WebSocket(websocketUrl(path, viewTicket));
    socketRef.current = socket;

    socket.onmessage = (event) => {
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(String(event.data)) as Record<string, unknown>;
      } catch {
        return;
      }
      switch (message.type) {
        case "ready": {
          const width = Number(message.width);
          const height = Number(message.height);
          if (Number.isFinite(width) && Number.isFinite(height)) {
            setViewport({ width, height });
          }
          setState("live");
          setNotice(
            typeof message.note === "string" ? message.note : null,
          );
          break;
        }
        case "frame": {
          if (typeof message.data !== "string") return;
          const width = Number(message.width);
          const height = Number(message.height);
          if (Number.isFinite(width) && Number.isFinite(height)) {
            setViewport({ width, height });
          }
          setFrame(`data:image/jpeg;base64,${message.data}`);
          break;
        }
        case "navigated": {
          setNotice(
            typeof message.message === "string"
              ? message.message
              : "The page changed. Re-analyze the form.",
          );
          void queryClient.invalidateQueries({ queryKey: fillKeys.all });
          void queryClient.invalidateQueries({
            queryKey: humanLoopKeys.all,
          });
          void queryClient.invalidateQueries({
            queryKey: applicationKeys.all,
          });
          break;
        }
        case "error": {
          setInputNotice(
            typeof message.message === "string"
              ? message.message
              : "That interaction was not accepted.",
          );
          break;
        }
        case "session_closed": {
          setState("closed");
          setNotice(
            typeof message.message === "string" ? message.message : null,
          );
          break;
        }
        default:
          break;
      }
    };

    socket.onerror = () => {
      setState("error");
      setError("The live browser connection could not be established.");
    };

    socket.onclose = (event) => {
      if (socketRef.current !== socket) return;
      socketRef.current = null;
      setState((current) => (current === "live" ? "closed" : current));
      if (event.code === 4404) {
        setNotice(
          "The browser session expired or was cancelled. Start it again to reopen the employer's page.",
        );
      } else if (event.code === 4409) {
        setNotice(
          "Another live view is already attached to this browser session.",
        );
      } else if (event.code === 4401) {
        setNotice(
          "The view ticket expired or was already used. Reconnect to get a new one.",
        );
      } else if (event.code === 4403) {
        setError("This browser view is not allowed from this origin.");
      }
    };
  };

  const openWorkspace = async () => {
    if (!capabilities.data?.view.available) return;
    setOpen(true);
    setState("connecting");
    setError(null);
    setInputNotice(null);
    setEndConfirm(false);
    try {
      // Idempotent: returns the existing live browser or opens the page once.
      const current = await startSession.mutateAsync();
      // A paused browser is still live and viewable; only a closed/expired
      // session cannot be attached.
      if (current.browser_status !== "open") {
        throw new Error(
          current.failure_reason ??
            "The browser session is not live. Start it again.",
        );
      }
      const issued = await ticket.mutateAsync();
      connectSocket(issued.websocket_path, issued.ticket);
    } catch (cause) {
      setState("error");
      setError(describeError(cause));
    }
  };

  const imagePoint = (event: {
    clientX: number;
    clientY: number;
  }): { x: number; y: number } | null => {
    const image = imageRef.current;
    if (!image) return null;
    const rect = image.getBoundingClientRect();
    const scale = Math.min(
      rect.width / viewport.width,
      rect.height / viewport.height,
    );
    if (!Number.isFinite(scale) || scale <= 0) return null;
    const offsetX = (rect.width - viewport.width * scale) / 2;
    const offsetY = (rect.height - viewport.height * scale) / 2;
    const x = (event.clientX - rect.left - offsetX) / scale;
    const y = (event.clientY - rect.top - offsetY) / scale;
    if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) {
      return null;
    }
    return { x, y };
  };

  const send = (payload: Record<string, unknown>) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify(payload));
  };

  const sendMouse = (
    event: ReactPointerEvent<HTMLImageElement>,
    kind: "down" | "up" | "move",
  ) => {
    if (state !== "live") return;
    const point = imagePoint(event);
    if (!point) return;
    send({
      type: "mouse",
      event: kind,
      x: point.x,
      y: point.y,
      button: "left",
      clickCount: 1,
      modifiers: modifiers(event),
    });
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (state !== "live") return;
    if (SPECIAL_KEYS.has(event.key)) {
      event.preventDefault();
      send({
        type: "key",
        event: "press",
        key: event.key,
        modifiers: modifiers(event),
      });
      return;
    }
    if (
      event.key.length === 1 &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey
    ) {
      event.preventDefault();
      send({ type: "text", text: event.key });
    }
  };

  const activeInterventions = (interventions.data?.interventions ?? []).filter(
    (item) => item.status === "pending" || item.status === "user_working",
  );
  const run = execution.data;
  const canLaunch = capabilities.data?.view.available === true;
  const sessionOpen = session.data?.browser_status === "open";
  const externalUrl = safeExternalUrl(applicationUrl);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <MonitorPlay aria-hidden="true" className="h-4 w-4 text-sky-300" />
            Live browser workspace
          </CardTitle>
          <CardDescription>
            View and interact with the same employer page the agent is using.
            CAPTCHA, OTP, MFA, legal declarations and final submission stay in
            your hands.
          </CardDescription>
        </div>
        {session.data ? (
          <Badge variant={sessionOpen ? "success" : "neutral"}>
            <Wifi aria-hidden="true" className="h-3 w-3" />
            {sessionOpen ? "Session live" : session.data.browser_status}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {capabilities.isLoading ? <Skeleton className="h-16 w-full" /> : null}
        {capabilities.data ? (
          <CapabilityNote view={capabilities.data.view} />
        ) : capabilities.error ? (
          <Alert variant="danger" title="Could not read browser capabilities">
            {describeError(capabilities.error)}
          </Alert>
        ) : null}

        {!open ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
            <div>
              <p className="text-[12px] font-medium text-ink">
                {sessionOpen
                  ? "A browser session is open for this application."
                  : session.data
                    ? `Session ${session.data.state.replaceAll("_", " ")}`
                    : "No browser session has been opened yet."}
              </p>
              <p className="mt-0.5 text-[11px] text-ink-subtle">
                Opening the workspace reuses the existing Playwright page; it
                never creates a second browser.
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {externalUrl ? (
                <a
                  href={externalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 text-[12px] font-medium text-ink-muted transition-colors hover:border-white/20 hover:text-ink"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Employer page
                </a>
              ) : null}
              <Button
                size="sm"
                disabled={!canLaunch || startSession.isPending || ticket.isPending}
                onClick={() => void openWorkspace()}
              >
                {startSession.isPending || ticket.isPending ? (
                  <Spinner />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                {sessionOpen ? "Open live view" : "Start live browser"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-navy-950/60 px-3 py-2">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Badge variant="brand">
                  {company ?? session.data?.company ?? "Employer"}
                </Badge>
                <span
                  className="truncate font-mono text-[11px] text-ink-muted"
                  title="Current page (query string hidden)"
                >
                  {displayUrl(session.data?.current_url ?? applicationUrl) ||
                    "about:blank"}
                </span>
                <Badge
                  variant={
                    state === "live"
                      ? "success"
                      : state === "connecting"
                        ? "brand"
                        : "neutral"
                  }
                >
                  {state === "live" ? (
                    <Wifi className="h-3 w-3" />
                  ) : (
                    <WifiOff className="h-3 w-3" />
                  )}
                  {state === "live"
                    ? "Connected"
                    : state === "connecting"
                      ? "Connecting"
                      : state}
                </Badge>
                {session.data ? (
                  <Badge variant="neutral">
                    {session.data.state.replaceAll("_", " ")}
                  </Badge>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={state === "connecting"}
                  onClick={() => void openWorkspace()}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Reconnect
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    disconnect();
                    setOpen(false);
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                  Close view
                </Button>
              </div>
            </div>

            {state === "live" ? (
              <div className="space-y-2">
                <div
                  ref={viewportRef}
                  tabIndex={0}
                  onKeyDown={onKeyDown}
                  aria-label="Live employer application page. Click the page, then type."
                  className="relative overflow-hidden rounded-xl border border-white/10 bg-black outline-none focus:ring-2 focus:ring-brand-blue/40"
                >
                  {frame ? (
                    // Real JPEG frames from the backend page; no placeholder.
                    // next/image cannot optimise a rapidly changing data URL.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      ref={imageRef}
                      src={frame}
                      alt="Live employer application page"
                      draggable={false}
                      className="block max-h-[620px] w-full cursor-default select-none object-contain"
                      onPointerDown={(event) => {
                        event.preventDefault();
                        event.currentTarget.parentElement?.focus();
                        sendMouse(event, "down");
                      }}
                      onPointerUp={(event) => sendMouse(event, "up")}
                      onPointerMove={(event) => {
                        const now = Date.now();
                        if (now - lastMoveRef.current < 60) return;
                        lastMoveRef.current = now;
                        sendMouse(event, "move");
                      }}
                    />
                  ) : (
                    <div className="flex h-64 items-center justify-center text-[12px] text-ink-subtle">
                      <Spinner />
                      Waiting for the first live frame…
                    </div>
                  )}
                  {notice ? (
                    <div className="flex items-start gap-2 border-t border-sky-400/20 bg-sky-500/10 px-3 py-2 text-[11px] leading-relaxed text-sky-100">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span>{notice}</span>
                    </div>
                  ) : null}
                </div>
                {inputNotice ? (
                  <Alert variant="warning" title="Input notice">
                    {inputNotice}
                  </Alert>
                ) : null}
                <p className="flex flex-wrap items-center gap-2 text-[11px] text-ink-subtle">
                  <MousePointer2 className="h-3.5 w-3.5" />
                  Click, type and scroll directly on the page.
                  <Keyboard className="h-3.5 w-3.5" />
                  Complete CAPTCHA, OTP and MFA yourself. Nothing is submitted.
                </p>
              </div>
            ) : state === "connecting" ? (
              <div className="flex h-56 items-center justify-center rounded-xl border border-white/[0.07] bg-white/[0.02]">
                <div className="text-center">
                  <Spinner className="mx-auto" />
                  <p className="mt-2 text-[12px] text-ink-muted">
                    Attaching to your existing browser session…
                  </p>
                </div>
              </div>
            ) : state === "error" ? (
              <Alert variant="danger" title="Live view unavailable">
                {error ?? "The live browser connection failed."}
              </Alert>
            ) : (
              <Alert variant="info" title="Live view disconnected">
                {notice ??
                  "The view is closed. Reconnect to attach to the browser session again."}
              </Alert>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                <p className="text-[11px] uppercase tracking-[0.1em] text-ink-subtle">
                  Automation status
                </p>
                <p className="text-[12px] font-medium text-ink">
                  {EXECUTION_STATUS[run?.status ?? ""] ?? "No fill run yet"}
                </p>
                <p className="text-[11px] text-ink-subtle">
                  {run
                    ? `${run.succeeded_count} filled · ${run.verified_count} verified · ${run.failed_count} failed`
                    : "Start a safe fill run to let the agent fill ready fields."}
                </p>
                <div className="flex flex-wrap gap-1.5 pt-1">
                  <Badge variant="warning">
                    {activeInterventions.length} need you
                  </Badge>
                  <Badge variant="neutral">
                    {interventions.data?.blocking_count ?? 0} blocking
                  </Badge>
                  <Badge
                    variant={review.data?.ready_for_final_review ? "success" : "neutral"}
                  >
                    {review.data?.ready_for_final_review
                      ? "Review ready"
                      : "Not ready"}
                  </Badge>
                </div>
                <div className="flex flex-wrap gap-1.5 pt-1">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={
                      reanalyze.isPending || !interventions.data?.browser_session_id
                    }
                    onClick={() => reanalyze.mutate()}
                  >
                    {reanalyze.isPending ? (
                      <Spinner />
                    ) : (
                      <ScanSearch className="h-3.5 w-3.5" />
                    )}
                    Re-analyze
                  </Button>
                  {run?.can_pause ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={pauseFill.isPending}
                      onClick={() => pauseFill.mutate()}
                    >
                      <Pause className="h-3.5 w-3.5" />
                      Pause fill
                    </Button>
                  ) : null}
                  {run?.can_resume ? (
                    <Button
                      size="sm"
                      disabled={resumeFill.isPending}
                      onClick={() => resumeFill.mutate()}
                    >
                      <Play className="h-3.5 w-3.5" />
                      Resume fill
                    </Button>
                  ) : null}
                </div>
                {reanalyze.error || pauseFill.error || resumeFill.error ? (
                  <p className="text-[11px] text-red-300">
                    {describeError(
                      reanalyze.error ?? pauseFill.error ?? resumeFill.error,
                    )}
                  </p>
                ) : null}
              </div>

              <div className="space-y-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                <p className="text-[11px] uppercase tracking-[0.1em] text-ink-subtle">
                  Final review
                </p>
                {review.data?.execution_id ? (
                  <>
                    <p className="text-[12px] font-medium text-ink">
                      {review.data.ready_for_final_review
                        ? "Safe fields are ready for your review."
                        : `${review.data.blocker_count} blocker${
                            review.data.blocker_count === 1 ? "" : "s"
                          } remain.`}
                    </p>
                    <p className="text-[11px] text-ink-subtle">
                      {review.data.resume.filename
                        ? `Resume: ${review.data.resume.filename}`
                        : "No resume is selected."}{" "}
                      · {review.data.filled_count} filled ·{" "}
                      {review.data.verified_count} verified
                    </p>
                    {review.data.blocking_interventions.length > 0 ? (
                      <ul className="list-disc space-y-1 pl-4 text-[11px] text-amber-100/80">
                        {review.data.blocking_interventions
                          .slice(0, 4)
                          .map((blocker) => (
                            <li key={blocker}>{blocker}</li>
                          ))}
                      </ul>
                    ) : null}
                  </>
                ) : (
                  <p className="text-[11px] text-ink-subtle">
                    Complete a fill run to generate the review checklist.
                  </p>
                )}
                <p className="text-[11px] leading-relaxed text-ink-subtle">
                  {review.data?.final_submission_note ??
                    "ApplyPilot never clicks the final Submit/Apply button."}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
              <div className="flex flex-wrap gap-1.5">
                {session.data?.can_pause ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={pauseSession.isPending}
                    onClick={() => pauseSession.mutate()}
                  >
                    <Pause className="h-3.5 w-3.5" />
                    Pause browser
                  </Button>
                ) : null}
                {session.data?.can_resume ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={resumeSession.isPending}
                    onClick={() => resumeSession.mutate()}
                  >
                    <Play className="h-3.5 w-3.5" />
                    Resume browser
                  </Button>
                ) : null}
                {externalUrl ? (
                  <a
                    href={externalUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 text-[12px] font-medium text-ink-muted transition-colors hover:border-white/20 hover:text-ink"
                  >
                    Employer page
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </a>
                ) : null}
              </div>
              {endConfirm ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] text-ink-subtle">
                    End the browser session and close the page?
                  </span>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={cancelSession.isPending}
                    onClick={async () => {
                      try {
                        await cancelSession.mutateAsync();
                        disconnect("closed");
                        setEndConfirm(false);
                      } catch (cause) {
                        setError(describeError(cause));
                      }
                    }}
                  >
                    End session
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setEndConfirm(false)}
                  >
                    Keep it
                  </Button>
                </div>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEndConfirm(true)}
                >
                  <X className="h-3.5 w-3.5" />
                  End browser session
                </Button>
              )}
            </div>
          </div>
        )}

        <p className={cn("text-[11px] leading-relaxed text-ink-subtle")}>
          The stream shows the actual backend Playwright page - not a screenshot
          or a second browser. Closing this panel does not cancel the browser
          session. ApplyPilot never solves CAPTCHA, reads OTP/MFA codes, stores
          passwords or submits the application.
        </p>
      </CardContent>
    </Card>
  );
}
