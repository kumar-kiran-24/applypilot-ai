"use client";

import { ArrowRight, KeyRound, ShieldCheck, Sparkles } from "lucide-react";
import { useState } from "react";

import { GoogleIcon } from "@/components/ui/google-icon";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { bffFetch } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { apiBaseUrl } from "@/lib/constants";

const JWT_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

interface LoginFormProps {
  nextPath: string;
  expired: boolean;
  signedOut: boolean;
}

export function LoginForm({ nextPath, expired, signedOut }: LoginFormProps) {
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [googlePending, setGooglePending] = useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = token.trim();

    if (!JWT_PATTERN.test(value)) {
      setError(
        "Enter the full access token from the callback page (three dot-separated parts).",
      );
      return;
    }

    setError(null);
    setPending(true);
    try {
      await bffFetch<{ authenticated: boolean }>("/api/auth/session", {
        method: "POST",
        body: { token: value },
      });
      setToken("");
      window.location.assign(nextPath);
    } catch (submitError) {
      setError(describeError(submitError));
      setPending(false);
    }
  };

  return (
    <div className="w-full max-w-md">
      <div className="glass-panel p-6 sm:p-8">
        <h1 className="text-xl font-semibold tracking-tight text-ink">Sign in to ApplyPilot</h1>
        <p className="mt-1.5 text-sm text-ink-muted">
          Use the Google account you want ApplyPilot to work with.
        </p>

        {expired ? (
          <Alert variant="warning" className="mt-5" title="Your session expired">
            Sign in again to continue where you left off.
          </Alert>
        ) : null}
        {signedOut ? (
          <Alert variant="success" className="mt-5" title="You have been signed out">
            The session cookie was cleared. The backend token expires on its own schedule.
          </Alert>
        ) : null}

        <a
          href={`${apiBaseUrl()}/auth/google/login`}
          onClick={() => setGooglePending(true)}
          aria-busy={googlePending}
          className="mt-6 flex h-12 w-full items-center justify-center gap-3 rounded-xl border border-white/15 bg-white text-[15px] font-medium text-slate-800 transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-brand-blue/60"
        >
          {googlePending ? <Spinner className="text-slate-500" /> : <GoogleIcon className="h-5 w-5" />}
          Continue with Google
        </a>

        <ul className="mt-5 space-y-2 text-[12px] text-ink-subtle">
          <li className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="h-3.5 w-3.5 text-sky-400" />
            Login requests only your basic Google profile.
          </li>
          <li className="flex items-center gap-2">
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5 text-violet-400" />
            Gmail access is a separate, read-only permission.
          </li>
        </ul>

        <div className="mt-8 border-t border-white/[0.07] pt-6">
          <div className="flex items-center gap-2">
            <KeyRound aria-hidden="true" className="h-4 w-4 text-ink-muted" />
            <h2 className="text-sm font-semibold text-ink">Development sign-in bridge</h2>
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-ink-muted">
            The backend&apos;s Google callback currently renders its JSON response instead of
            redirecting here, so the token cannot be captured automatically yet. Until that
            handoff is agreed, complete the steps below. The token is exchanged for an
            HttpOnly cookie and is never stored in localStorage or printed to the console.
          </p>
          <ol className="mt-3 list-decimal space-y-1 pl-4 text-[12px] leading-relaxed text-ink-subtle">
            <li>Choose &ldquo;Continue with Google&rdquo; above and approve the consent screen.</li>
            <li>
              On the callback page, copy the value of <code className="text-ink-muted">access_token</code>.
            </li>
            <li>Paste it here to open your dashboard.</li>
          </ol>

          <form onSubmit={handleSubmit} className="mt-4 space-y-3" noValidate>
            <label htmlFor="access-token" className="block text-[12px] font-medium text-ink-muted">
              ApplyPilot access token
            </label>
            <input
              id="access-token"
              name="token"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              aria-describedby="access-token-hint"
              placeholder="eyJhbGciOi…"
              className="field font-mono text-[12px]"
              disabled={pending}
            />
            <p id="access-token-hint" className="text-[11px] text-ink-subtle">
              Disabled in production builds. See the README for the recommended secure handoff.
            </p>

            {error ? (
              <Alert variant="danger" role="alert">
                {error}
              </Alert>
            ) : null}

            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Verifying session…
                </>
              ) : (
                <>
                  Open dashboard
                  <ArrowRight aria-hidden="true" className="h-4 w-4" />
                </>
              )}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
