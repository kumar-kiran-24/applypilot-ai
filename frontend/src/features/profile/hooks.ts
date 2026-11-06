"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addCertification,
  addEducation,
  addExperience,
  addProject,
  deleteCertification,
  deleteEducation,
  deleteExperience,
  deleteProject,
  deleteReusableAnswer,
  fetchProfile,
  fetchProfileCompleteness,
  fetchReusableAnswers,
  profileKeys,
  updateEducation,
  updateExperience,
  updatePreferences,
  updateProfile,
  updateProject,
  type CertificationInput,
  type EducationInput,
  type ExperienceInput,
  type PreferencesInput,
  type ProfileInput,
  type ProjectInput,
} from "@/features/profile/api";
import type {
  ApplicationAnswer,
  CandidateProfile,
  CertificationEntry,
  DeleteResponse,
  EducationEntry,
  ExperienceEntry,
  JobPreference,
  ProfileCompleteness,
  ProjectEntry,
} from "@/types/api";

async function invalidateProfile(queryClient: ReturnType<typeof useQueryClient>) {
  await queryClient.invalidateQueries({ queryKey: profileKeys.all });
}

export function useProfile() {
  return useQuery<CandidateProfile>({
    queryKey: profileKeys.detail,
    queryFn: fetchProfile,
    retry: false,
  });
}

export function useProfileCompleteness() {
  return useQuery<ProfileCompleteness>({
    queryKey: profileKeys.completeness,
    queryFn: fetchProfileCompleteness,
    retry: false,
  });
}

export function useReusableAnswers() {
  return useQuery<ApplicationAnswer[]>({
    queryKey: profileKeys.answers,
    queryFn: fetchReusableAnswers,
    retry: false,
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation<CandidateProfile, Error, ProfileInput>({
    mutationFn: updateProfile,
    onSuccess: async (data) => {
      queryClient.setQueryData(profileKeys.detail, data);
      await invalidateProfile(queryClient);
    },
  });
}

export function useUpdatePreferences() {
  const queryClient = useQueryClient();
  return useMutation<JobPreference, Error, PreferencesInput>({
    mutationFn: updatePreferences,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useAddEducation() {
  const queryClient = useQueryClient();
  return useMutation<EducationEntry, Error, EducationInput>({
    mutationFn: addEducation,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useUpdateEducation() {
  const queryClient = useQueryClient();
  return useMutation<
    EducationEntry,
    Error,
    { id: string; input: Partial<EducationInput> }
  >({
    mutationFn: ({ id, input }) => updateEducation(id, input),
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useDeleteEducation() {
  const queryClient = useQueryClient();
  return useMutation<DeleteResponse, Error, string>({
    mutationFn: deleteEducation,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useAddExperience() {
  const queryClient = useQueryClient();
  return useMutation<ExperienceEntry, Error, ExperienceInput>({
    mutationFn: addExperience,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useUpdateExperience() {
  const queryClient = useQueryClient();
  return useMutation<
    ExperienceEntry,
    Error,
    { id: string; input: Partial<ExperienceInput> }
  >({
    mutationFn: ({ id, input }) => updateExperience(id, input),
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useDeleteExperience() {
  const queryClient = useQueryClient();
  return useMutation<DeleteResponse, Error, string>({
    mutationFn: deleteExperience,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useAddProject() {
  const queryClient = useQueryClient();
  return useMutation<ProjectEntry, Error, ProjectInput>({
    mutationFn: addProject,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useUpdateProject() {
  const queryClient = useQueryClient();
  return useMutation<
    ProjectEntry,
    Error,
    { id: string; input: Partial<ProjectInput> }
  >({
    mutationFn: ({ id, input }) => updateProject(id, input),
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();
  return useMutation<DeleteResponse, Error, string>({
    mutationFn: deleteProject,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useAddCertification() {
  const queryClient = useQueryClient();
  return useMutation<CertificationEntry, Error, CertificationInput>({
    mutationFn: addCertification,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useDeleteCertification() {
  const queryClient = useQueryClient();
  return useMutation<DeleteResponse, Error, string>({
    mutationFn: deleteCertification,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}

export function useDeleteReusableAnswer() {
  const queryClient = useQueryClient();
  return useMutation<DeleteResponse, Error, string>({
    mutationFn: deleteReusableAnswer,
    onSuccess: async () => {
      await invalidateProfile(queryClient);
    },
  });
}
