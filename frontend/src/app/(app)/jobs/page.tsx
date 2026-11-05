import type { Metadata } from "next";
import { Info } from "lucide-react";

import { GlassdoorDiscovery } from "@/components/gmail/glassdoor-discovery";
import { Alert } from "@/components/ui/alert";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "Job Discovery",
};

export default function JobsPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Job discovery"
        description="Recommendations are extracted from Glassdoor alert emails in your connected inbox. ApplyPilot never scrapes Glassdoor and never applies to a posting on its own."
      />

      <Alert variant="info" title="Where these results come from">
        Only emails you have already received are parsed. Missing details
        (company, location, received date) stay empty rather than being guessed.
      </Alert>

      <GlassdoorDiscovery />

      <div className="flex items-start gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-[12px] leading-relaxed text-ink-subtle">
        <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        <p>
          Match scores measure overlap between each resume and the information
          in the posting. They are not hiring probabilities. ApplyPilot never
          edits a resume or submits an application for you.
        </p>
      </div>
    </div>
  );
}
