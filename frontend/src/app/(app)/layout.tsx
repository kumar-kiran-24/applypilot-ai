import { redirect } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { getCurrentUser, getSessionToken } from "@/lib/auth/session";

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

  const user = await getCurrentUser(token);
  if (!user) {
    redirect("/login?expired=1");
  }

  return <AppShell user={user}>{children}</AppShell>;
}
