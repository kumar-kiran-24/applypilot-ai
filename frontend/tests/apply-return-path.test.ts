import { describe, expect, it } from "vitest";

import { safeReturnPath } from "@/lib/utils/url";

describe("safeReturnPath", () => {
  it("keeps the encoded job URL on the apply route", () => {
    const url = "https://www.glassdoor.com/job-listing/x-JV_1.htm?jl=1";
    const path = `/apply?url=${encodeURIComponent(url)}`;
    expect(safeReturnPath(path)).toBe(path);
  });

  it("accepts the bare apply route", () => {
    expect(safeReturnPath("/apply")).toBe("/apply");
  });

  it("rejects absolute, protocol-relative and unrelated redirects", () => {
    expect(safeReturnPath("https://evil.example/apply?url=x")).toBeNull();
    expect(safeReturnPath("//evil.example/apply")).toBeNull();
    expect(safeReturnPath("/\\evil.example/apply")).toBeNull();
    expect(safeReturnPath("/dashboard")).toBeNull();
    expect(safeReturnPath("/apply/../admin")).toBeNull();
  });

  it("rejects unexpected query parameters and control characters", () => {
    expect(safeReturnPath("/apply?url=x&next=https://evil.example")).toBeNull();
    expect(safeReturnPath("/apply?url=x\nSet-Cookie: y")).toBeNull();
  });
});
