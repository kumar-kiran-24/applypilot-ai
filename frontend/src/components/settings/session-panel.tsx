"use client";

import { LogOut } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useLogout } from "@/features/auth/use-logout";

export function SessionPanel() {
  const logout = useLogout();

  return (
    <div className="space-y-4">
      <Alert variant="success" title="Secure session">
        Your ApplyPilot token is kept in an HttpOnly, SameSite=Lax cookie that
        JavaScript cannot read, and every backend call is made through this
        app&apos;s server. Sign-in completes through a one-time code that this
        app&apos;s server exchanges for the cookie, so the token never appears
        in a URL or browser storage.
      </Alert>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="danger"
          size="sm"
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
        >
          {logout.isPending ? (
            <>
              <Spinner className="h-3.5 w-3.5" />
              Signing out…
            </>
          ) : (
            <>
              <LogOut aria-hidden="true" className="h-3.5 w-3.5" />
              Sign out
            </>
          )}
        </Button>
        <p className="text-[11px] text-ink-subtle">
          Clears the cookie. The backend JWT itself expires after its configured
          lifetime.
        </p>
      </div>
    </div>
  );
}
