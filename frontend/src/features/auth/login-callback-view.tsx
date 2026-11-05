"use client";

import Link from "next/link";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { buttonClasses } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { toApiError } from "@/lib/api/errors";
import { loginErrorMessage } from "@/lib/auth/login-errors";

interface LoginCallbackViewProps {
  code: string | null;
  error: string | null;
}

const NETWORK_FAILURE =
  "The ApplyPilot API could not be reached. Check that the backend is running and try again.";

/**
 * Completes the login handoff.
 *
 * The backend redirects here with a single-use code. This component posts it to
 * this app's own BFF route, which exchanges it for the JWT and stores it in the
 * HttpOnly `ap_session` cookie. The token never reaches this component; the
 * code is not persisted anywhere.
 */
export function LoginCallbackView({ code, error }: LoginCallbackViewProps) {
  const started = useRef(false);
  const [failure, setFailure] = useState<string | null>(() => {
    if (error) {
      return loginErrorMessage(error);
    }
    if (!code) {
      return "This sign-in link is incomplete. Start again from the login page.";
    }
    return null;
  });

  useEffect(() => {
    if (!code || error || started.current || failure) {
      return;
    }
    started.current = true;

    let active = true;
    void (async () => {
      try {
        const response = await fetch("/api/auth/exchange", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code }),
          credentials: "same-origin",
          cache: "no-store",
        });

        if (!response.ok) {
          const apiError = await toApiError(response);
          throw apiError;
        }

        // `replace` keeps the one-time code out of the browser history.
        window.location.replace("/dashboard");
      } catch (exchangeError) {
        if (!active) {
          return;
        }
        setFailure(
          exchangeError instanceof TypeError
            ? NETWORK_FAILURE
            : exchangeError instanceof Error && exchangeError.message
              ? exchangeError.message
              : "Sign-in could not be completed. Start again from the login page.",
        );
      }
    })();

    return () => {
      active = false;
    };
  }, [code, error, failure]);

  return (
    <div className="glass-panel w-full max-w-md p-6 sm:p-8">
      <h1 className="text-xl font-semibold tracking-tight text-ink">
        {failure ? "Sign-in failed" : "Signing you in"}
      </h1>

      {failure ? (
        <>
          <Alert variant="danger" className="mt-4" title="Google sign-in failed">
            {failure}
          </Alert>
          <div className="mt-5 flex items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 text-[12px] leading-relaxed text-ink-muted">
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 h-4 w-4 shrink-0 text-amber-300"
            />
            <p>
              Sign-in links are valid for 60 seconds and can be used only once.
              Use the same host (127.0.0.1, not localhost) for the app and the
              API.
            </p>
          </div>
          <Link
            href="/login"
            className={buttonClasses({ className: "mt-5 w-full" })}
          >
            Back to sign in
          </Link>
        </>
      ) : (
        <>
          <p className="mt-1.5 flex items-center gap-2 text-sm text-ink-muted">
            <Spinner className="h-4 w-4 text-sky-300" />
            Completing the secure handoff…
          </p>
          <ul className="mt-5 space-y-2 text-[12px] text-ink-subtle">
            <li className="flex items-center gap-2">
              <ShieldCheck aria-hidden="true" className="h-3.5 w-3.5 text-sky-400" />
              Your session is stored in an HttpOnly cookie, never in this page or
              browser storage.
            </li>
          </ul>
        </>
      )}
    </div>
  );
}
