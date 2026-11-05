import type { Metadata } from "next";
import { Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "AI Assistant",
};

export default function AssistantPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="AI assistant"
        description="Ask about a posting, compare it to your experience and draft application answers."
        actions={<Badge variant="neutral">Phase 2</Badge>}
      />

      <Card>
        <CardContent>
          <EmptyState
            icon={Sparkles}
            title="Not available yet"
            description="The backend can already analyze a job description, but it is not exposed through an API endpoint yet. This interface will appear once that endpoint ships."
          />
        </CardContent>
      </Card>
    </div>
  );
}
