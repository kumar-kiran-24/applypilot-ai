import type { Metadata } from "next";

import { ApplicationWorkspace } from "@/components/applications/application-workspace";

export const metadata: Metadata = {
  title: "Application questionnaire",
};

export default async function ApplicationWorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div className="animate-fade-in">
      <ApplicationWorkspace applicationId={id} />
    </div>
  );
}
