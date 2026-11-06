import { apiFetch } from "@/lib/api/client";
import type {
  ApplicationAnswer,
  CandidateProfile,
  CertificationEntry,
  DeleteResponse,
  EducationEntry,
  EmploymentType,
  ExperienceEntry,
  JobPreference,
  ProfileCompleteness,
  ProjectEntry,
  RelocationPreference,
  WorkMode,
} from "@/types/api";

export const profileKeys = {
  all: ["profile"] as const,
  detail: ["profile", "detail"] as const,
  completeness: ["profile", "completeness"] as const,
  answers: ["profile", "answers"] as const,
};

export interface ProfileInput {
  full_name?: string;
  preferred_name?: string | null;
  email?: string;
  phone?: string | null;
  phone_country_code?: string | null;
  location?: string | null;
  address_line1?: string | null;
  address_line2?: string | null;
  city?: string | null;
  region?: string | null;
  postal_code?: string | null;
  country?: string | null;
  linkedin_url?: string | null;
  github_url?: string | null;
  portfolio_url?: string | null;
  professional_summary?: string | null;
  skills?: string[];
  work_authorization?: string | null;
  requires_sponsorship?: boolean | null;
  notice_period_days?: number | null;
  available_from?: string | null;
}

export interface PreferencesInput {
  preferred_roles?: string[];
  preferred_locations?: string[];
  preferred_job_types?: EmploymentType[];
  preferred_work_modes?: WorkMode[];
  relocation_preference?: RelocationPreference;
  minimum_salary?: string | number | null;
  expected_salary?: string | number | null;
  currency?: string;
}

export interface EducationInput {
  institution: string;
  degree: string;
  specialization?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  cgpa?: string | number | null;
  description?: string | null;
}

export interface ExperienceInput {
  company: string;
  designation: string;
  employment_type?: EmploymentType | null;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  is_current?: boolean;
  description?: string | null;
  technologies?: string[];
}

export interface ProjectInput {
  name: string;
  description?: string | null;
  technologies?: string[];
  project_url?: string | null;
  start_date?: string | null;
  end_date?: string | null;
}

export interface CertificationInput {
  name: string;
  issuer?: string | null;
  issue_date?: string | null;
  expiry_date?: string | null;
  credential_id?: string | null;
  credential_url?: string | null;
}

export function fetchProfile(): Promise<CandidateProfile> {
  return apiFetch<CandidateProfile>("profile");
}

export function fetchProfileCompleteness(): Promise<ProfileCompleteness> {
  return apiFetch<ProfileCompleteness>("profile/completeness");
}

export function updateProfile(input: ProfileInput): Promise<CandidateProfile> {
  return apiFetch<CandidateProfile>("profile", { method: "PUT", body: input });
}

export function updatePreferences(input: PreferencesInput): Promise<JobPreference> {
  return apiFetch<JobPreference>("profile/preferences", {
    method: "PUT",
    body: input,
  });
}

export function addEducation(input: EducationInput): Promise<EducationEntry> {
  return apiFetch<EducationEntry>("profile/education", {
    method: "POST",
    body: input,
  });
}

export function updateEducation(
  id: string,
  input: Partial<EducationInput>,
): Promise<EducationEntry> {
  return apiFetch<EducationEntry>(`profile/education/${id}`, {
    method: "PATCH",
    body: input,
  });
}

export function deleteEducation(id: string): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(`profile/education/${id}`, {
    method: "DELETE",
  });
}

export function addExperience(input: ExperienceInput): Promise<ExperienceEntry> {
  return apiFetch<ExperienceEntry>("profile/experience", {
    method: "POST",
    body: input,
  });
}

export function updateExperience(
  id: string,
  input: Partial<ExperienceInput>,
): Promise<ExperienceEntry> {
  return apiFetch<ExperienceEntry>(`profile/experience/${id}`, {
    method: "PATCH",
    body: input,
  });
}

export function deleteExperience(id: string): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(`profile/experience/${id}`, {
    method: "DELETE",
  });
}

export function addProject(input: ProjectInput): Promise<ProjectEntry> {
  return apiFetch<ProjectEntry>("profile/projects", {
    method: "POST",
    body: input,
  });
}

export function updateProject(
  id: string,
  input: Partial<ProjectInput>,
): Promise<ProjectEntry> {
  return apiFetch<ProjectEntry>(`profile/projects/${id}`, {
    method: "PATCH",
    body: input,
  });
}

export function deleteProject(id: string): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(`profile/projects/${id}`, {
    method: "DELETE",
  });
}

export function addCertification(
  input: CertificationInput,
): Promise<CertificationEntry> {
  return apiFetch<CertificationEntry>("profile/certifications", {
    method: "POST",
    body: input,
  });
}

export function deleteCertification(id: string): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(`profile/certifications/${id}`, {
    method: "DELETE",
  });
}

export function fetchReusableAnswers(): Promise<ApplicationAnswer[]> {
  return apiFetch<ApplicationAnswer[]>("profile/answers");
}

export function deleteReusableAnswer(key: string): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(`profile/answers/${key}`, {
    method: "DELETE",
  });
}
