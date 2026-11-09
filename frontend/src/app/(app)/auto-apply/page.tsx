import type { Metadata } from "next";

import { AutoApplyDashboard } from "@/components/auto-apply/auto-apply-dashboard";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "Auto-Apply",
};

export default function AutoApplyPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Auto-apply eligibility"
        description="Every application is evaluated deterministically against your profile, resume, answers and settings. Automation is off by default, never submits an application, and always requires your explicit approval."
        actions={<Badge variant="brand">Phase 2D</Badge>}
      />
      <AutoApplyDashboard />
    </div>
  );
}
