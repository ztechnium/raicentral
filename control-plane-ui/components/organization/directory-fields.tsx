"use client";

import { useEffect, useMemo, useState } from "react";
import { Select } from "@/components/ui/select";
import { listOrganizationDepartments, listOrganizationTeam } from "@/lib/api";
import type { Department, OrganizationMembership } from "@/lib/types";

export type DirectoryPerson = {
  userId: string;
  label: string;
  departmentId: string | null;
  departmentName: string | null;
};

export type Directory = {
  departments: Department[];
  people: DirectoryPerson[];
  loading: boolean;
};

/** Departments and de-duplicated active people (one row per user) of the active organization. */
export function useOrganizationDirectory(): Directory {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [members, setMembers] = useState<OrganizationMembership[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listOrganizationDepartments().catch(() => null),
      listOrganizationTeam({ limit: 500 }).catch(() => null),
    ])
      .then(([deps, team]) => {
        if (cancelled) return;
        setDepartments(deps?.data.items ?? []);
        setMembers(team?.data.items ?? []);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const people = useMemo(() => {
    const byUser = new Map<string, DirectoryPerson>();
    for (const m of members) {
      if (m.status && m.status !== "ACTIVE") continue;
      const existing = byUser.get(m.user_id);
      if (existing && existing.departmentId) continue;
      const name = m.user_name || m.user_email || m.user_id;
      byUser.set(m.user_id, {
        userId: m.user_id,
        label: m.user_email && m.user_name ? `${m.user_name} (${m.user_email})` : name,
        departmentId: m.department_id ?? null,
        departmentName: m.department_name ?? null,
      });
    }
    return [...byUser.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [members]);

  return { departments, people, loading };
}

/** Optional department picker. The stored value is the department name. */
export function DepartmentSelect({
  directory,
  value,
  onChange,
  disabled,
}: {
  directory: Directory;
  value: string;
  onChange: (departmentName: string) => void;
  disabled?: boolean;
}) {
  const known = directory.departments.some((d) => d.name === value);
  return (
    <Select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      <option value="">{directory.loading ? "Loading…" : "No department"}</option>
      {value && !known && <option value={value}>{value}</option>}
      {directory.departments.map((d) => (
        <option key={d.id} value={d.name}>
          {d.name}
        </option>
      ))}
    </Select>
  );
}

/**
 * Optional person picker. When `departmentName` is set only members of that department are
 * offered. The stored value is the user id, so it matches the acting principal at runtime.
 */
export function PersonSelect({
  directory,
  value,
  onChange,
  departmentName,
  disabled,
  emptyLabel = "No owner",
}: {
  directory: Directory;
  value: string;
  onChange: (userId: string) => void;
  departmentName?: string;
  disabled?: boolean;
  emptyLabel?: string;
}) {
  const options = directory.people.filter(
    (p) => !departmentName || p.departmentName === departmentName,
  );
  const known = options.some((p) => p.userId === value);
  return (
    <Select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      <option value="">{directory.loading ? "Loading…" : emptyLabel}</option>
      {value && !known && <option value={value}>{value}</option>}
      {options.map((p) => (
        <option key={p.userId} value={p.userId}>
          {p.label}
        </option>
      ))}
    </Select>
  );
}
