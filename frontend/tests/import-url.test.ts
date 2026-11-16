import { describe, expect, it } from "vitest";

import { validateJobImportUrl } from "@/features/jobs/import-url";

const VALID =
  "https://www.glassdoor.com/job-listing/senior-ai-engineer-JV_IC1.htm?jl=100";

describe("validateJobImportUrl", () => {
  it("accepts a supported Glassdoor job listing", () => {
    const result = validateJobImportUrl(VALID);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.url).toContain("glassdoor.com/job-listing/");
    }
  });

  it("accepts country domains and partner listing paths", () => {
    expect(validateJobImportUrl("https://www.glassdoor.co.uk/job-listing/x-JV_1.htm").ok).toBe(
      true,
    );
    expect(
      validateJobImportUrl(
        "https://www.glassdoor.com/partner/jobListing.htm?jobListingId=1",
      ).ok,
    ).toBe(true);
  });

  it("rejects a look-alike host", () => {
    const result = validateJobImportUrl(
      "https://glassdoor.com.evil.com/job-listing/x-JV_1.htm",
    );
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/only glassdoor/i);
  });

  it("rejects another job board and non-listing links", () => {
    expect(validateJobImportUrl("https://www.linkedin.com/jobs/view/1").ok).toBe(
      false,
    );
    expect(
      validateJobImportUrl("https://www.glassdoor.com/Overview/Working-at-Acme.htm")
        .ok,
    ).toBe(false);
  });

  it("rejects unsafe schemes, credentials, ports and empty input", () => {
    expect(validateJobImportUrl("").ok).toBe(false);
    expect(
      validateJobImportUrl("javascript:alert(1)//glassdoor.com/job-listing/x").ok,
    ).toBe(false);
    expect(
      validateJobImportUrl(
        "https://user:pass@www.glassdoor.com/job-listing/x-JV_1.htm",
      ).ok,
    ).toBe(false);
    expect(
      validateJobImportUrl(
        "https://www.glassdoor.com:8443/job-listing/x-JV_1.htm",
      ).ok,
    ).toBe(false);
  });

  it("rejects a private IP even when the path looks like a listing", () => {
    expect(
      validateJobImportUrl("http://127.0.0.1/job-listing/x-JV_1.htm").ok,
    ).toBe(false);
  });
});
