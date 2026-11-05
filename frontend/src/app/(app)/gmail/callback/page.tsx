import type { Metadata } from "next";

import { GmailCallbackView } from "@/components/gmail/gmail-callback-view";

export const metadata: Metadata = {
  title: "Gmail authorization",
};

export default function GmailCallbackPage() {
  return <GmailCallbackView />;
}
