import { beforeEach, describe, expect, it, vi } from "vitest";

const { redirectMock, getSessionTokenMock, probeSessionMock } = vi.hoisted(
  () => ({
    redirectMock: vi.fn((path: string) => {
      throw new Error(`NEXT_REDIRECT:${path}`);
    }),
    getSessionTokenMock: vi.fn(),
    probeSessionMock: vi.fn(),
  }),
);

vi.mock("next/navigation", () => ({ redirect: redirectMock }));
vi.mock("@/lib/auth/session", () => ({
  getSessionToken: getSessionTokenMock,
  probeSession: probeSessionMock,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: () => null,
}));

import ProtectedLayout from "@/app/(app)/layout";

const USER = {
  id: "u1",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  profile_picture: null,
};

describe("unauthenticated dashboard access", () => {
  beforeEach(() => {
    redirectMock.mockClear();
    getSessionTokenMock.mockReset();
    probeSessionMock.mockReset();
  });

  it("redirects to /login when there is no session cookie", async () => {
    getSessionTokenMock.mockResolvedValue(null);

    await expect(ProtectedLayout({ children: null })).rejects.toThrow(
      "NEXT_REDIRECT:/login",
    );
    expect(redirectMock).toHaveBeenCalledWith("/login");
    expect(probeSessionMock).not.toHaveBeenCalled();
  });

  it("redirects to /login?expired=1 when the backend rejects the token", async () => {
    getSessionTokenMock.mockResolvedValue("expired.jwt.token");
    probeSessionMock.mockResolvedValue({ status: "unauthenticated" });

    await expect(ProtectedLayout({ children: null })).rejects.toThrow(
      "NEXT_REDIRECT:/login?expired=1",
    );
    expect(redirectMock).toHaveBeenCalledWith("/login?expired=1");
  });

  it("keeps the user signed in when the API is unreachable", async () => {
    // A restart or outage is not an expired session: redirecting here signed
    // the user out and discarded the workspace they were working in.
    getSessionTokenMock.mockResolvedValue("valid.jwt.token");
    probeSessionMock.mockResolvedValue({ status: "unavailable" });

    const rendered = await ProtectedLayout({ children: null });

    expect(rendered).toBeTruthy();
    expect(redirectMock).not.toHaveBeenCalled();
  });

  it("renders the authenticated shell for a valid session", async () => {
    getSessionTokenMock.mockResolvedValue("valid.jwt.token");
    probeSessionMock.mockResolvedValue({ status: "authenticated", user: USER });

    await expect(ProtectedLayout({ children: null })).resolves.toBeTruthy();
    expect(redirectMock).not.toHaveBeenCalled();
  });
});
