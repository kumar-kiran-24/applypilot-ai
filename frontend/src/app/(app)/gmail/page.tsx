import type { Metadata } from "next";

import { ConnectionPanel } from "@/components/gmail/connection-panel";
import { GlassdoorDiscovery } from "@/components/gmail/glassdoor-discovery";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "Gmail Integration",
};

export default function GmailPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Gmail integration"
        description="Authorize read-only access so ApplyPilot can find job alerts in the mail you already receive. Login and Gmail authorization are separate steps."
      />
      <div className="grid gap-6">
        <ConnectionPanel />
        <GlassdoorDiscovery />
      </div>
    </div>
  );
}
