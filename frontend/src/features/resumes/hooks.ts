"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { jobKeys } from "@/features/jobs/api";
import {
  deleteResume,
  fetchResumes,
  resumeKeys,
  setDefaultResume,
  uploadResume,
} from "@/features/resumes/api";
import type { Resume } from "@/types/api";

/** Resume edits change stored match statistics and job card summaries. */
async function invalidateResumeAndMatches(
  queryClient: ReturnType<typeof useQueryClient>,
) {
  await queryClient.invalidateQueries({ queryKey: resumeKeys.all });
  await queryClient.invalidateQueries({ queryKey: jobKeys.summary });
  await queryClient.invalidateQueries({ queryKey: jobKeys.all });
}

export function useResumes() {
  return useQuery<Resume[]>({
    queryKey: resumeKeys.all,
    queryFn: fetchResumes,
  });
}

export function useUploadResume() {
  const queryClient = useQueryClient();
  return useMutation<Resume, Error, File>({
    mutationFn: uploadResume,
    onSuccess: async () => {
      await invalidateResumeAndMatches(queryClient);
    },
  });
}

export function useSetDefaultResume() {
  const queryClient = useQueryClient();
  return useMutation<Resume, Error, string>({
    mutationFn: setDefaultResume,
    onSuccess: async () => {
      await invalidateResumeAndMatches(queryClient);
    },
  });
}

export function useDeleteResume() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: deleteResume,
    onSuccess: async () => {
      await invalidateResumeAndMatches(queryClient);
    },
  });
}
