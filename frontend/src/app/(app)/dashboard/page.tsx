import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { ApplicationPipelineCard } from "@/components/dashboard/application-pipeline";
import { ApplyJobCard } from "@/components/dashboard/apply-job-card";
import { DashboardStats } from "@/components/dashboard/dashboard-stats";
import { JobRecommendations } from "@/components/dashboard/job-recommendations";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { ResumeOverview } from "@/components/dashboard/resume-overview";
import { WelcomeBanner } from "@/components/dashboard/welcome-banner";
import { GmailStatusCard } from "@/components/gmail/gmail-status-card";
import { getCurrentUser, getSessionToken } from "@/lib/auth/session";
import { firstName } from "@/lib/utils/format";

export const metadata: Metadata = {
  title: "Overview",
};

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const token = await getSessionToken();
  const user = token ? await getCurrentUser(token) : null;
  if (!user) {
    redirect("/login?expired=1");
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <WelcomeBanner name={firstName(user.full_name)} />
      <ApplyJobCard />
      <DashboardStats />
      <ApplicationPipelineCard />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <JobRecommendations />
          <RecentActivity />
        </div>
        <div className="space-y-6">
          <GmailStatusCard />
          <ResumeOverview />
        </div>
      </div>
    </div>
  );
}
