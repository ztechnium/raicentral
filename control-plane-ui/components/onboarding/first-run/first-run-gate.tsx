"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getFirstRunStatus } from "@/lib/first-run";

/**
 * Sends a new organization to setup before the console is shown.
 *
 * The server decides (`should_redirect`). Once it says no, this session is not
 * asked again. A failed check never locks anyone out of the console.
 */
export function FirstRunGate({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [cleared, setCleared] = useState(false);
  const onSetup = pathname === "/welcome";

  useEffect(() => {
    if (cleared || onSetup) return;
    let active = true;
    void getFirstRunStatus()
      .then(({ data }) => {
        if (!active) return;
        if (data.should_redirect) router.replace("/welcome");
        else setCleared(true);
      })
      .catch(() => {
        if (active) setCleared(true);
      });
    return () => {
      active = false;
    };
  }, [cleared, onSetup, pathname, router]);

  return <>{cleared || onSetup ? children : fallback}</>;
}
