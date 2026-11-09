import type { Metadata } from "next";

import { MissingInformationBoard } from "@/components/missing-information/missing-information-board";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "Missing Information",
};

export default function MissingInformationPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Missing information"
        description="Exactly what still needs your answer before each application can proceed. Answers are matched from your verified profile, resume and approved answer bank — never invented."
        actions={<Badge variant="brand">Phase 2C</Badge>}
      />
      <MissingInformationBoard />
    </div>
  );
}
