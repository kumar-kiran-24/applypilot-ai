"use client";

import {
  AlertTriangle,
  Award,
  BadgeCheck,
  Briefcase,
  Database,
  FolderKanban,
  GraduationCap,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  UserRound,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useProfile } from "@/features/profile/hooks";
import {
  useAddCertification,
  useAddEducation,
  useAddExperience,
  useAddProject,
  useDeleteCertification,
  useDeleteEducation,
  useDeleteExperience,
  useDeleteProject,
  useDeleteReusableAnswer,
  useProfileCompleteness,
  useReusableAnswers,
  useUpdateEducation,
  useUpdateExperience,
  useUpdatePreferences,
  useUpdateProfile,
  useUpdateProject,
} from "@/features/profile/hooks";
import { describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils/cn";
import type {
  CandidateProfile,
  CertificationEntry,
  EducationEntry,
  EmploymentType,
  ExperienceEntry,
  ProjectEntry,
  RelocationPreference,
  WorkMode,
} from "@/types/api";

const inputClass =
  "w-full rounded-xl border border-white/10 bg-navy-950/60 px-3 py-2 text-[13px] text-ink placeholder:text-ink-subtle transition-colors focus:border-brand-blue/60 focus:outline-none focus:ring-2 focus:ring-brand-blue/25 disabled:cursor-not-allowed disabled:opacity-60";

const EMPLOYMENT_TYPES: Array<{ value: EmploymentType; label: string }> = [
  { value: "full_time", label: "Full-time" },
  { value: "part_time", label: "Part-time" },
  { value: "contract", label: "Contract" },
  { value: "internship", label: "Internship" },
  { value: "freelance", label: "Freelance" },
  { value: "temporary", label: "Temporary" },
];

const WORK_MODES: Array<{ value: WorkMode; label: string }> = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];

interface ProfileFormState {
  fullName: string;
  preferredName: string;
  email: string;
  phoneCode: string;
  phone: string;
  addressLine1: string;
  addressLine2: string;
  city: string;
  region: string;
  postalCode: string;
  country: string;
  linkedin: string;
  github: string;
  portfolio: string;
  summary: string;
  skills: string;
  workAuthorization: string;
  sponsorship: "" | "yes" | "no";
  noticePeriod: string;
  availableFrom: string;
}

interface PreferencesFormState {
  expectedSalary: string;
  currency: string;
  workModes: WorkMode[];
  relocation: RelocationPreference;
}

function text(value: string | null | undefined): string {
  return value ?? "";
}

function nullable(value: string): string | null {
  const cleaned = value.trim();
  return cleaned ? cleaned : null;
}

function emptyProfileForm(): ProfileFormState {
  return {
    fullName: "",
    preferredName: "",
    email: "",
    phoneCode: "",
    phone: "",
    addressLine1: "",
    addressLine2: "",
    city: "",
    region: "",
    postalCode: "",
    country: "",
    linkedin: "",
    github: "",
    portfolio: "",
    summary: "",
    skills: "",
    workAuthorization: "",
    sponsorship: "",
    noticePeriod: "",
    availableFrom: "",
  };
}

function toProfileForm(profile: CandidateProfile): ProfileFormState {
  return {
    fullName: profile.full_name,
    preferredName: text(profile.preferred_name),
    email: profile.email,
    phoneCode: text(profile.phone_country_code),
    phone: text(profile.phone),
    addressLine1: text(profile.address_line1),
    addressLine2: text(profile.address_line2),
    city: text(profile.city),
    region: text(profile.region),
    postalCode: text(profile.postal_code),
    country: text(profile.country),
    linkedin: text(profile.linkedin_url),
    github: text(profile.github_url),
    portfolio: text(profile.portfolio_url),
    summary: text(profile.professional_summary),
    skills: (profile.skills ?? []).join(", "),
    workAuthorization: text(profile.work_authorization),
    sponsorship:
      profile.requires_sponsorship === true
        ? "yes"
        : profile.requires_sponsorship === false
          ? "no"
          : "",
    noticePeriod:
      profile.notice_period_days === null ? "" : String(profile.notice_period_days),
    availableFrom: text(profile.available_from),
  };
}

function toPreferencesForm(profile: CandidateProfile): PreferencesFormState {
  const preference = profile.preference;
  return {
    expectedSalary:
      preference?.expected_salary === null || preference?.expected_salary === undefined
        ? ""
        : String(preference.expected_salary),
    currency: preference?.currency ?? "USD",
    workModes: preference?.preferred_work_modes ?? [],
    relocation: preference?.relocation_preference ?? "unknown",
  };
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.08em] text-ink-subtle">
        {label}
      </span>
      {children}
      {hint ? <span className="mt-1 block text-[11px] text-ink-subtle">{hint}</span> : null}
    </label>
  );
}

function SectionHeader({
  icon,
  title,
  description,
  badge,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  badge?: React.ReactNode;
}) {
  return (
    <CardHeader>
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-sky-300">
          {icon}
        </span>
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
      </div>
      {badge}
    </CardHeader>
  );
}

function RecordActions({
  onEdit,
  onDelete,
  deleting,
  label,
}: {
  onEdit?: () => void;
  onDelete: () => void;
  deleting: boolean;
  label: string;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      {onEdit ? (
        <Button size="sm" variant="ghost" onClick={onEdit} aria-label={`Edit ${label}`}>
          <Pencil aria-hidden="true" className="h-3.5 w-3.5" />
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        onClick={onDelete}
        disabled={deleting}
        aria-label={`Delete ${label}`}
        className="text-red-300 hover:text-red-200"
      >
        {deleting ? (
          <Spinner className="h-3.5 w-3.5" />
        ) : (
          <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
        )}
      </Button>
    </div>
  );
}

function ProfileBasics({
  form,
  setForm,
}: {
  form: ProfileFormState;
  setForm: React.Dispatch<React.SetStateAction<ProfileFormState>>;
}) {
  const update = (field: keyof ProfileFormState, value: string) =>
    setForm((current) => ({ ...current, [field]: value }));

  return (
    <Card id="personal">
      <SectionHeader
        icon={<UserRound aria-hidden="true" className="h-4 w-4" />}
        title="Personal and contact information"
        description="Verified values used to prefill employer forms. Nothing here is guessed from a resume."
      />
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Full legal name">
            <input
              className={inputClass}
              value={form.fullName}
              onChange={(event) => update("fullName", event.target.value)}
              autoComplete="name"
            />
          </Field>
          <Field label="Preferred name" hint="Optional; used when a form asks for it.">
            <input
              className={inputClass}
              value={form.preferredName}
              onChange={(event) => update("preferredName", event.target.value)}
            />
          </Field>
          <Field label="Email address">
            <input
              type="email"
              className={inputClass}
              value={form.email}
              onChange={(event) => update("email", event.target.value)}
              autoComplete="email"
            />
          </Field>
          <Field label="Phone number" hint="Include the number without the country code.">
            <div className="flex gap-2">
              <input
                className={cn(inputClass, "w-20 shrink-0")}
                value={form.phoneCode}
                onChange={(event) => update("phoneCode", event.target.value)}
                placeholder="+1"
                aria-label="Phone country code"
              />
              <input
                type="tel"
                className={inputClass}
                value={form.phone}
                onChange={(event) => update("phone", event.target.value)}
                autoComplete="tel"
              />
            </div>
          </Field>
          <Field label="LinkedIn URL">
            <input
              type="url"
              className={inputClass}
              value={form.linkedin}
              onChange={(event) => update("linkedin", event.target.value)}
              placeholder="https://www.linkedin.com/in/…"
            />
          </Field>
          <Field label="GitHub URL">
            <input
              type="url"
              className={inputClass}
              value={form.github}
              onChange={(event) => update("github", event.target.value)}
              placeholder="https://github.com/…"
            />
          </Field>
          <Field label="Portfolio URL">
            <input
              type="url"
              className={inputClass}
              value={form.portfolio}
              onChange={(event) => update("portfolio", event.target.value)}
              placeholder="https://…"
            />
          </Field>
          <Field label="Professional summary">
            <textarea
              className={inputClass}
              rows={3}
              value={form.summary}
              onChange={(event) => update("summary", event.target.value)}
            />
          </Field>
        </div>
      </CardContent>
    </Card>
  );
}

function ProfileAddress({
  form,
  setForm,
}: {
  form: ProfileFormState;
  setForm: React.Dispatch<React.SetStateAction<ProfileFormState>>;
}) {
  const update = (field: keyof ProfileFormState, value: string) =>
    setForm((current) => ({ ...current, [field]: value }));

  return (
    <Card id="address">
      <SectionHeader
        icon={<Database aria-hidden="true" className="h-4 w-4" />}
        title="Address"
        description="Used for address fields and location-based eligibility questions."
      />
      <CardContent className="grid gap-4 sm:grid-cols-2">
        <Field label="Address line 1">
          <input
            className={inputClass}
            value={form.addressLine1}
            onChange={(event) => update("addressLine1", event.target.value)}
            autoComplete="address-line1"
          />
        </Field>
        <Field label="Address line 2">
          <input
            className={inputClass}
            value={form.addressLine2}
            onChange={(event) => update("addressLine2", event.target.value)}
            autoComplete="address-line2"
          />
        </Field>
        <Field label="City">
          <input
            className={inputClass}
            value={form.city}
            onChange={(event) => update("city", event.target.value)}
            autoComplete="address-level2"
          />
        </Field>
        <Field label="State / province / region">
          <input
            className={inputClass}
            value={form.region}
            onChange={(event) => update("region", event.target.value)}
            autoComplete="address-level1"
          />
        </Field>
        <Field label="Postal / ZIP code">
          <input
            className={inputClass}
            value={form.postalCode}
            onChange={(event) => update("postalCode", event.target.value)}
            autoComplete="postal-code"
          />
        </Field>
        <Field label="Country">
          <input
            className={inputClass}
            value={form.country}
            onChange={(event) => update("country", event.target.value)}
            autoComplete="country-name"
          />
        </Field>
      </CardContent>
    </Card>
  );
}

function ProfileAuthorization({
  form,
  setForm,
}: {
  form: ProfileFormState;
  setForm: React.Dispatch<React.SetStateAction<ProfileFormState>>;
}) {
  const update = (field: keyof ProfileFormState, value: string) =>
    setForm((current) => ({ ...current, [field]: value }));

  return (
    <Card id="authorization">
      <SectionHeader
        icon={<ShieldCheck aria-hidden="true" className="h-4 w-4" />}
        title="Work authorization and availability"
        description="Legally sensitive answers. They are prefilled but always require your confirmation per application."
        badge={
          <Badge variant="warning">
            <ShieldCheck aria-hidden="true" className="h-3 w-3" />
            Sensitive
          </Badge>
        }
      />
      <CardContent className="grid gap-4 sm:grid-cols-2">
        <Field label="Work authorization">
          <input
            className={inputClass}
            value={form.workAuthorization}
            onChange={(event) => update("workAuthorization", event.target.value)}
            placeholder="e.g. US citizen, EU work permit"
          />
        </Field>
        <Field label="Will you require visa sponsorship?">
          <select
            className={inputClass}
            value={form.sponsorship}
            onChange={(event) => update("sponsorship", event.target.value)}
          >
            <option value="">Not specified</option>
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </select>
        </Field>
        <Field label="Notice period (days)">
          <input
            type="number"
            min={0}
            max={365}
            className={inputClass}
            value={form.noticePeriod}
            onChange={(event) => update("noticePeriod", event.target.value)}
          />
        </Field>
        <Field label="Available from">
          <input
            type="date"
            className={inputClass}
            value={form.availableFrom}
            onChange={(event) => update("availableFrom", event.target.value)}
          />
        </Field>
      </CardContent>
    </Card>
  );
}

function ProfilePreferences() {
  const profile = useProfile();
  const mutation = useUpdatePreferences();
  const [form, setForm] = useState<PreferencesFormState>({
    expectedSalary: "",
    currency: "USD",
    workModes: [],
    relocation: "unknown",
  });

  useEffect(() => {
    if (profile.data) {
      setForm(toPreferencesForm(profile.data));
    }
  }, [profile.data]);

  const toggleWorkMode = (mode: WorkMode) =>
    setForm((current) => ({
      ...current,
      workModes: current.workModes.includes(mode)
        ? current.workModes.filter((item) => item !== mode)
        : [...current.workModes, mode],
    }));

  const save = () =>
    mutation.mutate({
      expected_salary: nullable(form.expectedSalary),
      currency: form.currency.trim().toUpperCase() || "USD",
      preferred_work_modes: form.workModes,
      relocation_preference: form.relocation,
    });

  return (
    <Card id="preferences">
      <SectionHeader
        icon={<Briefcase aria-hidden="true" className="h-4 w-4" />}
        title="Salary and work preferences"
        description="Used for compensation and work-mode questions. Salary is sensitive and confirmed per application."
        badge={
          mutation.isSuccess ? <Badge variant="success">Saved</Badge> : undefined
        }
      />
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Expected salary">
            <input
              inputMode="decimal"
              className={inputClass}
              value={form.expectedSalary}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  expectedSalary: event.target.value,
                }))
              }
              placeholder="85000"
            />
          </Field>
          <Field label="Currency">
            <input
              className={inputClass}
              maxLength={3}
              value={form.currency}
              onChange={(event) =>
                setForm((current) => ({ ...current, currency: event.target.value }))
              }
            />
          </Field>
          <Field label="Relocation">
            <select
              className={inputClass}
              value={form.relocation}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  relocation: event.target.value as RelocationPreference,
                }))
              }
            >
              <option value="unknown">Not specified</option>
              <option value="yes">Willing to relocate</option>
              <option value="no">Not willing to relocate</option>
              <option value="negotiable">Negotiable</option>
            </select>
          </Field>
        </div>

        <div>
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-subtle">
            Preferred work modes
          </p>
          <div className="flex flex-wrap gap-2">
            {WORK_MODES.map((mode) => {
              const selected = form.workModes.includes(mode.value);
              return (
                <button
                  key={mode.value}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => toggleWorkMode(mode.value)}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-[12px] transition-colors",
                    selected
                      ? "border-brand-blue/40 bg-brand-blue/15 text-sky-200"
                      : "border-white/10 bg-white/[0.03] text-ink-muted hover:border-white/20",
                  )}
                >
                  {mode.label}
                </button>
              );
            })}
          </div>
        </div>

        {mutation.error ? (
          <Alert variant="danger" title="Preferences were not saved">
            {describeError(mutation.error)}
          </Alert>
        ) : null}

        <Button size="sm" onClick={save} disabled={mutation.isPending}>
          {mutation.isPending ? (
            <>
              <Spinner className="h-4 w-4" />
              Saving…
            </>
          ) : (
            <>
              <Save aria-hidden="true" className="h-4 w-4" />
              Save preferences
            </>
          )}
        </Button>
      </CardContent>
    </Card>
  );
}

function ProfileSkills({
  form,
  setForm,
}: {
  form: ProfileFormState;
  setForm: React.Dispatch<React.SetStateAction<ProfileFormState>>;
}) {
  const skills = form.skills
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

  return (
    <Card id="skills">
      <SectionHeader
        icon={<BadgeCheck aria-hidden="true" className="h-4 w-4" />}
        title="Technical skills"
        description="Comma-separated. These verified skills take priority over resume-extracted skills."
        badge={<Badge variant="neutral">{skills.length} skills</Badge>}
      />
      <CardContent className="space-y-3">
        <Field label="Skills">
          <input
            className={inputClass}
            value={form.skills}
            onChange={(event) =>
              setForm((current) => ({ ...current, skills: event.target.value }))
            }
            placeholder="Python, FastAPI, PostgreSQL, Docker"
          />
        </Field>
        {skills.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {skills.map((skill) => (
              <Badge key={skill} variant="brand">
                {skill}
              </Badge>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function EducationSection({ entries }: { entries: EducationEntry[] }) {
  const add = useAddEducation();
  const update = useUpdateEducation();
  const remove = useDeleteEducation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({
    institution: "",
    degree: "",
    specialization: "",
    startDate: "",
    endDate: "",
  });

  const reset = () => {
    setEditingId(null);
    setForm({ institution: "", degree: "", specialization: "", startDate: "", endDate: "" });
  };

  const startEdit = (entry: EducationEntry) => {
    setEditingId(entry.id);
    setForm({
      institution: entry.institution,
      degree: entry.degree,
      specialization: text(entry.specialization),
      startDate: text(entry.start_date),
      endDate: text(entry.end_date),
    });
  };

  const save = () => {
    const payload = {
      institution: form.institution.trim(),
      degree: form.degree.trim(),
      specialization: nullable(form.specialization),
      start_date: nullable(form.startDate),
      end_date: nullable(form.endDate),
    };
    const onSuccess = () => reset();
    if (editingId) {
      update.mutate({ id: editingId, input: payload }, { onSuccess });
    } else {
      add.mutate(payload, { onSuccess });
    }
  };

  const pending = add.isPending || update.isPending;
  const error = add.error ?? update.error ?? remove.error;

  return (
    <Card id="education">
      <SectionHeader
        icon={<GraduationCap aria-hidden="true" className="h-4 w-4" />}
        title="Education history"
        description="Multiple degrees are supported and rendered in reverse chronological order."
        badge={<Badge variant="neutral">{entries.length} records</Badge>}
      />
      <CardContent className="space-y-4">
        {entries.length === 0 ? (
          <p className="text-[12px] text-ink-muted">No education records yet.</p>
        ) : (
          <ul className="space-y-2">
            {entries.map((entry) => (
              <li
                key={entry.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
              >
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">{entry.degree}</p>
                  <p className="mt-0.5 text-[12px] text-ink-muted">
                    {entry.institution}
                    {entry.specialization ? ` · ${entry.specialization}` : ""}
                  </p>
                  {entry.start_date || entry.end_date ? (
                    <p className="mt-1 text-[11px] text-ink-subtle">
                      {text(entry.start_date) || "—"} to {text(entry.end_date) || "present"}
                    </p>
                  ) : null}
                </div>
                <RecordActions
                  onEdit={() => startEdit(entry)}
                  onDelete={() => remove.mutate(entry.id)}
                  deleting={remove.isPending && remove.variables === entry.id}
                  label={`education at ${entry.institution}`}
                />
              </li>
            ))}
          </ul>
        )}

        <div className="grid gap-3 rounded-xl border border-dashed border-white/12 bg-white/[0.015] p-3 sm:grid-cols-2">
          <Field label={editingId ? "Institution" : "Add institution"}>
            <input
              className={inputClass}
              value={form.institution}
              onChange={(event) =>
                setForm((current) => ({ ...current, institution: event.target.value }))
              }
              placeholder="University name"
            />
          </Field>
          <Field label="Degree">
            <input
              className={inputClass}
              value={form.degree}
              onChange={(event) =>
                setForm((current) => ({ ...current, degree: event.target.value }))
              }
              placeholder="BSc Computer Science"
            />
          </Field>
          <Field label="Specialization">
            <input
              className={inputClass}
              value={form.specialization}
              onChange={(event) =>
                setForm((current) => ({ ...current, specialization: event.target.value }))
              }
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Start date">
              <input
                type="date"
                className={inputClass}
                value={form.startDate}
                onChange={(event) =>
                  setForm((current) => ({ ...current, startDate: event.target.value }))
                }
              />
            </Field>
            <Field label="End date">
              <input
                type="date"
                className={inputClass}
                value={form.endDate}
                onChange={(event) =>
                  setForm((current) => ({ ...current, endDate: event.target.value }))
                }
              />
            </Field>
          </div>
          <div className="flex gap-2 sm:col-span-2">
            <Button
              size="sm"
              onClick={save}
              disabled={pending || !form.institution.trim() || !form.degree.trim()}
            >
              {pending ? (
                <Spinner className="h-4 w-4" />
              ) : editingId ? (
                <Pencil aria-hidden="true" className="h-4 w-4" />
              ) : (
                <Plus aria-hidden="true" className="h-4 w-4" />
              )}
              {editingId ? "Update education" : "Add education"}
            </Button>
            {editingId ? (
              <Button size="sm" variant="ghost" onClick={reset}>
                Cancel
              </Button>
            ) : null}
          </div>
        </div>

        {error ? (
          <Alert variant="danger" title="Education was not saved">
            {describeError(error)}
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ExperienceSection({ entries }: { entries: ExperienceEntry[] }) {
  const add = useAddExperience();
  const update = useUpdateExperience();
  const remove = useDeleteExperience();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({
    company: "",
    designation: "",
    employmentType: "full_time" as EmploymentType,
    location: "",
    startDate: "",
    endDate: "",
    isCurrent: false,
    technologies: "",
    description: "",
  });

  const reset = () => {
    setEditingId(null);
    setForm({
      company: "",
      designation: "",
      employmentType: "full_time",
      location: "",
      startDate: "",
      endDate: "",
      isCurrent: false,
      technologies: "",
      description: "",
    });
  };

  const startEdit = (entry: ExperienceEntry) => {
    setEditingId(entry.id);
    setForm({
      company: entry.company,
      designation: entry.designation,
      employmentType: entry.employment_type ?? "full_time",
      location: text(entry.location),
      startDate: text(entry.start_date),
      endDate: text(entry.end_date),
      isCurrent: entry.is_current,
      technologies: (entry.technologies ?? []).join(", "),
      description: text(entry.description),
    });
  };

  const save = () => {
    const payload = {
      company: form.company.trim(),
      designation: form.designation.trim(),
      employment_type: form.employmentType,
      location: nullable(form.location),
      start_date: nullable(form.startDate),
      end_date: form.isCurrent ? null : nullable(form.endDate),
      is_current: form.isCurrent,
      technologies: form.technologies
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      description: nullable(form.description),
    };
    const onSuccess = () => reset();
    if (editingId) {
      update.mutate({ id: editingId, input: payload }, { onSuccess });
    } else {
      add.mutate(payload, { onSuccess });
    }
  };

  const pending = add.isPending || update.isPending;
  const error = add.error ?? update.error ?? remove.error;

  return (
    <Card id="experience">
      <SectionHeader
        icon={<Briefcase aria-hidden="true" className="h-4 w-4" />}
        title="Employment history"
        description="Includes internships, contract, freelance and temporary roles."
        badge={<Badge variant="neutral">{entries.length} records</Badge>}
      />
      <CardContent className="space-y-4">
        {entries.length === 0 ? (
          <p className="text-[12px] text-ink-muted">No employment records yet.</p>
        ) : (
          <ul className="space-y-2">
            {entries.map((entry) => (
              <li
                key={entry.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
              >
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">{entry.designation}</p>
                  <p className="mt-0.5 text-[12px] text-ink-muted">
                    {entry.company}
                    {entry.location ? ` · ${entry.location}` : ""}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge variant="neutral">
                      {EMPLOYMENT_TYPES.find(
                        (item) => item.value === entry.employment_type,
                      )?.label ?? "Role"}
                    </Badge>
                    {entry.is_current ? <Badge variant="success">Current</Badge> : null}
                    <span className="text-[11px] text-ink-subtle">
                      {text(entry.start_date) || "—"} to{" "}
                      {entry.is_current ? "present" : text(entry.end_date) || "—"}
                    </span>
                  </div>
                </div>
                <RecordActions
                  onEdit={() => startEdit(entry)}
                  onDelete={() => remove.mutate(entry.id)}
                  deleting={remove.isPending && remove.variables === entry.id}
                  label={`role at ${entry.company}`}
                />
              </li>
            ))}
          </ul>
        )}

        <div className="grid gap-3 rounded-xl border border-dashed border-white/12 bg-white/[0.015] p-3 sm:grid-cols-2">
          <Field label="Company">
            <input
              className={inputClass}
              value={form.company}
              onChange={(event) =>
                setForm((current) => ({ ...current, company: event.target.value }))
              }
            />
          </Field>
          <Field label="Job title">
            <input
              className={inputClass}
              value={form.designation}
              onChange={(event) =>
                setForm((current) => ({ ...current, designation: event.target.value }))
              }
            />
          </Field>
          <Field label="Employment type">
            <select
              className={inputClass}
              value={form.employmentType}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  employmentType: event.target.value as EmploymentType,
                }))
              }
            >
              {EMPLOYMENT_TYPES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Location">
            <input
              className={inputClass}
              value={form.location}
              onChange={(event) =>
                setForm((current) => ({ ...current, location: event.target.value }))
              }
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Start date">
              <input
                type="date"
                className={inputClass}
                value={form.startDate}
                onChange={(event) =>
                  setForm((current) => ({ ...current, startDate: event.target.value }))
                }
              />
            </Field>
            <Field label="End date">
              <input
                type="date"
                className={inputClass}
                value={form.endDate}
                disabled={form.isCurrent}
                onChange={(event) =>
                  setForm((current) => ({ ...current, endDate: event.target.value }))
                }
              />
            </Field>
          </div>
          <label className="flex items-center gap-2 self-end pb-2 text-[12px] text-ink-muted">
            <input
              type="checkbox"
              checked={form.isCurrent}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  isCurrent: event.target.checked,
                  endDate: event.target.checked ? "" : current.endDate,
                }))
              }
              className="h-4 w-4 rounded border-white/20 bg-white/[0.04]"
            />
            I currently work here
          </label>
          <Field label="Technologies" hint="Comma-separated.">
            <input
              className={inputClass}
              value={form.technologies}
              onChange={(event) =>
                setForm((current) => ({ ...current, technologies: event.target.value }))
              }
            />
          </Field>
          <Field label="Description">
            <textarea
              className={inputClass}
              rows={2}
              value={form.description}
              onChange={(event) =>
                setForm((current) => ({ ...current, description: event.target.value }))
              }
            />
          </Field>
          <div className="flex gap-2 sm:col-span-2">
            <Button
              size="sm"
              onClick={save}
              disabled={pending || !form.company.trim() || !form.designation.trim()}
            >
              {pending ? (
                <Spinner className="h-4 w-4" />
              ) : editingId ? (
                <Pencil aria-hidden="true" className="h-4 w-4" />
              ) : (
                <Plus aria-hidden="true" className="h-4 w-4" />
              )}
              {editingId ? "Update employment" : "Add employment"}
            </Button>
            {editingId ? (
              <Button size="sm" variant="ghost" onClick={reset}>
                Cancel
              </Button>
            ) : null}
          </div>
        </div>

        {error ? (
          <Alert variant="danger" title="Employment was not saved">
            {describeError(error)}
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ProjectSection({ entries }: { entries: ProjectEntry[] }) {
  const add = useAddProject();
  const update = useUpdateProject();
  const remove = useDeleteProject();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    description: "",
    technologies: "",
    projectUrl: "",
  });

  const reset = () => {
    setEditingId(null);
    setForm({ name: "", description: "", technologies: "", projectUrl: "" });
  };

  const startEdit = (entry: ProjectEntry) => {
    setEditingId(entry.id);
    setForm({
      name: entry.name,
      description: text(entry.description),
      technologies: (entry.technologies ?? []).join(", "),
      projectUrl: text(entry.project_url),
    });
  };

  const save = () => {
    const payload = {
      name: form.name.trim(),
      description: nullable(form.description),
      technologies: form.technologies
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      project_url: nullable(form.projectUrl),
    };
    const onSuccess = () => reset();
    if (editingId) {
      update.mutate({ id: editingId, input: payload }, { onSuccess });
    } else {
      add.mutate(payload, { onSuccess });
    }
  };

  const pending = add.isPending || update.isPending;
  const error = add.error ?? update.error ?? remove.error;

  return (
    <Card id="projects">
      <SectionHeader
        icon={<FolderKanban aria-hidden="true" className="h-4 w-4" />}
        title="Projects"
        description="Portfolio projects used for application and resume matching."
        badge={<Badge variant="neutral">{entries.length} projects</Badge>}
      />
      <CardContent className="space-y-4">
        {entries.length === 0 ? (
          <p className="text-[12px] text-ink-muted">No projects yet.</p>
        ) : (
          <ul className="space-y-2">
            {entries.map((entry) => (
              <li
                key={entry.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
              >
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">{entry.name}</p>
                  {entry.description ? (
                    <p className="mt-0.5 line-clamp-2 text-[12px] text-ink-muted">
                      {entry.description}
                    </p>
                  ) : null}
                  {(entry.technologies ?? []).length > 0 ? (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {entry.technologies.map((technology) => (
                        <Badge key={technology} variant="neutral">
                          {technology}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </div>
                <RecordActions
                  onEdit={() => startEdit(entry)}
                  onDelete={() => remove.mutate(entry.id)}
                  deleting={remove.isPending && remove.variables === entry.id}
                  label={entry.name}
                />
              </li>
            ))}
          </ul>
        )}

        <div className="grid gap-3 rounded-xl border border-dashed border-white/12 bg-white/[0.015] p-3 sm:grid-cols-2">
          <Field label="Project name">
            <input
              className={inputClass}
              value={form.name}
              onChange={(event) =>
                setForm((current) => ({ ...current, name: event.target.value }))
              }
            />
          </Field>
          <Field label="Project URL">
            <input
              type="url"
              className={inputClass}
              value={form.projectUrl}
              onChange={(event) =>
                setForm((current) => ({ ...current, projectUrl: event.target.value }))
              }
            />
          </Field>
          <Field label="Technologies" hint="Comma-separated.">
            <input
              className={inputClass}
              value={form.technologies}
              onChange={(event) =>
                setForm((current) => ({ ...current, technologies: event.target.value }))
              }
            />
          </Field>
          <Field label="Description">
            <textarea
              className={inputClass}
              rows={2}
              value={form.description}
              onChange={(event) =>
                setForm((current) => ({ ...current, description: event.target.value }))
              }
            />
          </Field>
          <div className="flex gap-2 sm:col-span-2">
            <Button size="sm" onClick={save} disabled={pending || !form.name.trim()}>
              {pending ? (
                <Spinner className="h-4 w-4" />
              ) : editingId ? (
                <Pencil aria-hidden="true" className="h-4 w-4" />
              ) : (
                <Plus aria-hidden="true" className="h-4 w-4" />
              )}
              {editingId ? "Update project" : "Add project"}
            </Button>
            {editingId ? (
              <Button size="sm" variant="ghost" onClick={reset}>
                Cancel
              </Button>
            ) : null}
          </div>
        </div>

        {error ? (
          <Alert variant="danger" title="Project was not saved">
            {describeError(error)}
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function CertificationSection({ entries }: { entries: CertificationEntry[] }) {
  const add = useAddCertification();
  const remove = useDeleteCertification();
  const [form, setForm] = useState({ name: "", issuer: "", credentialUrl: "" });

  const save = () => {
    add.mutate(
      {
        name: form.name.trim(),
        issuer: nullable(form.issuer),
        credential_url: nullable(form.credentialUrl),
      },
      { onSuccess: () => setForm({ name: "", issuer: "", credentialUrl: "" }) },
    );
  };

  return (
    <Card id="certifications">
      <SectionHeader
        icon={<Award aria-hidden="true" className="h-4 w-4" />}
        title="Certifications"
        description="Credentials shown to employers and reused in application summaries."
        badge={<Badge variant="neutral">{entries.length} credentials</Badge>}
      />
      <CardContent className="space-y-4">
        {entries.length === 0 ? (
          <p className="text-[12px] text-ink-muted">No certifications yet.</p>
        ) : (
          <ul className="space-y-2">
            {entries.map((entry) => (
              <li
                key={entry.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
              >
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">{entry.name}</p>
                  <p className="mt-0.5 text-[12px] text-ink-muted">
                    {entry.issuer ?? "Issuer not specified"}
                  </p>
                </div>
                <RecordActions
                  onDelete={() => remove.mutate(entry.id)}
                  deleting={remove.isPending && remove.variables === entry.id}
                  label={entry.name}
                />
              </li>
            ))}
          </ul>
        )}

        <div className="grid gap-3 rounded-xl border border-dashed border-white/12 bg-white/[0.015] p-3 sm:grid-cols-3">
          <Field label="Certification name">
            <input
              className={inputClass}
              value={form.name}
              onChange={(event) =>
                setForm((current) => ({ ...current, name: event.target.value }))
              }
            />
          </Field>
          <Field label="Issuer">
            <input
              className={inputClass}
              value={form.issuer}
              onChange={(event) =>
                setForm((current) => ({ ...current, issuer: event.target.value }))
              }
            />
          </Field>
          <Field label="Credential URL">
            <input
              type="url"
              className={inputClass}
              value={form.credentialUrl}
              onChange={(event) =>
                setForm((current) => ({ ...current, credentialUrl: event.target.value }))
              }
            />
          </Field>
          <div className="sm:col-span-3">
            <Button size="sm" onClick={save} disabled={add.isPending || !form.name.trim()}>
              {add.isPending ? (
                <Spinner className="h-4 w-4" />
              ) : (
                <Plus aria-hidden="true" className="h-4 w-4" />
              )}
              Add certification
            </Button>
          </div>
        </div>

        {add.error ?? remove.error ? (
          <Alert variant="danger" title="Certification was not saved">
            {describeError(add.error ?? remove.error)}
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AnswerBank() {
  const answers = useReusableAnswers();
  const remove = useDeleteReusableAnswer();

  return (
    <Card id="answers">
      <SectionHeader
        icon={<Database aria-hidden="true" className="h-4 w-4" />}
        title="Reusable approved answers"
        description="Answers you confirmed on earlier applications. They are scoped to your account; legal declarations are never stored."
        badge={<Badge variant="neutral">{answers.data?.length ?? 0} answers</Badge>}
      />
      <CardContent className="space-y-3">
        {answers.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : answers.isError ? (
          <Alert variant="danger" title="Could not load reusable answers">
            {describeError(answers.error)}
          </Alert>
        ) : (answers.data ?? []).length === 0 ? (
          <p className="text-[12px] text-ink-muted">
            No reusable answers yet. Confirming an answer on an application saves it
            here automatically.
          </p>
        ) : (
          <ul className="space-y-2">
            {(answers.data ?? []).map((answer) => (
              <li
                key={answer.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant={
                        answer.verification_status === "verified" ? "success" : "warning"
                      }
                    >
                      {answer.verification_status === "verified"
                        ? "Approved for reuse"
                        : "Needs verification"}
                    </Badge>
                    {answer.is_sensitive ? (
                      <Badge variant="warning">
                        <ShieldCheck aria-hidden="true" className="h-3 w-3" />
                        Sensitive
                      </Badge>
                    ) : null}
                    <Badge variant="neutral">{answer.question_key}</Badge>
                  </div>
                  <p className="mt-1.5 text-[12px] text-ink-muted">
                    {answer.question_text}
                  </p>
                  <p className="mt-1 break-words text-[13px] text-ink">
                    {answer.answer ?? "No value stored"}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-red-300 hover:text-red-200"
                  disabled={remove.isPending && remove.variables === answer.question_key}
                  onClick={() => remove.mutate(answer.question_key)}
                  aria-label={`Forget answer ${answer.question_key}`}
                >
                  {remove.isPending && remove.variables === answer.question_key ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                  )}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {remove.error ? (
          <Alert variant="danger" title="Could not forget the answer">
            {describeError(remove.error)}
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function CompletenessCard() {
  const completeness = useProfileCompleteness();

  if (completeness.isPending) {
    return (
      <Card>
        <CardContent className="space-y-3">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-2 w-full" />
          <Skeleton className="h-3 w-64" />
        </CardContent>
      </Card>
    );
  }

  if (completeness.isError) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load profile completion">
            {describeError(completeness.error)}
          </Alert>
        </CardContent>
      </Card>
    );
  }

  const data = completeness.data;
  const missingRequired = (data.fields ?? []).filter(
    (field) => field.required && !field.complete,
  );
  const optionalMissing = (data.fields ?? []).filter(
    (field) => !field.required && !field.complete,
  );

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-[13px] font-medium text-ink">
              Profile completeness
            </p>
            <p className="mt-0.5 text-[11px] text-ink-subtle">
              {data.completed} of {data.total} fields filled. Missing fields are
              highlighted in the wizard and never guessed.
            </p>
          </div>
          <span className="text-lg font-semibold text-ink">{data.percent}%</span>
        </div>

        <div
          className="h-2 overflow-hidden rounded-full bg-white/[0.06]"
          role="progressbar"
          aria-valuenow={data.percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Profile completeness"
        >
          <div
            className={cn(
              "h-full rounded-full transition-all",
              data.percent >= 80
                ? "bg-gradient-to-r from-emerald-500 to-teal-400"
                : data.percent >= 50
                  ? "bg-gradient-to-r from-amber-500 to-orange-400"
                  : "bg-gradient-to-r from-red-500 to-rose-400",
            )}
            style={{ width: `${data.percent}%` }}
          />
        </div>

        {missingRequired.length > 0 ? (
          <div>
            <p className="flex items-center gap-1.5 text-[12px] font-medium text-amber-200">
              <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5" />
              Required before applying
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {missingRequired.map((field) => (
                <Badge key={field.field} variant="warning">
                  {field.label}
                </Badge>
              ))}
            </div>
          </div>
        ) : (
          <Alert variant="success" title="Every required profile field is filled">
            Optional fields can still strengthen matching and prefill more forms.
          </Alert>
        )}

        {optionalMissing.length > 0 ? (
          <div>
            <p className="text-[11px] uppercase tracking-[0.08em] text-ink-subtle">
              Optional improvements
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {optionalMissing.map((field) => (
                <Badge key={field.field} variant="neutral">
                  {field.label}
                </Badge>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function ProfileEditor() {
  const profile = useProfile();
  const save = useUpdateProfile();
  const [form, setForm] = useState<ProfileFormState>(emptyProfileForm);

  useEffect(() => {
    if (profile.data) {
      setForm(toProfileForm(profile.data));
    }
  }, [profile.data]);

  if (profile.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (profile.isError || !profile.data) {
    return (
      <Card>
        <CardContent>
          <Alert variant="danger" title="Could not load your profile">
            {profile.error ? describeError(profile.error) : "Profile data is unavailable."}
          </Alert>
          <Button
            variant="secondary"
            size="sm"
            className="mt-4"
            onClick={() => void profile.refetch()}
            disabled={profile.isRefetching}
          >
            <RefreshCw
              aria-hidden="true"
              className={cn("h-3.5 w-3.5", profile.isRefetching && "animate-spin")}
            />
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  const submit = () => {
    const noticePeriod = form.noticePeriod.trim();
    const parsedNotice = noticePeriod ? Number(noticePeriod) : null;
    save.mutate({
      full_name: form.fullName.trim(),
      preferred_name: nullable(form.preferredName),
      email: form.email.trim(),
      phone: nullable(form.phone),
      phone_country_code: nullable(form.phoneCode),
      address_line1: nullable(form.addressLine1),
      address_line2: nullable(form.addressLine2),
      city: nullable(form.city),
      region: nullable(form.region),
      postal_code: nullable(form.postalCode),
      country: nullable(form.country),
      linkedin_url: nullable(form.linkedin),
      github_url: nullable(form.github),
      portfolio_url: nullable(form.portfolio),
      professional_summary: nullable(form.summary),
      skills: form.skills
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      work_authorization: nullable(form.workAuthorization),
      requires_sponsorship:
        form.sponsorship === "" ? null : form.sponsorship === "yes",
      notice_period_days:
        parsedNotice !== null && Number.isFinite(parsedNotice)
          ? Math.max(0, Math.min(365, Math.round(parsedNotice)))
          : null,
      available_from: nullable(form.availableFrom),
    });
  };

  return (
    <div className="space-y-5">
      <CompletenessCard />

      <ProfileBasics form={form} setForm={setForm} />
      <ProfileAddress form={form} setForm={setForm} />
      <ProfileAuthorization form={form} setForm={setForm} />
      <ProfileSkills form={form} setForm={setForm} />

      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-[13px] font-medium text-ink">Save profile details</p>
            <p className="mt-0.5 text-[11px] text-ink-subtle">
              Saves personal, contact, address, skills and work-authorization fields.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {save.isSuccess ? <Badge variant="success">Saved</Badge> : null}
            <Button onClick={submit} disabled={save.isPending || !form.fullName.trim()}>
              {save.isPending ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Saving…
                </>
              ) : (
                <>
                  <Save aria-hidden="true" className="h-4 w-4" />
                  Save profile
                </>
              )}
            </Button>
          </div>
        </CardContent>
        {save.error ? (
          <CardContent className="pt-0">
            <Alert variant="danger" title="Profile was not saved">
              {describeError(save.error)}
            </Alert>
          </CardContent>
        ) : null}
      </Card>

      <ProfilePreferences />
      <EducationSection entries={profile.data.education ?? []} />
      <ExperienceSection entries={profile.data.experiences ?? []} />
      <ProjectSection entries={profile.data.projects ?? []} />
      <CertificationSection entries={profile.data.certifications ?? []} />
      <AnswerBank />
    </div>
  );
}
