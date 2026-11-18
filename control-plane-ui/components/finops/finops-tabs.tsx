"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { filtersToSearch, parseFilters } from "@/lib/finops-filters";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/finops", label: "Overview", carryFilters: true },
  { href: "/finops/usage", label: "Usage & Costs", carryFilters: true },
  { href: "/finops/budgets", label: "Budgets & Forecasting", carryFilters: false },
  { href: "/finops/pricing", label: "Pricing Catalog", carryFilters: false },
] as const;

/**
 * Local section navigation. The sidebar keeps a single FinOps entry; period and filters
 * follow the user between Overview and Usage & Costs so context is never lost.
 */
export function FinOpsTabs() {
  const pathname = usePathname();
  const search = useSearchParams();
  const carried = filtersToSearch(parseFilters(new URLSearchParams(search.toString())).filters);

  return (
    <nav aria-label="FinOps sections" className="border-b border-base-200">
      <ul className="flex gap-1 overflow-x-auto">
        {TABS.map((tab) => {
          const active =
            tab.href === "/finops" ? pathname === "/finops" : pathname.startsWith(tab.href);
          const query = tab.carryFilters && carried.toString() ? `?${carried.toString()}` : "";
          return (
            <li key={tab.href} className="shrink-0">
              <Link
                href={`${tab.href}${query}`}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px block border-b-2 px-3 py-2 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600",
                  active
                    ? "border-accent-600 font-semibold text-ink-900"
                    : "border-transparent text-ink-500 hover:text-ink-800",
                )}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
