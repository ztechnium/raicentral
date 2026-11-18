"use client";

import { useState } from "react";
import { FormField } from "@/components/registry/form-field";
import { Panel } from "@/components/registry/panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { Department, OrganizationMembership } from "@/lib/types";

/** Organization admins can only hand out these three roles. */
export const TEAM_ROLES = [
  {
    value: "ORGANIZATION_ADMIN",
    label: "Organization admin",
    description: "Manages departments, people, and access for the organization.",
  },
  {
    value: "RAI_OFFICER",
    label: "RAI officer",
    description: "Owns responsible-AI reviews, approvals, and governance decisions.",
  },
  {
    value: "AUDITOR",
    label: "Auditor",
    description: "Read-only access to evidence, reports, and audit trails.",
  },
] as const;

const ROLE_LABELS: Record<string, string> = Object.fromEntries(
  TEAM_ROLES.map((role) => [role.value, role.label]),
);

export type Person = {
  userId: string;
  name: string;
  email: string;
  roles: OrganizationMembership[];
};

export function roleLabel(role: string) {
  return ROLE_LABELS[role] ?? role.replace(/_/g, " ").toLowerCase();
}

export function initials(name: string) {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  const letters = parts.map((part) => part[0]?.toUpperCase() ?? "").join("");
  return letters || "?";
}

export function personDepartment(person: Person) {
  const match = person.roles.find((membership) => membership.department_id);
  return {
    id: match?.department_id ?? null,
    name: match?.department_name?.trim() || null,
  };
}

export function OrganizationHierarchy({
  organizationName,
  departments,
  people,
  canManage,
  actingId,
  onMove,
}: {
  organizationName: string;
  departments: Department[];
  people: Person[];
  canManage: boolean;
  actingId: string | null;
  onMove: (person: Person, departmentId: string | null) => void;
}) {
  const assigned = new Set<string>();
  const groups = departments.map((department) => {
    const members = people.filter((person) => personDepartment(person).id === department.id);
    for (const person of members) assigned.add(person.userId);
    return { department, members };
  });
  const unassigned = people.filter((person) => !assigned.has(person.userId));

  const renderMember = (person: Person) => (
    <li
      key={person.userId}
      className="flex flex-wrap items-center gap-3 rounded-md border border-base-200 px-3 py-2"
    >
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-100 text-xs font-semibold text-accent-700">
        {initials(person.name)}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink-900">{person.name}</p>
        {person.email ? <p className="truncate text-xs text-ink-500">{person.email}</p> : null}
      </div>
      <div className="flex flex-wrap gap-1">
        {person.roles.map((membership) => (
          <Badge key={membership.id} variant="neutral">
            {roleLabel(membership.role)}
          </Badge>
        ))}
      </div>
      {canManage ? (
        <Select
          aria-label={`Move ${person.name}`}
          value={personDepartment(person).id ?? ""}
          disabled={actingId === person.userId}
          onChange={(event) => onMove(person, event.target.value || null)}
        >
          <option value="">Unassigned</option>
          {departments.map((department) => (
            <option key={department.id} value={department.id}>
              {department.name}
            </option>
          ))}
        </Select>
      ) : null}
    </li>
  );

  return (
    <div data-testid="organization-hierarchy" className="text-sm">
      <div className="flex items-center gap-3 rounded-lg border border-accent-500/30 bg-accent-100 px-4 py-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-accent-600 text-sm font-semibold text-white">
          {initials(organizationName)}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs uppercase tracking-wide text-accent-700">Company</p>
          <p className="truncate font-semibold text-ink-900">{organizationName}</p>
        </div>
        <p className="text-xs text-ink-600">
          {departments.length} departments · {people.length} members
        </p>
      </div>
      <ul className="ml-5 mt-3 space-y-4 border-l-2 border-base-200 pl-5">
        {groups.map(({ department, members }) => (
          <li key={department.id} className="relative">
            <span className="absolute -left-5 top-3 h-px w-5 bg-base-200" aria-hidden />
            <div className="flex items-center gap-2">
              <p className="font-semibold text-ink-900">{department.name}</p>
              <Badge variant="neutral">{members.length}</Badge>
            </div>
            {department.description ? (
              <p className="text-xs text-ink-500">{department.description}</p>
            ) : null}
            <ul className="mt-2 space-y-2 border-l border-base-200 pl-4">
              {members.length === 0 ? (
                <li className="text-xs text-ink-400">No team members</li>
              ) : (
                members.map(renderMember)
              )}
            </ul>
          </li>
        ))}
        {unassigned.length > 0 ? (
          <li className="relative">
            <span className="absolute -left-5 top-3 h-px w-5 bg-base-200" aria-hidden />
            <div className="flex items-center gap-2">
              <p className="font-semibold text-ink-900">Unassigned</p>
              <Badge variant="neutral">{unassigned.length}</Badge>
            </div>
            <ul className="mt-2 space-y-2 border-l border-base-200 pl-4">
              {unassigned.map(renderMember)}
            </ul>
          </li>
        ) : null}
      </ul>
    </div>
  );
}

export function DepartmentManager({
  departments,
  people,
  canManage,
  busy,
  onCreate,
  onUpdate,
  onDelete,
}: {
  departments: Department[];
  people: Person[];
  canManage: boolean;
  busy: boolean;
  onCreate: (name: string, description: string) => Promise<boolean>;
  onUpdate: (id: string, name: string, description: string) => Promise<boolean>;
  onDelete: (department: Department, memberCount: number) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");

  const counts = new Map<string, number>();
  for (const person of people) {
    const id = personDepartment(person).id;
    if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
  }

  return (
    <div className="space-y-4">
      {canManage ? (
        <Panel title="Add a department" description="Departments group people under the company.">
          <div className="grid gap-4 md:grid-cols-[1fr_2fr_auto] md:items-end">
            <FormField label="Department name" required>
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Legal"
              />
            </FormField>
            <FormField label="Description">
              <Input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What this department does"
              />
            </FormField>
            <Button
              disabled={busy || !name.trim()}
              onClick={async () => {
                if (await onCreate(name, description)) {
                  setName("");
                  setDescription("");
                }
              }}
            >
              Add department
            </Button>
          </div>
        </Panel>
      ) : null}
      <Panel title="Departments" description={`${departments.length} in this organization.`}>
        {departments.length === 0 ? (
          <p className="text-sm text-ink-500">No departments yet.</p>
        ) : (
          <ul className="divide-y divide-base-200">
            {departments.map((department) => {
              const count = counts.get(department.id) ?? 0;
              return (
                <li key={department.id} className="py-3">
                  {editingId === department.id ? (
                    <div className="grid gap-3 md:grid-cols-[1fr_2fr_auto] md:items-end">
                      <FormField label="Name">
                        <Input
                          value={editName}
                          onChange={(event) => setEditName(event.target.value)}
                        />
                      </FormField>
                      <FormField label="Description">
                        <Input
                          value={editDescription}
                          onChange={(event) => setEditDescription(event.target.value)}
                        />
                      </FormField>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          disabled={busy || !editName.trim()}
                          onClick={async () => {
                            if (await onUpdate(department.id, editName, editDescription)) {
                              setEditingId(null);
                            }
                          }}
                        >
                          Save
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-ink-900">{department.name}</p>
                        {department.description ? (
                          <p className="text-xs text-ink-500">{department.description}</p>
                        ) : null}
                      </div>
                      <Badge variant="neutral">
                        {count} {count === 1 ? "member" : "members"}
                      </Badge>
                      {canManage ? (
                        <div className="flex gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            aria-label={`Edit department ${department.name}`}
                            onClick={() => {
                              setEditingId(department.id);
                              setEditName(department.name);
                              setEditDescription(department.description ?? "");
                            }}
                          >
                            Edit
                          </Button>
                          <Button
                            variant="danger"
                            size="sm"
                            aria-label={`Delete department ${department.name}`}
                            disabled={busy}
                            onClick={() => onDelete(department, count)}
                          >
                            Delete
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}
