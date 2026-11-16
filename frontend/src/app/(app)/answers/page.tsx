import type { Metadata } from "next";

import { AnswerLibrary } from "@/components/answers/answer-library";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = {
  title: "Answers & knowledge",
};

export default function AnswersPage() {
  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="Answers & knowledge"
        description="Search, review and reuse your saved application answers. Every answer shows where it came from and whether it is a confirmed fact."
        actions={<Badge variant="brand">Provenance tracked</Badge>}
      />

      <AnswerLibrary />
    </div>
  );
}
