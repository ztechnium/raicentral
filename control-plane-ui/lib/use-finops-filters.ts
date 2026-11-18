"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import {
  FILTER_KEYS,
  filtersToQuery,
  filtersToSearch,
  parseFilters,
  type FinOpsFilterState,
} from "@/lib/finops-filters";

const PERIOD_KEYS = ["period", "from", "to"];

/**
 * URL-synchronised FinOps filters. The URL is the single source of truth, so a view is
 * shareable, survives reload, and carries across tabs. Parameters this hook does not own
 * (dimension, sort, page, ...) are preserved when filters change.
 */
export function useFinOpsFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const [refreshKey, setRefreshKey] = useState(0);

  const searchString = search.toString();
  const { filters, errors } = useMemo(
    () => parseFilters(new URLSearchParams(searchString)),
    [searchString],
  );

  /** Identity of the data inputs; changes only when a filter, period or refresh changes. */
  const dataKey = useMemo(
    () => `${filtersToSearch(filters).toString()}#${refreshKey}`,
    [filters, refreshKey],
  );
  const query = useMemo(
    () => filtersToQuery(filters),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- end-of-window is pinned per dataKey
    [dataKey],
  );

  const replace = useCallback(
    (params: URLSearchParams) => {
      const next = params.toString();
      router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  const setFilters = useCallback(
    (next: FinOpsFilterState) => {
      const params = new URLSearchParams(searchString);
      for (const key of [...PERIOD_KEYS, ...FILTER_KEYS]) params.delete(key);
      filtersToSearch(next).forEach((value, key) => params.set(key, value));
      params.delete("page"); // a new filter set invalidates the current page
      replace(params);
    },
    [replace, searchString],
  );

  const setParams = useCallback(
    (patch: Record<string, string | undefined>) => {
      const params = new URLSearchParams(searchString);
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined || value === "") params.delete(key);
        else params.set(key, value);
      }
      replace(params);
    },
    [replace, searchString],
  );

  const refresh = useCallback(() => setRefreshKey((value) => value + 1), []);

  return {
    filters,
    errors,
    query,
    dataKey,
    params: search,
    setFilters,
    setParams,
    refresh,
  };
}
