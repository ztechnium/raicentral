"use client";

import { useMemo } from "react";
import { usePermissions } from "@/lib/hooks/use-auth";
import { resolveSetupAccess, type SetupAccess } from "@/lib/welcome-gate";

/**
 * Exact-permission setup capability. Uses the server `permissions` list only;
 * never `tenant.canWrite`, a role guess, or `isPlatformAdmin`.
 */
export function useSetupAccess(): {
  access: SetupAccess;
  canReadSetup: boolean;
  canManageSetup: boolean;
  retry: () => Promise<void>;
} {
  const { tenant, isLoading, error, refreshPermissions } = usePermissions();
  const permissions = tenant?.permissions ?? null;

  const access = useMemo(
    () => resolveSetupAccess({ loading: isLoading, error, permissions }),
    [isLoading, error, permissions],
  );

  return {
    access,
    canReadSetup: access === "read" || access === "manage",
    canManageSetup: access === "manage",
    retry: refreshPermissions,
  };
}
