import { apiFetch } from "@/lib/api/client";
import type {
  AutoApplySettings,
  AutoApplySettingsInput,
  EligibilityListResponse,
  EligibilityReport,
} from "@/types/api";

export const autoApplyKeys = {
  all: ["auto-apply"] as const,
  settings: ["auto-apply", "settings"] as const,
  eligibility: ["auto-apply", "eligibility"] as const,
  report: (applicationId: string) =>
    ["auto-apply", "eligibility", applicationId] as const,
};

export function fetchAutoApplySettings(): Promise<AutoApplySettings> {
  return apiFetch<AutoApplySettings>("auto-apply/settings");
}

export function updateAutoApplySettings(
  input: AutoApplySettingsInput,
): Promise<AutoApplySettings> {
  return apiFetch<AutoApplySettings>("auto-apply/settings", {
    method: "PUT",
    body: input,
  });
}

export function fetchEligibilityList(): Promise<EligibilityListResponse> {
  return apiFetch<EligibilityListResponse>("auto-apply/eligibility");
}

export function fetchEligibilityReport(
  applicationId: string,
): Promise<EligibilityReport> {
  return apiFetch<EligibilityReport>(
    `auto-apply/eligibility/${applicationId}`,
  );
}

export function evaluateEligibility(
  applicationId: string,
): Promise<EligibilityReport> {
  return apiFetch<EligibilityReport>(
    `auto-apply/eligibility/${applicationId}/evaluate`,
    { method: "POST" },
  );
}

export function approveEligibility(
  applicationId: string,
  reportVersion: string,
): Promise<EligibilityReport> {
  return apiFetch<EligibilityReport>(
    `auto-apply/eligibility/${applicationId}/approve`,
    {
      method: "POST",
      body: { report_version: reportVersion, approved: true },
    },
  );
}

export function withdrawEligibilityApproval(
  applicationId: string,
  reportVersion: string,
): Promise<EligibilityReport> {
  return apiFetch<EligibilityReport>(
    `auto-apply/eligibility/${applicationId}/approve`,
    {
      method: "POST",
      body: { report_version: reportVersion, approved: false },
    },
  );
}
