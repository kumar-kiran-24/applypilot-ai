import type { Metadata } from "next";

import { JobWorkspace } from "@/components/jobs/job-workspace";

export const metadata: Metadata = {
  title: "Job Workspace",
};

export default async function JobWorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div className="animate-fade-in">
      <JobWorkspace jobId={id} />
    </div>
  );
}
