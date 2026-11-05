import { apiFetch } from "@/lib/api/client";
import type { Resume } from "@/types/api";

export const resumeKeys = {
  all: ["resumes"] as const,
  detail: (id: string) => ["resumes", id] as const,
};

export function fetchResumes(): Promise<Resume[]> {
  return apiFetch<Resume[]>("resumes");
}

export function uploadResume(file: File): Promise<Resume> {
  const form = new FormData();
  form.append("file", file, file.name);
  return apiFetch<Resume>("resumes/upload", {
    method: "POST",
    formData: form,
    timeoutMs: 60_000,
  });
}

export function setDefaultResume(resumeId: string): Promise<Resume> {
  return apiFetch<Resume>(`resumes/${resumeId}/default`, {
    method: "PATCH",
  });
}

export function deleteResume(resumeId: string): Promise<void> {
  return apiFetch<{ id: string; deleted: boolean }>(`resumes/${resumeId}`, {
    method: "DELETE",
  }).then(() => undefined);
}

/**
 * Download/preview goes through the same-origin BFF proxy so the HttpOnly
 * session cookie is attached by the server; no token ever reaches the browser.
 */
export function resumeFileUrl(
  resumeId: string,
  disposition: "attachment" | "inline",
): string {
  return `/api/backend/resumes/${resumeId}/download?disposition=${disposition}`;
}
