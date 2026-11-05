"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { bffFetch } from "@/lib/api/client";

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      await bffFetch<{ signedOut: boolean }>("/api/auth/logout", {
        method: "POST",
      });
    },
    onSettled: () => {
      queryClient.clear();
      // Full navigation drops every cached server component for this user.
      window.location.assign("/login?signedOut=1");
    },
  });
}
