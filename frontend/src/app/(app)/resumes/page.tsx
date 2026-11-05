import type { Metadata } from "next";

import { ResumeManager } from "@/components/resumes/resume-manager";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "Resume Manager",
};

export default function ResumesPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Resume manager"
        description="Keep a single inventory of resumes and pick the right one for each job. Files are stored privately and only your account can reach them."
      />
      <ResumeManager />
    </div>
  );
}
