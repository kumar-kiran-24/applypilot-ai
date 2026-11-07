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

export type ApplicationStatus =
  | "draft"
  | "analyzing"
  | "awaiting_user_information"
  | "ready_for_review"
  | "awaiting_approval"
  | "submitting"
  | "submitted"
  | "failed"
  | "needs_user_action"
  | "cancelled";

export type ApplicationStage =
  | "applied"
  | "screening"
  | "interview"
  | "offer"
  | "rejected"
  | "withdrawn";

export type AutomationMode = "automated" | "assisted" | "manual";
export type InspectionMode = "automated" | "declared" | "manual";
export type AnswerSource =
  | "profile"
  | "resume"
  | "previously_approved"
  | "ai_suggestion"
  | "user_input";
export type QuestionStatus = "missing" | "suggested" | "confirmed";
export type QuestionType =
  | "text"
  | "textarea"
  | "email"
  | "phone"
  | "url"
  | "number"
  | "date"
  | "boolean"
  | "single_choice"
  | "multi_choice"
  | "radio"
  | "checkbox"
  | "file"
  | "declaration";

export interface ApplicationQuestion {
  id: string;
  question_key: string;
  field_identifier: string | null;
  section: string;
  question_text: string;
  help_text: string | null;
  field_type: QuestionType;
  required: boolean;
  options: string[];
  suggested_answer: string | null;
  answer: string | null;
  answer_source: AnswerSource;
  confidence: number;
  requires_confirmation: boolean;
  is_sensitive: boolean;
  is_legal: boolean;
  status: QuestionStatus;
  note: string | null;
}

export interface ApplicationSection {
  section: string;
  label: string;
  questions: ApplicationQuestion[];
}

export interface ApplicationPreview {
  application_id: string;
  job_id: string;
  job_title: string;
  company: string | null;
  platform: string;
  platform_name: string;
  application_url: string;
  automation_mode: AutomationMode;
  inspection_mode: InspectionMode;
  status: ApplicationStatus;
  stage: ApplicationStage | null;
  approved_at: string | null;
  submitted_at: string | null;
  submission_reference: string | null;
  failure_reason: string | null;
  requires_account: boolean;
  notes: string[];
  resume: {
    id: string | null;
    filename: string | null;
    reason: string;
    score: number | null;
  };
  sections: ApplicationSection[];
  missing_required: Array<{
    question_key: string;
    question_text: string;
  }>;
  progress: {
    total: number;
    confirmed: number;
    required_total: number;
    required_confirmed: number;
    percent: number;
  };
  ready_to_approve: boolean;
  can_submit: boolean;
  submission_mode: "automated" | "manual";
  manual_steps: string[];
  warnings: string[];
}

export interface ApplicationSummary {
  id: string;
  job_id: string;
  job_title: string;
  company: string | null;
  platform: string;
  platform_name: string;
  application_url: string;
  status: ApplicationStatus;
  stage: ApplicationStage | null;
  automation_mode: AutomationMode;
  requires_account: boolean;
  resume_filename: string | null;
  questions_total: number;
  questions_confirmed: number;
  missing_required_count: number;
  created_at: string | null;
  updated_at: string | null;
  approved_at: string | null;
  submitted_at: string | null;
}

export interface ApplicationListResponse {
  total: number;
  applications: ApplicationSummary[];
}

export interface SubmissionResponse {
  submitted: boolean;
  manual_required: boolean;
  message: string;
  reference: string | null;
  steps: string[];
  application: ApplicationPreview;
}

export interface ApplicationPlatformInfo {
  platform: string;
  display_name: string;
  supports_automation: boolean;
  requires_account: boolean | null;
  documentation_url: string;
  notes: string[];
  declared_questions: Array<{
    question_key: string;
    question_text: string;
    field_type: QuestionType;
    required: boolean;
  }>;
}

export interface PlatformCatalogResponse {
  platforms: ApplicationPlatformInfo[];
}

export type EmploymentType =
  | "full_time"
  | "part_time"
  | "contract"
  | "internship"
  | "freelance"
  | "temporary";

export type WorkMode = "onsite" | "hybrid" | "remote";
export type RelocationPreference = "yes" | "no" | "negotiable" | "unknown";

export interface EducationEntry {
  id: string;
  candidate_id: string;
  institution: string;
  degree: string;
  specialization: string | null;
  start_date: string | null;
  end_date: string | null;
  cgpa: string | number | null;
  description: string | null;
}

export interface ExperienceEntry {
  id: string;
  candidate_id: string;
  company: string;
  designation: string;
  employment_type: EmploymentType | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  description: string | null;
  technologies: string[];
}

export interface ProjectEntry {
  id: string;
  candidate_id: string;
  name: string;
  description: string | null;
  technologies: string[];
  project_url: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface CertificationEntry {
  id: string;
  name: string;
  issuer: string | null;
  issue_date: string | null;
  expiry_date: string | null;
  credential_id: string | null;
  credential_url: string | null;
  created_at: string | null;
}

export interface JobPreference {
  id: string;
  candidate_id: string;
  preferred_roles: string[];
  preferred_locations: string[];
  preferred_job_types: EmploymentType[];
  preferred_work_modes: WorkMode[];
  relocation_preference: RelocationPreference;
  minimum_salary: string | number | null;
  expected_salary: string | number | null;
  currency: string;
  created_at: string;
  updated_at: string;
}

export type AnswerType =
  | "text"
  | "number"
  | "boolean"
  | "date"
  | "single_choice"
  | "multi_choice"
  | "url";

export type VerificationStatus = "verified" | "derived" | "unknown";

export interface ApplicationAnswer {
  id: string;
  candidate_id: string;
  question_key: string;
  question_text: string;
  answer: string | null;
  answer_type: AnswerType;
  verification_status: VerificationStatus;
  is_sensitive: boolean;
  is_legal: boolean;
  verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CandidateProfile {
  id: string;
  full_name: string;
  preferred_name: string | null;
  email: string;
  phone: string | null;
  phone_country_code: string | null;
  location: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  region: string | null;
  postal_code: string | null;
  country: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  portfolio_url: string | null;
  professional_summary: string | null;
  skills: string[];
  work_authorization: string | null;
  requires_sponsorship: boolean | null;
  notice_period_days: number | null;
  available_from: string | null;
  education: EducationEntry[];
  experiences: ExperienceEntry[];
  projects: ProjectEntry[];
  preference: JobPreference | null;
  certifications: CertificationEntry[];
  application_answers: ApplicationAnswer[];
  created_at: string;
  updated_at: string;
}

export interface ProfileFieldStatus {
  field: string;
  label: string;
  section: string;
  required: boolean;
  complete: boolean;
}

export interface ProfileCompleteness {
  percent: number;
  completed: number;
  total: number;
  missing_fields: string[];
  missing_required: string[];
  fields: ProfileFieldStatus[];
}

export interface DeleteResponse {
  id?: string;
  question_key?: string;
  deleted: boolean;
}

export type MissingInformationQueueStatus = "pending" | "ready_to_complete";

export type MissingInformationItemStatus =
  | "missing"
  | "suggested"
  | "stale"
  | "sensitive_confirmation"
  | "legal_confirmation"
  | "resolved"
  | "dismissed";

/** The matcher can attribute a suggestion to a pending confirmation. */
export type MissingInformationAnswerSource =
  | AnswerSource
  | "needs_confirmation";

export interface MissingInformationItem {
  question_key: string;
  question_text: string;
  section: string;
  field_type: QuestionType;
  required: boolean;
  status: MissingInformationItemStatus;
  reason: string;
  answer: string | null;
  answer_source: MissingInformationAnswerSource | null;
  suggested_answer: string | null;
  suggested_source: MissingInformationAnswerSource | null;
  confidence: number;
  requires_confirmation: boolean;
  is_sensitive: boolean;
  is_legal: boolean;
  blocks_application: boolean;
  source_record_id: string | null;
  last_confirmed_at: string | null;
  options: string[];
}

export type ConflictResolution = "keep_stored" | "use_profile" | "edited";

export interface MissingInformationConflict {
  question_key: string;
  question_text: string;
  category: string | null;
  stored_answer: string;
  conflicting_answer: string;
  conflicting_source: string;
  reason: string;
  resolution_options: ConflictResolution[];
  blocks_application: boolean;
}

export interface MissingInformationSummary {
  application_id: string;
  job_id: string;
  job_title: string;
  company: string | null;
  application_url: string;
  application_status: ApplicationStatus;
  queue_status: MissingInformationQueueStatus;
  ready_to_complete: boolean;
  required_total: number;
  required_resolved: number;
  required_remaining: number;
  optional_unanswered: number;
  stale_count: number;
  conflict_count: number;
  sensitive_confirmation_count: number;
  matched_count: number;
  progress_percent: number;
  last_recalculated_at: string | null;
  resolved_at: string | null;
}

export interface MissingInformationDetail extends MissingInformationSummary {
  required_questions: MissingInformationItem[];
  optional_questions: MissingInformationItem[];
  matched_questions: MissingInformationItem[];
  stale_answers: MissingInformationItem[];
  sensitive_confirmations: MissingInformationItem[];
  dismissed_questions: MissingInformationItem[];
  conflicts: MissingInformationConflict[];
}

export interface MissingInformationQueueResponse {
  total: number;
  applications: MissingInformationSummary[];
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
