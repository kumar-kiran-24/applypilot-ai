import type { Metadata } from "next";

import { AnswerAssistant } from "@/components/assistant/answer-assistant";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "AI Assistant",
};

export default function AssistantPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="AI assistant"
        description="Draft application answers from your verified profile, resumes and the job posting. Evidence first, editable always."
        actions={<Badge variant="brand">Evidence grounded</Badge>}
      />

      <AnswerAssistant />
    </div>
  );
}
