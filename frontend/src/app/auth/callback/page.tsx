import type { Metadata } from "next";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { Logo } from "@/components/layout/logo";
import { LoginCallbackView } from "@/features/auth/login-callback-view";
import { getCurrentUser, getSessionToken } from "@/lib/auth/session";
import { RETURN_TO_COOKIE, safeReturnPath } from "@/lib/utils/url";

export const metadata: Metadata = {
  title: "Signing in",
  robots: { index: false, follow: false },
};

export const dynamic = "force-dynamic";

/**
 * Dedicated login callback (`AUTH_FRONTEND_REDIRECT_URI`).
 *
 * The backend never returns a token here - only a one-time code (or a short
 * error identifier). `LoginCallbackView` exchanges the code through the BFF,
 * which sets the HttpOnly session cookie and normalises the failure states.
 */
function storedReturnPath(raw: string | undefined): string | null {
  if (!raw) return null;
  try {
    return safeReturnPath(decodeURIComponent(raw));
  } catch {
    return null;
  }
}

export default async function LoginCallbackPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const cookieStore = await cookies();
  const returnTo = storedReturnPath(cookieStore.get(RETURN_TO_COOKIE)?.value);

  const token = await getSessionToken();
  if (token && (await getCurrentUser(token))) {
    redirect(returnTo ?? "/dashboard");
  }

  const params = await searchParams;
  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center px-5 py-10">
      <div className="mb-8">
        <Logo />
      </div>
      <LoginCallbackView
        code={typeof params.code === "string" ? params.code : null}
        error={typeof params.error === "string" ? params.error : null}
        returnTo={returnTo}
      />
    </main>
  );
}
