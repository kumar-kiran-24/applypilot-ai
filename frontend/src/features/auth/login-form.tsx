"use client";

import { ShieldCheck, Sparkles } from "lucide-react";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { GoogleIcon } from "@/components/ui/google-icon";
import { Spinner } from "@/components/ui/spinner";
import { apiBaseUrl } from "@/lib/constants";

interface LoginFormProps {
  expired: boolean;
  signedOut: boolean;
}

export function LoginForm({ expired, signedOut }: LoginFormProps) {
  const [googlePending, setGooglePending] = useState(false);

  return (
    <div className="w-full max-w-md">
      <div className="glass-panel p-6 sm:p-8">
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Sign in to ApplyPilot
        </h1>
        <p className="mt-1.5 text-sm text-ink-muted">
          Use the Google account you want ApplyPilot to work with.
        </p>

        {expired ? (
          <Alert
            variant="warning"
            className="mt-5"
            title="Your session expired"
          >
            Sign in again to continue where you left off.
          </Alert>
        ) : null}
        {signedOut ? (
          <Alert
            variant="success"
            className="mt-5"
            title="You have been signed out"
          >
            The session cookie was cleared. The backend token expires on its own
            schedule.
          </Alert>
        ) : null}

        <a
          href={`${apiBaseUrl()}/auth/google/login`}
          onClick={() => setGooglePending(true)}
          aria-busy={googlePending}
          className="mt-6 flex h-12 w-full items-center justify-center gap-3 rounded-xl border border-white/15 bg-white text-[15px] font-medium text-slate-800 transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-brand-blue/60"
        >
          {googlePending ? (
            <Spinner className="text-slate-500" />
          ) : (
            <GoogleIcon className="h-5 w-5" />
          )}
          Continue with Google
        </a>

        <p className="mt-4 text-[11px] leading-relaxed text-ink-subtle">
          After you approve Google, ApplyPilot sends you back here through a
          one-time sign-in link and stores your session in an HttpOnly cookie.
        </p>

        <ul className="mt-5 space-y-2 text-[12px] text-ink-subtle">
          <li className="flex items-center gap-2">
            <ShieldCheck
              aria-hidden="true"
              className="h-3.5 w-3.5 text-sky-400"
            />
            Login requests only your basic Google profile.
          </li>
          <li className="flex items-center gap-2">
            <Sparkles
              aria-hidden="true"
              className="h-3.5 w-3.5 text-violet-400"
            />
            Gmail access is a separate, read-only permission.
          </li>
        </ul>
      </div>
    </div>
  );
}
