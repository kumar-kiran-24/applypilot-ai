import type { Metadata } from "next";

import { ProfileEditor } from "@/components/profile/profile-editor";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "Candidate Profile",
};

export default function ProfilePage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Candidate profile"
        description="The single source of truth for every application. Profile values are verified data; resume parsing only ever suggests answers."
        actions={<Badge variant="brand">Phase 2B</Badge>}
      />
      <ProfileEditor />
    </div>
  );
}
