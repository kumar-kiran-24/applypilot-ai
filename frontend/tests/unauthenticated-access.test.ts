import { beforeEach, describe, expect, it, vi } from "vitest";

const { redirectMock, getSessionTokenMock, getCurrentUserMock } = vi.hoisted(
  () => ({
    redirectMock: vi.fn((path: string) => {
      throw new Error(`NEXT_REDIRECT:${path}`);
    }),
    getSessionTokenMock: vi.fn(),
    getCurrentUserMock: vi.fn(),
  }),
);

vi.mock("next/navigation", () => ({ redirect: redirectMock }));
vi.mock("@/lib/auth/session", () => ({
  getSessionToken: getSessionTokenMock,
  getCurrentUser: getCurrentUserMock,
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
    getCurrentUserMock.mockReset();
  });

  it("redirects to /login when there is no session cookie", async () => {
    getSessionTokenMock.mockResolvedValue(null);

    await expect(ProtectedLayout({ children: null })).rejects.toThrow(
      "NEXT_REDIRECT:/login",
    );
    expect(redirectMock).toHaveBeenCalledWith("/login");
    expect(getCurrentUserMock).not.toHaveBeenCalled();
  });

  it("redirects to /login?expired=1 when the backend rejects the token", async () => {
    getSessionTokenMock.mockResolvedValue("expired.jwt.token");
    getCurrentUserMock.mockResolvedValue(null);

    await expect(ProtectedLayout({ children: null })).rejects.toThrow(
      "NEXT_REDIRECT:/login?expired=1",
    );
    expect(redirectMock).toHaveBeenCalledWith("/login?expired=1");
  });

  it("renders the authenticated shell for a valid session", async () => {
    getSessionTokenMock.mockResolvedValue("valid.jwt.token");
    getCurrentUserMock.mockResolvedValue(USER);

    await expect(ProtectedLayout({ children: null })).resolves.toBeTruthy();
    expect(redirectMock).not.toHaveBeenCalled();
  });
});
