import type {
  JobIntelligenceExtraction,
  JobIntelligenceFields,
  JobIntelligenceResponse,
  ResumeComparisonResponse,
  ResumeIntelligenceExtraction,
  ResumeIntelligenceFields,
  ResumeIntelligenceResponse,
} from "@/types/api";

/** Synthetic job intelligence; nothing here is read from a real posting. */
export function jobIntelligence(
  overrides: {
    fields?: Partial<JobIntelligenceFields>;
    extraction?: Partial<JobIntelligenceExtraction>;
    message?: string | null;
  } = {},
): JobIntelligenceResponse {
  return {
    job_id: "22222222-2222-4222-8222-222222222222",
    fields: {
      job_title: "Senior Backend Engineer",
      company: "Acme",
      description: "Build Python services.",
      required_skills: ["Python", "FastAPI", "PostgreSQL"],
      preferred_skills: ["Docker"],
      experience_years: 5,
      experience_years_max: null,
      education_level: "bachelor",
      education_field: "Computer Science",
      education_requirement: "Bachelor's degree in Computer Science",
      required_qualifications: ["5+ years building backend services"],
      responsibilities: ["Design and operate Python services"],
      employment_type: "full_time",
      work_location: "Remote (US)",
      work_mode: "remote",
      salary_range: "$120,000 – $150,000",
      experience_level: "senior",
      required_certifications: ["AWS Certified Developer"],
      application_url: null,
      application_url_is_listing: true,
      ...(overrides.fields ?? {}),
    },
    extraction: {
      confidence: "high",
      provenance: {
        required_skills: "job_record",
        salary_range: "deterministic",
      },
      field_confidence: { required_skills: "high", salary_range: "medium" },
      contradictions: [],
      warnings: [],
      llm_used: false,
      llm_status: "disabled",
      llm_model: null,
      extractor_version: "job-intelligence-v1",
      dropped_llm_claims: [],
      supplemented_fields: [],
      computed_at: "2026-11-09T12:00:00Z",
      cached: false,
      ...(overrides.extraction ?? {}),
    },
    disclaimer:
      "This analysis reports only information stated in the posting.",
    message: overrides.message ?? null,
  };
}

/** Synthetic resume intelligence covering every displayed section. */
export function resumeIntelligence(
  overrides: {
    fields?: Partial<ResumeIntelligenceFields>;
    extraction?: Partial<ResumeIntelligenceExtraction>;
    message?: string | null;
  } = {},
): ResumeIntelligenceResponse {
  return {
    resume_id: "11111111-1111-4111-8111-111111111111",
    fields: {
      summary: "Backend engineer building Python services.",
      total_experience_months: 54,
      has_dated_experience: true,
      skills: [
        {
          name: "Python",
          category: "language",
          explicit: true,
          category_inferred: false,
          source: "resume_text",
          confidence: "high",
          evidence: "Python, FastAPI",
          method: "skills_section",
        },
        {
          name: "FastAPI",
          category: "framework",
          explicit: true,
          category_inferred: false,
          source: "resume_text",
          confidence: "high",
          evidence: "Python, FastAPI",
          method: "skills_section",
        },
      ],
      experience: [
        {
          company: "Acme",
          role: "Senior Backend Engineer",
          location: "Remote",
          start_date: "Jan 2019",
          end_date: null,
          is_current: true,
          duration_months: 54,
          responsibilities: ["Built FastAPI services"],
          achievements: [],
          technologies: ["Python"],
          source: "resume_text",
          confidence: "high",
          evidence: "Senior Backend Engineer at Acme",
          method: "parsed_experience_entry",
        },
      ],
      projects: [
        {
          title: "Payments API",
          description: "A billing service.",
          technologies: ["FastAPI"],
          contribution: "Led the migration",
          demonstrated_skills: ["FastAPI"],
          measurable_outcomes: ["Cut latency 40%"],
          url: null,
          source: "resume_text",
          confidence: "medium",
          evidence: "Payments API — FastAPI billing service",
          method: "parsed_project_entry",
        },
      ],
      education: [
        {
          institution: "State University",
          degree: "BSc Computer Science",
          specialization: "Computer Science",
          start_date: "2010",
          end_date: "2014",
          graduation_year: 2014,
          achievements: [],
          source: "resume_text",
          confidence: "high",
          evidence: "BSc Computer Science, State University",
          method: "parsed_education_entry",
        },
      ],
      certifications: [
        {
          name: "AWS Certified Developer",
          issuing_organization: "Amazon",
          issued_on: "2022",
          source: "resume_text",
          confidence: "medium",
          evidence: "AWS Certified Developer (2022)",
          method: "parsed_certification_entry",
        },
      ],
      sections_found: ["skills", "experience", "education"],
      ...(overrides.fields ?? {}),
    },
    extraction: {
      status: "completed",
      analysis_state: "ready",
      confidence: "high",
      provenance: { skills: "resume_text", experience: "resume_text" },
      field_confidence: { skills: "high", experience: "high" },
      warnings: [],
      error_code: null,
      extractor_version: "resume-intelligence-v1",
      computed_at: "2026-11-09T12:00:00Z",
      cached: true,
      ...(overrides.extraction ?? {}),
    },
    disclaimer:
      "This analysis reports only evidence found in the uploaded resume.",
    message:
      overrides.message ??
      "Cached analysis reused. Send force=true to re-run it.",
  };
}

/** Synthetic comparison with a recommendation, one alternative and a failure. */
export function resumeComparison(
  overrides: Partial<ResumeComparisonResponse> = {},
): ResumeComparisonResponse {
  return {
    job_id: "22222222-2222-4222-8222-222222222222",
    status: "completed",
    total_resumes: 3,
    compared_resumes: 2,
    resumes: [
      {
        resume_id: "11111111-1111-4111-8111-111111111111",
        resume_title: "backend-strong.pdf",
        rank: 1,
        processing_state: "ready",
        state_reason: "Resume and job analysis are current.",
        comparable: true,
        is_recommended: true,
        overall_score: 82.5,
        scores: {
          skills: 90,
          experience: 80,
          education: 70,
          title: 85,
          projects: 60,
        },
        required_skill_coverage: 66.7,
        required_skills_matched: 2,
        required_skills_total: 3,
        evidence_confidence: "high",
        match_processing_state: "ready",
        matched_required_skills: ["Python", "FastAPI"],
        missing_required_skills: ["PostgreSQL"],
        evidence: [
          {
            category: "skills",
            description: "Python listed in the skills section",
            resume_reference: "skills section",
            confidence: "high",
            match_reason: "Exact required skill",
          },
          {
            category: "experience",
            description: "5 years of backend work",
            resume_reference: "experience: Senior Backend Engineer",
            confidence: "high",
            match_reason: "Dated role",
          },
        ],
        gaps: ["PostgreSQL is not evidenced"],
        explanation: "Strong overlap with the posting requirements.",
      },
      {
        resume_id: "33333333-3333-4333-8333-333333333333",
        resume_title: "backend-weak.pdf",
        rank: 2,
        processing_state: "analysis_required",
        state_reason:
          "No resume analysis is stored; parsed resume data was used instead.",
        comparable: true,
        is_recommended: false,
        overall_score: 71,
        scores: {
          skills: 60,
          experience: 65,
          education: 70,
          title: 80,
          projects: 40,
        },
        required_skill_coverage: 33.3,
        required_skills_matched: 1,
        required_skills_total: 3,
        evidence_confidence: "medium",
        match_processing_state: "partial",
        matched_required_skills: ["Python"],
        missing_required_skills: ["FastAPI", "PostgreSQL"],
        evidence: [],
        gaps: ["FastAPI is not evidenced"],
        explanation: "Partial overlap; analysis would improve confidence.",
      },
    ],
    recommendation: {
      resume_id: "11111111-1111-4111-8111-111111111111",
      resume_title: "backend-strong.pdf",
      overall_score: 82.5,
      confidence: "high",
      reasons: [
        "Highest overall compatibility score.",
        "Covers 2 of 3 required skills.",
      ],
      supporting_evidence: [
        {
          category: "skills",
          description: "Python listed in the skills section",
          resume_reference: "skills section",
          confidence: "high",
          match_reason: "Exact required skill",
        },
      ],
      important_gaps: ["PostgreSQL is not evidenced"],
      close_call: false,
      close_call_threshold: 3,
      close_call_explanation: null,
      alternatives: [
        {
          resume_id: "33333333-3333-4333-8333-333333333333",
          resume_title: "backend-weak.pdf",
          overall_score: 71,
          score_difference: 11.5,
          distinguishing_factors: ["required-skill coverage", "project evidence"],
        },
      ],
    },
    close_score: false,
    close_score_threshold: 3,
    processing_failures: [
      {
        resume_id: "44444444-4444-4444-8444-444444444444",
        resume_title: "scanned.pdf",
        processing_state: "extraction_failed",
        reason: "Resume analysis failed and no usable content remains.",
      },
    ],
    algorithm_version: "matching-v3",
    generated_at: "2026-11-09T12:00:00Z",
    disclaimer:
      "Compatibility scores measure overlap with the posting, not hiring probability.",
    message: null,
    ...overrides,
  };
}
