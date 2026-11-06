import type { Metadata } from "next";

import { ApplicationList } from "@/components/applications/application-list";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "My Applications",
};

export default function ApplicationsPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="My applications"
        description="Prepare each application from your verified profile, review every answer and approve explicitly before anything is submitted."
        actions={<Badge variant="brand">Phase 2B</Badge>}
      />
      <ApplicationList />
    </div>
  );
}
