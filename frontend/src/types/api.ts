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
  job_listing_url: string | null;
  employer_careers_url: string | null;
  application_url_is_job_board: boolean;
  suggested_application_url: string | null;
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

export type WorkflowStage =
  | "discovered"
  | "matched"
  | "application_created"
  | "needs_information"
  | "needs_approval"
  | "approved"
  | "browser_session_ready"
  | "form_analysis_pending"
  | "filling"
  | "paused_for_user"
  | "waiting_for_confirmation"
  | "ready_for_final_review"
  | "awaiting_user_submission"
  | "submitted"
  | "failed"
  | "cancelled"
  | "session_expired";

export type SubmissionPhase =
  | "ready_for_final_review"
  | "awaiting_user_submission"
  | "user_confirmed_submitted"
  | "submission_confirmed";

export interface WorkflowAction {
  action: string;
  label: string;
  enabled: boolean;
  reason: string | null;
}

export interface ApplicationWorkflow {
  application_id: string;
  job_id: string;
  job_title: string;
  company: string | null;
  application_url: string;
  status: ApplicationStatus;
  workflow_stage: WorkflowStage;
  submission_phase: SubmissionPhase | null;
  ready_for_final_review: boolean;
  missing_required_count: number;
  blocking_items: string[];
  next_actions: WorkflowAction[];
  browser: {
    session_id: string | null;
    run_number: number | null;
    state: string | null;
    browser_status: string | null;
    current_url: string | null;
    live: boolean;
    expired: boolean;
    view_available: boolean;
  };
  analysis: {
    analysis_id: string | null;
    version: number | null;
    field_count: number;
    page_url: string | null;
    analyzed_at: string | null;
  };
  execution: {
    execution_id: string | null;
    execution_status: string | null;
    run_number: number | null;
    analysis_version: number | null;
    succeeded_count: number;
    verified_count: number;
    failed_count: number;
    skipped_count: number;
    pending_confirmation_count: number;
    can_pause: boolean;
    can_resume: boolean;
  };
  interventions: {
    total: number;
    pending_count: number;
    user_working_count: number;
    blocking_count: number;
  };
  submission: {
    confirmed_by_user: boolean;
    confirmed_at: string | null;
    reference: string | null;
    confirmation_url: string | null;
    confirmation_message: string | null;
    employer_confirmation_detected: boolean;
  };
  recovery: string[];
  as_of: string;
  final_submission_note: string;
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

export type EligibilityStatus =
  | "eligible"
  | "needs_information"
  | "needs_confirmation"
  | "needs_user_approval"
  | "ineligible"
  | "duplicate"
  | "already_applied"
  | "unsupported_platform"
  | "failed";

export type EligibilityDecision =
  | "ready_to_prepare"
  | "ready_for_user_review"
  | "blocked";

export type AutoApplyWorkMode = WorkMode;

export interface AutoApplySettings {
  id: string;
  automation_enabled: boolean;
  require_user_approval: boolean;
  require_user_review: boolean;
  max_applications_per_day: number;
  minimum_resume_match_percentage: number;
  preferred_job_titles: string[];
  excluded_job_titles: string[];
  preferred_companies: string[];
  excluded_companies: string[];
  preferred_locations: string[];
  work_modes: WorkMode[];
  employment_types: EmploymentType[];
  minimum_salary: string | number | null;
  maximum_salary: string | number | null;
  currency: string;
  relocation_willingness: RelocationPreference;
  created_at: string;
  updated_at: string;
}

export interface AutoApplySettingsInput {
  automation_enabled?: boolean;
  require_user_approval?: boolean;
  require_user_review?: boolean;
  max_applications_per_day?: number;
  minimum_resume_match_percentage?: number;
  preferred_job_titles?: string[];
  excluded_job_titles?: string[];
  preferred_companies?: string[];
  excluded_companies?: string[];
  preferred_locations?: string[];
  work_modes?: WorkMode[];
  employment_types?: EmploymentType[];
  minimum_salary?: string | number | null;
  maximum_salary?: string | number | null;
  currency?: string;
  relocation_willingness?: RelocationPreference;
}

export interface EligibilityCheck {
  name: string;
  label: string;
  status: EligibilityStatus;
  explanation: string;
  blocking: boolean;
  source: string;
  suggested_resolution: string;
  details: Record<string, unknown>;
}

export interface EligibilityApproval {
  approved: boolean;
  current: boolean;
  approved_at: string | null;
  approved_by_user_id: string | null;
  report_version: string | null;
  report_decision: string | null;
}

export interface EligibilityReport {
  application_id: string;
  job_id: string;
  job_title: string;
  company: string | null;
  platform: string;
  platform_name: string;
  application_url: string;
  application_status: ApplicationStatus;
  resume_selected: boolean;
  resume_id: string | null;
  resume_filename: string | null;
  resume_match_score: number | null;
  overall_decision: EligibilityDecision;
  readiness_percentage: number;
  message: string;
  blocking_issues: EligibilityCheck[];
  warnings: EligibilityCheck[];
  required_user_actions: string[];
  passed_checks: EligibilityCheck[];
  failed_checks: EligibilityCheck[];
  checks: EligibilityCheck[];
  automation_enabled: boolean;
  require_user_approval: boolean;
  require_user_review: boolean;
  approval: EligibilityApproval;
  evaluation_timestamp: string;
  report_version: string;
}

export interface EligibilitySummary {
  application_id: string;
  job_id: string;
  job_title: string;
  company: string | null;
  application_status: ApplicationStatus;
  platform_name: string;
  resume_filename: string | null;
  resume_match_score: number | null;
  overall_decision: EligibilityDecision;
  readiness_percentage: number;
  blocking_count: number;
  warning_count: number;
  blocking_issue_names: string[];
  next_action: string | null;
  automation_enabled: boolean;
  approved: boolean;
  evaluation_timestamp: string;
  report_version: string;
}

export interface EligibilityOverview {
  total: number;
  ready_to_prepare: number;
  ready_for_user_review: number;
  blocked: number;
  needs_information: number;
  needs_confirmation: number;
  needs_user_approval: number;
  ineligible: number;
  duplicate: number;
  already_applied: number;
  unsupported_platform: number;
  failed: number;
  automation_enabled: boolean;
}

export interface EligibilityListResponse {
  overview: EligibilityOverview;
  applications: EligibilitySummary[];
}

export type FillFieldStatus =
  | "ready"
  | "needs_confirmation"
  | "needs_human_input"
  | "already_filled"
  | "skipped"
  | "succeeded"
  | "failed"
  | "pending_confirmation"
  | "pending_human_input";

export type FillAction = "fill" | "select" | "check" | "upload" | "none";

export interface FillResumeSelection {
  resume_id: string | null;
  filename: string | null;
  will_upload: boolean;
  file_field_ids: string[];
  reason: string;
}

export interface FillPlanField {
  field_id: string;
  label: string;
  field_type: string;
  classification: string;
  required: boolean;
  action: FillAction;
  status: FillFieldStatus;
  proposed_value: string | null;
  source: string;
  confidence: number;
  options: string[];
  reason: string;
}

export interface FillPlanResponse {
  application_id: string;
  analysis_id: string;
  analysis_version: number;
  browser_session_id: string;
  generated_at: string;
  total_fields: number;
  eligible_count: number;
  confirmation_count: number;
  human_input_count: number;
  already_filled_count: number;
  skipped_count: number;
  resume: FillResumeSelection;
  fields: FillPlanField[];
  warnings: string[];
  executable: boolean;
  final_submission_note: string;
}

export interface FillFieldResult {
  field_id: string;
  label: string;
  action: FillAction;
  status: FillFieldStatus;
  verified: boolean;
  reason: string;
  source: string;
}

export type InterventionType =
  | "missing_information"
  | "user_confirmation"
  | "stale_information"
  | "conflicting_information"
  | "sensitive_information"
  | "unsupported_field"
  | "browser_session_expired"
  | "form_changed";

export type InterventionStatus =
  | "pending"
  | "user_working"
  | "resolved"
  | "cancelled"
  | "expired";

export interface Intervention {
  intervention_id: string;
  application_id: string;
  browser_session_id: string | null;
  execution_id: string | null;
  analysis_id: string | null;
  analysis_version: number | null;
  field_id: string | null;
  field_label: string;
  field_category: string;
  required: boolean;
  reason: string;
  intervention_type: InterventionType;
  status: InterventionStatus;
  resolution_reason: string | null;
  created_at: string;
  updated_at: string;
  user_working_at: string | null;
  resolved_at: string | null;
  cancelled_at: string | null;
  expired_at: string | null;
}

export interface BrowserViewInfo {
  available: boolean;
  mode: string;
  user_can_interact: boolean;
  reason: string;
  required_component: string | null;
}

export interface BrowserSession {
  session_id: string;
  application_id: string;
  job_id: string | null;
  job_title: string;
  company: string | null;
  application_url: string;
  run_number: number;
  state: string;
  browser_status: string;
  current_url: string | null;
  allowed_origins: string[];
  pause_reason: string | null;
  failure_code: string | null;
  failure_reason: string | null;
  checkpoint: Record<string, unknown>;
  active: boolean;
  can_pause: boolean;
  can_resume: boolean;
  can_cancel: boolean;
  started_at: string | null;
  last_activity_at: string | null;
  expires_at: string | null;
  stopped_at: string | null;
  view: BrowserViewInfo;
}

export interface BrowserSessionCapabilitiesResponse {
  view: BrowserViewInfo;
  automation_enabled: boolean;
  session_ttl_seconds: number;
  final_submission_note: string;
}

export interface BrowserViewTicketResponse {
  browser_session_id: string;
  ticket: string;
  websocket_path: string;
  expires_at: string;
  mode: string;
  frame_format: string;
  can_interact: boolean;
  note: string;
}

export interface InterventionListResponse {
  application_id: string;
  application_url: string;
  browser_session_id: string | null;
  view: BrowserViewInfo;
  total: number;
  pending_count: number;
  user_working_count: number;
  blocking_count: number;
  interventions: Intervention[];
  final_submission_note: string;
}

export interface FormFieldChange {
  change: "added" | "removed" | "changed";
  field_id: string | null;
  label: string;
  previous_label: string | null;
  field_type: string | null;
  previous_field_type: string | null;
  required: boolean | null;
  previous_required: boolean | null;
  options_changed: boolean;
  now_has_value: boolean;
  previously_had_value: boolean;
}

export interface HumanLoopReconcileResponse {
  application_id: string;
  analysis_id: string;
  analysis_version: number;
  previous_analysis_id: string | null;
  previous_analysis_version: number | null;
  page_url: string | null;
  field_count: number;
  changes: FormFieldChange[];
  newly_completed_fields: string[];
  blocked_count: number;
  interventions: Intervention[];
  execution: FillExecutionResponse | null;
  view: BrowserViewInfo;
  manual_handoff_required: boolean;
  verification_note: string;
  final_submission_note: string;
}

export interface ReviewItem {
  label: string;
  present: boolean;
  optional: boolean;
  detail: string | null;
}

export interface ReviewSection {
  key: string;
  label: string;
  status: string;
  items: ReviewItem[];
}

export interface FinalReviewResponse {
  application_id: string;
  execution_id: string | null;
  execution_status: string;
  analysis_version: number | null;
  ready_for_final_review: boolean;
  generated_at: string;
  resume: FillResumeSelection;
  sections: ReviewSection[];
  filled_field_labels: string[];
  verified_field_labels: string[];
  skipped_field_labels: string[];
  confirmation_fields: string[];
  blocking_interventions: string[];
  missing_optional_fields: string[];
  filled_count: number;
  verified_count: number;
  skipped_count: number;
  blocker_count: number;
  view: BrowserViewInfo;
  manual_submission_required: boolean;
  final_submission_note: string;
}

export interface FillExecutionResponse {
  execution_id: string;
  application_id: string;
  analysis_id: string;
  analysis_version: number;
  browser_session_id: string;
  run_number: number;
  status: string;
  total_fields: number;
  ready_count: number;
  succeeded_count: number;
  failed_count: number;
  skipped_count: number;
  verified_count: number;
  pending_confirmation_count: number;
  pending_human_input_count: number;
  selected_resume_id: string | null;
  selected_resume_filename: string | null;
  results: FillFieldResult[];
  warnings: string[];
  pause_reason: string | null;
  failure_code: string | null;
  failure_reason: string | null;
  can_pause: boolean;
  can_resume: boolean;
  can_cancel: boolean;
  started_at: string | null;
  paused_at: string | null;
  completed_at: string | null;
  last_activity_at: string | null;
  final_submission_note: string;
}

/* ---------------------------------------------------------------------------
 * Phase 2F structured intelligence (backend/app/schemas/*intelligence*.py)
 * ------------------------------------------------------------------------- */

/** One explainable fact behind a match score. Never a storage path or file text. */
export interface MatchEvidence {
  category: string;
  description: string;
  resume_reference: string | null;
  confidence: string;
  match_reason: string;
}

export type ExtractionConfidence = "high" | "medium" | "low";

/** `ready` and `failed` are stored; `stale`/`missing` were computed on read. */
export type ResumeAnalysisState = "ready" | "stale" | "missing" | "failed";

export type ResumeProcessingState =
  | "ready"
  | "partial"
  | "analysis_required"
  | "extraction_failed"
  | "invalid"
  | "inaccessible";

export interface JobIntelligenceFields {
  job_title: string;
  company: string | null;
  description: string | null;
  required_skills: string[];
  preferred_skills: string[];
  experience_years: number | null;
  experience_years_max: number | null;
  education_level: string | null;
  education_field: string | null;
  education_requirement: string | null;
  required_qualifications: string[];
  responsibilities: string[];
  employment_type: string | null;
  work_location: string | null;
  work_mode: string | null;
  salary_range: string | null;
  experience_level: string;
  required_certifications: string[];
  application_url: string | null;
  application_url_is_listing: boolean;
}

export interface JobIntelligenceExtraction {
  confidence: ExtractionConfidence | string;
  provenance: Record<string, string>;
  field_confidence: Record<string, string>;
  contradictions: string[];
  warnings: string[];
  llm_used: boolean;
  llm_status: string;
  llm_model: string | null;
  extractor_version: string;
  dropped_llm_claims: string[];
  supplemented_fields: string[];
  computed_at: string;
  cached: boolean;
}

export interface JobIntelligenceResponse {
  job_id: string;
  fields: JobIntelligenceFields;
  extraction: JobIntelligenceExtraction;
  disclaimer: string;
  message: string | null;
}

export interface ResumeFact {
  source: string;
  confidence: ExtractionConfidence | string;
  evidence: string | null;
  method: string;
}

export interface ResumeSkillFact extends ResumeFact {
  name: string;
  category: string;
  explicit: boolean;
  category_inferred: boolean;
}

export interface ResumeExperienceFact extends ResumeFact {
  company: string | null;
  role: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  duration_months: number | null;
  responsibilities: string[];
  achievements: string[];
  technologies: string[];
}

export interface ResumeProjectFact extends ResumeFact {
  title: string;
  description: string | null;
  technologies: string[];
  contribution: string | null;
  demonstrated_skills: string[];
  measurable_outcomes: string[];
  url: string | null;
}

export interface ResumeEducationFact extends ResumeFact {
  institution: string | null;
  degree: string | null;
  specialization: string | null;
  start_date: string | null;
  end_date: string | null;
  graduation_year: number | null;
  achievements: string[];
}

export interface ResumeCertificationFact extends ResumeFact {
  name: string;
  issuing_organization: string | null;
  issued_on: string | null;
}

export interface ResumeIntelligenceFields {
  summary: string | null;
  total_experience_months: number | null;
  has_dated_experience: boolean;
  skills: ResumeSkillFact[];
  experience: ResumeExperienceFact[];
  projects: ResumeProjectFact[];
  education: ResumeEducationFact[];
  certifications: ResumeCertificationFact[];
  sections_found: string[];
}

export interface ResumeIntelligenceExtraction {
  status: string;
  analysis_state: ResumeAnalysisState;
  confidence: ExtractionConfidence | string;
  provenance: Record<string, string>;
  field_confidence: Record<string, string>;
  warnings: string[];
  error_code: string | null;
  extractor_version: string;
  computed_at: string;
  cached: boolean;
}

export interface ResumeIntelligenceResponse {
  resume_id: string;
  fields: ResumeIntelligenceFields;
  extraction: ResumeIntelligenceExtraction;
  disclaimer: string;
  message: string | null;
}

export interface ResumeComparisonEntry {
  resume_id: string;
  resume_title: string;
  rank: number | null;
  processing_state: ResumeProcessingState | string;
  state_reason: string;
  comparable: boolean;
  is_recommended: boolean;
  overall_score: number | null;
  scores: MatchScoreBreakdown;
  required_skill_coverage: number | null;
  required_skills_matched: number | null;
  required_skills_total: number | null;
  evidence_confidence: ExtractionConfidence | string | null;
  match_processing_state: string | null;
  matched_required_skills: string[];
  missing_required_skills: string[];
  evidence: MatchEvidence[];
  gaps: string[];
  explanation: string;
}

export interface ResumeProcessingFailure {
  resume_id: string;
  resume_title: string;
  processing_state: string;
  reason: string;
}

export interface ResumeAlternative {
  resume_id: string;
  resume_title: string;
  overall_score: number;
  score_difference: number;
  distinguishing_factors: string[];
}

export interface ResumeRecommendation {
  resume_id: string;
  resume_title: string;
  overall_score: number;
  confidence: ExtractionConfidence | string;
  reasons: string[];
  supporting_evidence: MatchEvidence[];
  important_gaps: string[];
  close_call: boolean;
  close_call_threshold: number;
  close_call_explanation: string | null;
  alternatives: ResumeAlternative[];
}

export interface ResumeComparisonResponse {
  job_id: string;
  status: string;
  total_resumes: number;
  compared_resumes: number;
  resumes: ResumeComparisonEntry[];
  recommendation: ResumeRecommendation | null;
  close_score: boolean;
  close_score_threshold: number;
  processing_failures: ResumeProcessingFailure[];
  algorithm_version: string;
  generated_at: string;
  disclaimer: string;
  message: string | null;
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
