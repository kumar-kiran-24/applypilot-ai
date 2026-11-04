"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  disconnectGmail,
  fetchGmailStatus,
  gmailKeys,
  startGmailConnection,
} from "@/features/gmail/api";
import type { GmailConnectResponse, GmailDisconnectResponse, GmailStatus } from "@/types/api";

export function useGmailStatus() {
  return useQuery<GmailStatus>({
    queryKey: gmailKeys.status,
    queryFn: fetchGmailStatus,
  });
}

/**
 * On success the whole page navigates to Google's consent screen, so this
 * mutation intentionally keeps its data instead of resetting on settle.
 */
export function useConnectGmail() {
  return useMutation<GmailConnectResponse, Error, void>({
    mutationFn: startGmailConnection,
    onSuccess: (data) => {
      window.location.assign(data.authorization_url);
    },
  });
}

export function useDisconnectGmail() {
  const queryClient = useQueryClient();
  return useMutation<GmailDisconnectResponse, Error, void>({
    mutationFn: disconnectGmail,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: gmailKeys.status });
      queryClient.removeQueries({ queryKey: ["gmail", "glassdoor"] });
    },
  });
}
