import { NextResponse } from "next/server";

import { getCurrentUser, getSessionToken } from "@/lib/auth/session";
import type { SessionResponse } from "@/types/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** GET: report whether this browser has a valid session (used by the client). */
export async function GET(): Promise<NextResponse<SessionResponse>> {
  const token = await getSessionToken();
  const user = token ? await getCurrentUser(token) : null;
  return NextResponse.json({ authenticated: user !== null, user });
}
