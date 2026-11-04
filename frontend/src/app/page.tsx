import { redirect } from "next/navigation";

import { getCurrentUser, getSessionToken } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function RootPage() {
  const token = await getSessionToken();
  if (token) {
    const user = await getCurrentUser(token);
    if (user) {
      redirect("/dashboard");
    }
  }
  redirect("/login");
}
