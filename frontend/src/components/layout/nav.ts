import {
  Briefcase,
  FileText,
  LayoutDashboard,
  Mail,
  Search,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Phase 2 modules get an explicit "Soon" marker instead of fake screens. */
  available: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  {
    label: "Overview",
    href: "/dashboard",
    icon: LayoutDashboard,
    available: true,
  },
  { label: "Job Discovery", href: "/jobs", icon: Search, available: true },
  {
    label: "My Applications",
    href: "/applications",
    icon: Briefcase,
    available: false,
  },
  {
    label: "Resume Manager",
    href: "/resumes",
    icon: FileText,
    available: true,
  },
  { label: "Gmail Integration", href: "/gmail", icon: Mail, available: true },
  {
    label: "AI Assistant",
    href: "/assistant",
    icon: Sparkles,
    available: false,
  },
  { label: "Settings", href: "/settings", icon: Settings, available: true },
];
