import type { Metadata } from "next";
import { Briefcase } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "My Applications",
};

export default function ApplicationsPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="My applications"
        description="Track every application, its status and the documents you sent."
        actions={<Badge variant="neutral">Phase 2</Badge>}
      />

      <Card>
        <CardContent>
          <EmptyState
            icon={Briefcase}
            title="No applications yet"
            description="The applications module is not part of Phase 1, so there is nothing tracked here. ApplyPilot will never submit an application without your explicit confirmation."
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-3">
        {[
          {
            title: "Status tracking",
            body: "Applied, screening, interview and offer stages with timeline history.",
          },
          {
            title: "Per-application review",
            body: "Every submission is previewed and confirmed by you before it leaves ApplyPilot.",
          },
          {
            title: "Document snapshots",
            body: "Know exactly which resume and answers were used for each application.",
          },
        ].map((item) => (
          <div
            key={item.title}
            className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4"
          >
            <p className="text-[13px] font-medium text-ink">{item.title}</p>
            <p className="mt-1.5 text-[12px] leading-relaxed text-ink-muted">
              {item.body}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
