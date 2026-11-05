/** Types mirroring the FastAPI schemas in `backend/app/schemas`. */

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  profile_picture: string | null;
}

export interface GmailStatus {
  connected: boolean;
  is_active: boolean;
  gmail_email: string | null;
  connected_at: string | null;
  granted_scopes: string[];
}

export interface GmailConnectResponse {
  authorization_url: string;
  required_scopes: string[];
  message: string;
}

export interface GmailDisconnectResponse {
  disconnected: boolean;
  token_revoked: boolean;
  message: string;
}

export interface GlassdoorJob {
  job_title: string;
  company: string | null;
  location: string | null;
  job_url: string;
  email_subject: string;
  received_at: string | null;
  job_description: string | null;
  message_id: string;
}

export interface GlassdoorJobsResponse {
  messages_scanned: number;
  jobs_found: number;
  jobs: GlassdoorJob[];
}

export type MatchConfidence = "high" | "medium" | "low";

export interface JobRequirements {
  required_skills: string[];
  preferred_skills: string[];
  mentioned_skills: string[];
  experience_years: number | null;
  education_level: string | null;
  education_field: string | null;
  responsibilities: string[];
  has_description: boolean;
  skills_source: string;
  confidence: MatchConfidence;
}

export interface BestMatchSummary {
  resume_id: string;
  resume_filename: string | null;
  overall_score: number;
  skill_score: number | null;
  rank: number;
  explanation: string;
}

export interface Job {
  id: string;
  job_title: string;
  company: string | null;
  location: string | null;
  job_url: string;
  job_description: string | null;
  email_subject: string;
  received_at: string | null;
  source: string;
  discovered_at: string;
  requirements: JobRequirements;
  best_match: BestMatchSummary | null;
  matches_count: number;
}

export interface JobListResponse {
  total: number;
  jobs: Job[];
}

export interface DiscoverJobsResponse {
  jobs_found: number;
  jobs_created: number;
  jobs_updated: number;
  jobs: Job[];
}

export interface MatchScoreBreakdown {
  skills: number | null;
  experience: number | null;
  education: number | null;
  title: number | null;
  projects: number | null;
}

export interface ResumeMatch {
  resume_id: string;
  resume_filename: string;
  rank: number;
  is_recommended: boolean;
  overall_score: number;
  scores: MatchScoreBreakdown;
  matched_required_skills: string[];
  matched_preferred_skills: string[];
  missing_required_skills: string[];
  missing_preferred_skills: string[];
  strengths: string[];
  suggestions: string[];
  explanation: string;
  confidence: MatchConfidence;
  computed_at: string;
}

export interface JobMatchesResponse {
  job: Job;
  matches: ResumeMatch[];
  recommended_resume_id: string | null;
  resumes_evaluated: number;
  computed_at: string | null;
  disclaimer: string;
  message: string | null;
}

export interface BulkMatchRequest {
  job_ids?: string[];
  limit?: number;
  force?: boolean;
}

export interface BulkMatchResult {
  job_id: string;
  job_title: string;
  company: string | null;
  best_match: BestMatchSummary | null;
  matches_count: number;
  resumes_evaluated: number;
  computed_at: string | null;
}

export interface BulkMatchResponse {
  matched_jobs: number;
  results: BulkMatchResult[];
  disclaimer: string;
}

export interface ResumeMatchStat {
  resume_id: string;
  resume_filename: string;
  is_default: boolean;
  recommended_jobs: number;
  matches_count: number;
  average_score: number | null;
}

export interface MatchingSummary {
  jobs_total: number;
  jobs_matched: number;
  resumes_total: number;
  resumes_matched: number;
  average_best_score: number | null;
  best_match: {
    job_id: string;
    job_title: string;
    company: string | null;
    resume_id: string;
    resume_filename: string | null;
    overall_score: number;
  } | null;
  resume_stats: ResumeMatchStat[];
  last_computed_at: string | null;
  disclaimer: string;
}

export type ResumeParsingStatus =
  | "pending"
  | "processing"
  | "completed"
  | "ocr_required"
  | "failed";

export interface ParsedEducationEntry {
  institution: string | null;
  degree: string | null;
  field_of_study: string | null;
  start_date: string | null;
  end_date: string | null;
  details: string | null;
}

export interface ParsedExperienceEntry {
  title: string | null;
  company: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  description: string | null;
}

export interface ParsedProjectEntry {
  name: string;
  description: string | null;
  technologies: string[];
  url: string | null;
}

export interface ParsedResumeData {
  full_name: string | null;
  email: string | null;
  phone: string | null;
  professional_summary: string | null;
  education: ParsedEducationEntry[];
  experience: ParsedExperienceEntry[];
  projects: ParsedProjectEntry[];
  certifications: string[];
  links: string[];
}

export interface Resume {
  id: string;
  original_filename: string;
  file_size: number;
  content_type: string;
  is_default: boolean;
  parsing_status: ResumeParsingStatus;
  parsing_error: string | null;
  parsed_at: string | null;
  uploaded_at: string;
  skills: string[];
  parsed_data: ParsedResumeData;
}

/** Body of `GET /api/auth/session` (frontend BFF, not a backend route). */
export interface SessionResponse {
  authenticated: boolean;
  user: AuthUser | null;
}

export interface ValidationDetail {
  loc: Array<string | number>;
  msg: string;
}
