"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";

export type WidgetState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "forbidden" }
  | { status: "error"; message: string };

/**
 * Loads one dashboard widget independently so a single failing API never blanks the page.
 * `key` must change whenever the request inputs (period, organization) change.
 */
export function useWidget<T>(
  key: string,
  load: () => Promise<{ data: T }>,
): [WidgetState<T>, () => void] {
  const [state, setState] = useState<WidgetState<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    loadRef
      .current()
      .then((res) => {
        if (active) setState({ status: "ready", data: res.data });
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 403) {
          setState({ status: "forbidden" });
          return;
        }
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "Request failed",
        });
      });
    return () => {
      active = false;
    };
  }, [key, attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return [state, retry];
}
