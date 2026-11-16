import type { Metadata } from "next";

import { ApplyImportView } from "@/components/applications/apply-import-view";
import { ApplyReturnGate } from "@/components/applications/apply-return-gate";
import { Logo } from "@/components/layout/logo";
import { getCurrentUser, getSessionToken } from "@/lib/auth/session";
import { safeReturnPath } from "@/lib/utils/url";

export const metadata: Metadata = {
  title: "Apply to a job",
  robots: { index: false, follow: false },
};

export const dynamic = "force-dynamic";

/**
 * Direct job import route.
 *
 * This sits outside the protected `(app)` layout on purpose: an unauthenticated
 * visitor must be able to keep their `?url=` through Google sign-in instead of
 * being redirected to a bare login page.
 */
export default async function ApplyPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const rawUrl = typeof params.url === "string" ? params.url : null;
  const returnTo = rawUrl
    ? safeReturnPath(`/apply?url=${encodeURIComponent(rawUrl)}`)
    : null;

  const token = await getSessionToken();
  const user = token ? await getCurrentUser(token) : null;

  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center px-5 py-10">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -left-24 top-0 h-80 w-80 rounded-full bg-brand-blue/15 blur-[120px]"
      />
      <div className="relative mb-8">
        <Logo />
      </div>
      <div className="relative flex w-full justify-center animate-fade-in">
        {user ? (
          <ApplyImportView initialUrl={rawUrl} />
        ) : (
          <ApplyReturnGate returnTo={returnTo} />
        )}
      </div>
      <p className="relative mt-6 max-w-md text-center text-[11px] leading-relaxed text-ink-subtle">
        ApplyPilot never scrapes the listing, solves CAPTCHA, enters OTP/MFA
        codes or submits the final application.
      </p>
    </main>
  );
}
