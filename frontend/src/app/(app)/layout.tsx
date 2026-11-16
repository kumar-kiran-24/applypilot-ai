import { redirect } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { getSessionToken, probeSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/**
 * The middleware only checks that a cookie exists; this layout is where the
 * token is actually validated against the backend before any data is loaded.
 */
export default async function ProtectedLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const token = await getSessionToken();
  if (!token) {
    redirect("/login");
  }

  const probe = await probeSession(token);
  if (probe.status === "unauthenticated") {
    // Only a token the backend actually rejected is an expired session.
    redirect("/login?expired=1");
  }
  if (probe.status === "unavailable") {
    // The API is down or restarting. Redirecting to the login screen here
    // claimed the user's session had expired and discarded their workspace.
    return (
      <main className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-md space-y-3 rounded-xl border border-white/10 bg-white/[0.02] p-6 text-center">
          <h1 className="text-lg font-semibold text-ink">
            ApplyPilot cannot reach its API
          </h1>
          <p className="text-sm text-ink-muted">
            Your sign-in is still valid. The ApplyPilot API did not answer, so
            this page could not load. If the backend was restarted, an open
            employer browser session may have been closed; once the API is back
            you will be asked to confirm reopening it.
          </p>
          <a
            className="inline-flex h-9 items-center rounded-lg bg-brand-blue px-4 text-sm font-medium text-white"
            href=""
          >
            Try again
          </a>
        </div>
      </main>
    );
  }

  return <AppShell user={probe.user}>{children}</AppShell>;
}
