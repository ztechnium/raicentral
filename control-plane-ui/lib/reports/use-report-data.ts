"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { classifyFailure, type LoadFailure } from "@/lib/reports/client";

export type ReportQuery<T> =
  | { status: "idle" | "loading"; data: T | null; error: null; refetch: () => void }
  | { status: "success"; data: T; error: null; refetch: () => void }
  | { status: "error"; data: T | null; error: LoadFailure; refetch: () => void };

const CACHE_TTL_MS = 60_000;
const cache = new Map<string, { at: number; data: unknown }>();

/** Test hook: forget cached responses. */
export function clearReportCache(): void {
  cache.clear();
}

/**
 * Load one report independently of every other panel.
 *
 * - `key === null` disables the query (e.g. view not mounted).
 * - Identical keys are served from a short in-memory cache so switching tabs
 *   back and forth does not refetch; `refetch()` bypasses it.
 * - A newer request always wins, so a slow stale response can never overwrite
 *   the result of a filter change.
 */
export function useReportData<T>(key: string | null, fetcher: () => Promise<T>): ReportQuery<T> {
  const [state, setState] = useState<{
    status: "idle" | "loading" | "success" | "error";
    data: T | null;
    error: LoadFailure | null;
  }>({ status: "idle", data: null, error: null });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const requestId = useRef(0);
  const [nonce, setNonce] = useState(0);
  const bypass = useRef(false);

  const refetch = useCallback(() => {
    bypass.current = true;
    setNonce((value) => value + 1);
  }, []);

  useEffect(() => {
    if (key === null) {
      setState({ status: "idle", data: null, error: null });
      return;
    }
    const id = ++requestId.current;
    const skipCache = bypass.current;
    bypass.current = false;

    if (!skipCache) {
      const hit = cache.get(key);
      if (hit && Date.now() - hit.at < CACHE_TTL_MS) {
        setState({ status: "success", data: hit.data as T, error: null });
        return;
      }
    }

    // Keep previous data visible while reloading to avoid layout jumps.
    setState((prev) => ({ status: "loading", data: prev.data, error: null }));
    fetcherRef
      .current()
      .then((data) => {
        if (id !== requestId.current) return;
        cache.set(key, { at: Date.now(), data });
        setState({ status: "success", data, error: null });
      })
      .catch((error: unknown) => {
        if (id !== requestId.current) return;
        setState((prev) => ({ status: "error", data: prev.data, error: classifyFailure(error) }));
      });
  }, [key, nonce]);

  return { ...state, refetch } as ReportQuery<T>;
}
