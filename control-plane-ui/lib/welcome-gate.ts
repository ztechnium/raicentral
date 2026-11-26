/**
 * Setup entry policy (Community, single instance).
 *
 * A new organization is sent to /welcome until it finishes or skips setup (see
 * FirstRunGate); everyone else enters it by choice. Capability comes from the exact server permission list
 * (`sdk:read` / `sdk:manage`), never from a platform-role flag or `canWrite`.
 */

export type SetupAccess =
  /** Permission context still loading: show a loading state, do not redirect. */
  | "loading"
  /** Permission context could not be loaded: finite error with Retry. */
  | "error"
  /** No `sdk:read`: finite "setup unavailable" state with a dashboard link. */
  | "unavailable"
  /** `sdk:read` only: status is visible, no mutation controls or PATCHes. */
  | "read"
  /** `sdk:read` and `sdk:manage`: full setup. */
  | "manage";

export function resolveSetupAccess(input: {
  /** `usePermissions().isLoading` */
  loading: boolean;
  /** `usePermissions().error` */
  error?: string | null;
  /** `usePermissions().tenant?.permissions`, or null before the first load. */
  permissions: readonly string[] | null;
}): SetupAccess {
  if (input.loading) return "loading";
  // A failed context fetch may leave a fallback tenant with an empty list. That
  // is "unknown", so it must not be read as "no permission".
  if (input.error) return "error";
  if (input.permissions === null) return "loading";
  const canRead = input.permissions.includes("sdk:read");
  const canManage = input.permissions.includes("sdk:manage");
  if (canRead && canManage) return "manage";
  if (canRead) return "read";
  return "unavailable";
}

export interface SetupAction {
  label: string;
  href: string;
}

/** Dashboard entry action, or null when the user cannot see setup at all. */
export function setupAction(input: {
  access: SetupAccess;
  welcomeComplete: boolean;
  skipped?: boolean;
}): SetupAction | null {
  if (input.access === "read") {
    return { label: "View setup status", href: "/app/onboarding" };
  }
  if (input.access !== "manage") return null;
  if (input.welcomeComplete) return { label: "Continue setup", href: "/app/onboarding" };
  return { label: input.skipped ? "Resume setup" : "Set up RaiCentral", href: "/welcome" };
}

/**
 * Only redirect off the full-screen welcome page once welcome is complete.
 * Every other route is left exactly where the user asked to go.
 */
export function welcomeDestination(input: {
  welcomeCompleted: boolean | null;
  pathname: string;
}): string | null {
  if (input.welcomeCompleted === true && input.pathname === "/welcome") {
    return "/app/onboarding";
  }
  return null;
}

/** Login always lands on the dashboard; setup is resolved inside the app. */
export function postLoginPath(): string {
  return "/dashboard";
}

// Local storage is a navigation aid only. It never overrides server status,
// grants permission, or certifies evidence. The "instance" suffix keeps the
// keys that earlier builds already wrote.
const WELCOME_DONE_KEY = "rai_welcome_done_instance";

export function markWelcomeDoneLocally() {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(WELCOME_DONE_KEY, "1");
  } catch {
    // storage unavailable: navigation hint only
  }
}

export function readWelcomeDoneLocally(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.sessionStorage.getItem(WELCOME_DONE_KEY) === "1";
  } catch {
    return false;
  }
}
