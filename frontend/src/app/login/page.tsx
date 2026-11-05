import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { Logo } from "@/components/layout/logo";
import { LoginForm } from "@/features/auth/login-form";
import { getCurrentUser, getSessionToken } from "@/lib/auth/session";

export const metadata: Metadata = {
  title: "Sign in",
};

export const dynamic = "force-dynamic";

const HIGHLIGHTS = [
  {
    title: "One account, one workspace",
    body: "Google sign-in creates a single ApplyPilot profile. No password to manage or store.",
  },
  {
    title: "Read-only job discovery",
    body: "Glassdoor alerts are parsed from your inbox. Emails are never modified and nothing is sent on your behalf.",
  },
  {
    title: "You stay in control",
    body: "Phase 1 never submits an application. Automatic applying arrives later, behind explicit confirmation.",
  },
];

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const token = await getSessionToken();
  if (token) {
    const user = await getCurrentUser(token);
    if (user) {
      redirect("/dashboard");
    }
  }

  const params = await searchParams;

  return (
    <main className="relative flex min-h-screen flex-col lg:flex-row">
      <section className="relative hidden flex-1 flex-col justify-between overflow-hidden border-r border-white/[0.06] p-10 lg:flex xl:p-14">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -left-28 -top-32 h-96 w-96 rounded-full bg-brand-blue/20 blur-[120px]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-24 right-0 h-80 w-80 rounded-full bg-brand-violet/20 blur-[120px]"
        />

        <Logo />

        <div className="relative max-w-lg">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-sky-300/80">
            Your Intelligent Job Application Agent
          </p>
          <h2 className="mt-4 text-4xl font-semibold leading-[1.15] tracking-tight text-ink xl:text-[42px]">
            Discover, analyze and match —{" "}
            <span className="brand-gradient-text">without the busywork.</span>
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-ink-muted">
            ApplyPilot reads the job alerts you already receive, keeps them
            organized, and prepares your applications. You decide what gets
            sent.
          </p>

          <ul className="mt-10 space-y-5">
            {HIGHLIGHTS.map((item) => (
              <li key={item.title} className="flex gap-3.5">
                <span
                  aria-hidden="true"
                  className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-gradient"
                />
                <div>
                  <p className="text-sm font-medium text-ink">{item.title}</p>
                  <p className="mt-1 text-[13px] leading-relaxed text-ink-muted">
                    {item.body}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-[11px] text-ink-subtle">
          ApplyPilot AI · Phase 1 · Gmail access is read-only (gmail.readonly)
        </p>
      </section>

      <section className="flex flex-1 flex-col items-center justify-center px-5 py-10 sm:px-8">
        <div className="mb-8 lg:hidden">
          <Logo />
        </div>
        <LoginForm
          expired={params.expired === "1"}
          signedOut={params.signedOut === "1"}
        />
        <p className="mt-6 max-w-md text-center text-[11px] leading-relaxed text-ink-subtle">
          By continuing you agree that ApplyPilot may read job-related email
          from the Google account you authorize. You can disconnect Gmail at any
          time.
        </p>
      </section>
    </main>
  );
}
