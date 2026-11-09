import {
  Briefcase,
  ClipboardList,
  FileText,
  LayoutDashboard,
  Mail,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
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
    available: true,
  },
  {
    label: "Missing Information",
    href: "/missing-information",
    icon: ClipboardList,
    available: true,
  },
  {
    label: "Auto-Apply",
    href: "/auto-apply",
    icon: ShieldCheck,
    available: true,
  },
  {
    label: "Resume Manager",
    href: "/resumes",
    icon: FileText,
    available: true,
  },
  {
    label: "Candidate Profile",
    href: "/profile",
    icon: UserRound,
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
