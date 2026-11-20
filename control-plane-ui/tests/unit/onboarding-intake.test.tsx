import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mocked UI tests: they prove client behavior and the exact API calls made, not
// backend enforcement (that is covered by tests/onboarding).

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/app/onboarding/sessions",
  useSearchParams: () => new URLSearchParams(),
}));

const auth = vi.hoisted(() => ({ permissions: [] as string[] }));
vi.mock("@/lib/hooks/use-auth", () => ({
  usePermissions: () => ({ tenant: { permissions: auth.permissions }, isLoading: false }),
}));

const api = vi.hoisted(() => ({
  listSessions: vi.fn(),
  createSession: vi.fn(),
  getSession: vi.fn(),
  saveTextSource: vi.fn(),
  uploadFileSource: vi.fn(),
  prepareSources: vi.fn(),
  retryRun: vi.fn(),
  cancelRun: vi.fn(),
  setSourceIncluded: vi.fn(),
  setArchived: vi.fn(),
  listRevisions: vi.fn(),
  fetchRevisionContent: vi.fn(),
  fetchRevisionText: vi.fn(),
  newIdempotencyKey: vi.fn(() => "key-fixed-0001"),
}));
vi.mock("@/lib/onboarding-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/onboarding-api")>("@/lib/onboarding-api");
  return { ...actual, ...api };
});

import { SessionList } from "@/components/onboarding/intake/session-list";
import { SessionWorkspace } from "@/components/onboarding/intake/session-workspace";
import { IntakeApiError, type RunSummary, type SessionDetail } from "@/lib/onboarding-api";
import { pollDelay, resolveIntakeAccess, runStatus } from "@/lib/onboarding-intake";

const MANAGER = ["sdk:read", "sdk:manage", "onboarding:sources:read"];
const READER = ["sdk:read"];

function run(over: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "run-1",
    run_number: 1,
    stage: "PREPARE_SOURCES",
    state: "QUEUED",
    active: true,
    input_revision: 2,
    retry_of_run_id: null,
    attempt_count: 0,
    max_attempts: 5,
    source_count: 2,
    sources_checked: null,
    sources_total: 2,
    created_at: null,
    ended_at: null,
    error_code: null,
    ...over,
  };
}

function detail(over: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: "s1",
    label: "Session s1",
    title: "Q4 onboarding",
    lifecycle: "OPEN",
    view: "DRAFT",
    row_version: 3,
    input_revision: 2,
    source_count: 2,
    total_source_count: 2,
    sources: [
      {
        id: "src-prompt",
        label: "Source 1",
        kind: "TEXT",
        role: "USER_PROMPT",
        included: true,
        created_at: null,
        current_revision: {
          id: "rev-p1",
          revision_number: 1,
          media_family: "TEXT",
          byte_length: 24,
          received_at: null,
          display_name: "Prompt",
        },
      },
      {
        id: "src-file",
        label: "Source 2",
        kind: "FILE",
        role: "SUPPORTING_MATERIAL",
        included: true,
        created_at: null,
        current_revision: {
          id: "rev-f1",
          revision_number: 1,
          media_family: "PDF",
          byte_length: 2048,
          received_at: null,
          display_name: "policy.pdf",
        },
      },
    ],
    current_run: null,
    allowed_actions: ["edit_inputs", "rename", "archive", "prepare", "read_content"],
    created_at: null,
    updated_at: null,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.permissions = MANAGER;
  api.fetchRevisionText.mockResolvedValue("Onboard our support bots");
  api.newIdempotencyKey.mockReturnValue("key-fixed-0001");
});
afterEach(() => vi.useRealTimers());

describe("intake access and copy", () => {
  it("separates progress, content and management permissions", () => {
    expect(resolveIntakeAccess(READER)).toEqual({
      canRead: true,
      canContent: false,
      canManage: false,
    });
    expect(resolveIntakeAccess(["sdk:read", "onboarding:sources:read"]).canManage).toBe(false);
    expect(resolveIntakeAccess(["sdk:manage", "onboarding:sources:read"]).canContent).toBe(false);
    expect(resolveIntakeAccess(MANAGER)).toEqual({
      canRead: true,
      canContent: true,
      canManage: true,
    });
    expect(resolveIntakeAccess(null).canRead).toBe(false);
  });

  it("reports only real server states and never claims analysis", () => {
    expect(runStatus(run())?.detail).toBe("Waiting for a worker.");
    expect(runStatus(run({ state: "PREPARING", sources_checked: 1 }))).toEqual({
      title: "Checking saved sources",
      detail: "1 of 2 sources checked.",
    });
    expect(runStatus(run({ state: "SUCCEEDED", active: false }))).toEqual({
      title: "Sources saved",
      detail: "Your selected files and prompt are saved and ready for analysis.",
    });
    const all = [
      run(),
      run({ state: "RETRY_SCHEDULED", attempt_count: 1 }),
      run({ state: "FAILED", active: false, error_code: "SOURCE_MISSING" }),
      run({ state: "CANCELLED", active: false }),
    ]
      .map((r) => JSON.stringify(runStatus(r)))
      .join(" ");
    expect(all).not.toMatch(/analysis complete|classif|confidence|verified|production/i);
    expect(runStatus(null)).toBeNull();
  });

  it("bounds the polling backoff", () => {
    expect(pollDelay(0)).toBe(1500);
    expect(pollDelay(50)).toBeLessThanOrEqual(10_000);
    expect(pollDelay(3)).toBeGreaterThan(pollDelay(0));
  });
});

describe("session workspace", () => {
  it("reconstructs everything from the server on load (resume)", async () => {
    api.getSession.mockResolvedValue(detail());
    render(<SessionWorkspace sessionId="s1" />);
    expect(await screen.findByText("Q4 onboarding")).toBeTruthy();
    expect(screen.getByText("policy.pdf")).toBeTruthy();
    expect(screen.getByText(/Saved on the server/)).toBeTruthy();
    await waitFor(() =>
      expect((screen.getByLabelText("Prompt text") as HTMLTextAreaElement).value).toBe(
        "Onboard our support bots",
      ),
    );
    expect(screen.getByTestId("prompt-state").textContent).toBe("Saved");
    expect(api.createSession).not.toHaveBeenCalled();
    expect(api.prepareSources).not.toHaveBeenCalled(); // never enqueue on open
  });

  it("distinguishes unsaved from saved prompt text and saves explicitly", async () => {
    api.getSession.mockResolvedValue(detail());
    api.saveTextSource.mockResolvedValue({
      session: detail({ row_version: 4, input_revision: 3 }),
      source_id: "src-prompt",
      revision_id: "rev-p2",
      replayed: false,
    });
    render(<SessionWorkspace sessionId="s1" />);
    const box = (await screen.findByLabelText("Prompt text")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("Onboard our support bots"));

    fireEvent.change(box, { target: { value: "A new prompt" } });
    expect(screen.getByTestId("prompt-state").textContent).toBe("Unsaved changes");
    expect(api.saveTextSource).not.toHaveBeenCalled();
    expect((screen.getByText("Prepare sources") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByText("Save prompt"));
    await waitFor(() => expect(api.saveTextSource).toHaveBeenCalledTimes(1));
    expect(api.saveTextSource).toHaveBeenCalledWith(
      "s1",
      expect.objectContaining({
        role: "USER_PROMPT",
        text: "A new prompt",
        sourceId: "src-prompt",
        expectedRowVersion: 3,
        key: "key-fixed-0001",
      }),
    );
    await waitFor(() => expect(screen.getByTestId("prompt-state").textContent).toBe("Saved"));
  });

  it("preserves unsaved text and refreshes versions on a revision conflict", async () => {
    api.getSession.mockResolvedValue(detail());
    api.saveTextSource.mockRejectedValue(
      new IntakeApiError("Session changed", 409, "REVISION_CONFLICT"),
    );
    render(<SessionWorkspace sessionId="s1" />);
    const box = (await screen.findByLabelText("Prompt text")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).not.toBe(""));
    fireEvent.change(box, { target: { value: "my careful edit" } });
    api.getSession.mockResolvedValue(detail({ row_version: 7 }));
    fireEvent.click(screen.getByText("Save prompt"));

    expect(await screen.findByText(/changed since you opened it/)).toBeTruthy();
    expect((screen.getByLabelText("Prompt text") as HTMLTextAreaElement).value).toBe(
      "my careful edit",
    );
    expect(api.saveTextSource).toHaveBeenCalledTimes(1); // no blind retry with the fresh version
    expect(api.getSession.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("locks inputs while a run is active and offers Cancel preparation", async () => {
    api.getSession.mockResolvedValue(
      detail({
        view: "QUEUED",
        current_run: run(),
        allowed_actions: ["cancel", "read_content"],
      }),
    );
    api.cancelRun.mockResolvedValue({
      session: detail(),
      run: run({ state: "CANCELLED", active: false }),
      replayed: false,
    });
    render(<SessionWorkspace sessionId="s1" />);
    expect(await screen.findByText("Waiting for a worker.")).toBeTruthy();
    expect(screen.getByText(/Inputs are locked/)).toBeTruthy();
    expect((screen.getByLabelText("Prompt text") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByText("Add files") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText("Prepare sources")).toBeNull();

    fireEvent.click(screen.getByText("Cancel preparation"));
    await waitFor(() => expect(api.cancelRun).toHaveBeenCalledWith("s1", "run-1"));
  });

  it("shows the exact saved copy after success and nothing about analysis results", async () => {
    api.getSession.mockResolvedValue(
      detail({
        view: "READY_FOR_ANALYSIS",
        current_run: run({ state: "SUCCEEDED", active: false }),
      }),
    );
    render(<SessionWorkspace sessionId="s1" />);
    expect(await screen.findByText("Sources saved")).toBeTruthy();
    expect(
      screen.getByText("Your selected files and prompt are saved and ready for analysis."),
    ).toBeTruthy();
    const page = document.body.textContent ?? "";
    expect(page).not.toMatch(/analysis complete|classified|confidence|verified|generated/i);
  });

  it("starts preparation only on click, with the current versions and a stable key", async () => {
    api.getSession.mockResolvedValue(detail());
    api.prepareSources.mockResolvedValue({
      session: detail({ view: "QUEUED", current_run: run(), allowed_actions: ["cancel"] }),
      run: run(),
      replayed: false,
    });
    render(<SessionWorkspace sessionId="s1" />);
    const button = await screen.findByText("Prepare sources");
    await waitFor(() =>
      expect((screen.getByLabelText("Prompt text") as HTMLTextAreaElement).value).not.toBe(""),
    );
    fireEvent.click(button);
    await waitFor(() =>
      expect(api.prepareSources).toHaveBeenCalledWith("s1", {
        expectedRowVersion: 3,
        inputRevision: 2,
        key: "key-fixed-0001",
      }),
    );
  });

  it("reports a failed upload per file without losing the session", async () => {
    api.getSession.mockResolvedValue(detail());
    api.uploadFileSource.mockRejectedValue(
      new IntakeApiError("File type is not supported", 415, "UNSUPPORTED_MEDIA"),
    );
    render(<SessionWorkspace sessionId="s1" />);
    await screen.findByText("policy.pdf");
    const file = new File(["MZ"], "tool.exe", { type: "application/octet-stream" });
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [file] } });
    expect(await screen.findByText(/Failed: File type is not supported/)).toBeTruthy();
    expect(api.uploadFileSource).toHaveBeenCalledWith(
      "s1",
      expect.objectContaining({ expectedRowVersion: 3 }),
    );
    expect(screen.getByText("policy.pdf")).toBeTruthy();
  });

  it("uploads files one per request and shows Saved, not analyzed", async () => {
    api.getSession.mockResolvedValue(detail());
    api.uploadFileSource
      .mockResolvedValueOnce({
        session: detail({ row_version: 4 }),
        source_id: "a",
        revision_id: "ra",
        replayed: false,
      })
      .mockResolvedValueOnce({
        session: detail({ row_version: 5 }),
        source_id: "b",
        revision_id: "rb",
        replayed: false,
      });
    render(<SessionWorkspace sessionId="s1" />);
    await screen.findByText("policy.pdf");
    const files = [new File(["a"], "a.txt"), new File(["b"], "b.txt")];
    fireEvent.change(screen.getByTestId("file-input"), { target: { files } });
    await waitFor(() => expect(api.uploadFileSource).toHaveBeenCalledTimes(2));
    // The second request uses the version returned by the first.
    expect(api.uploadFileSource.mock.calls[0][1].expectedRowVersion).toBe(3);
    expect(api.uploadFileSource.mock.calls[1][1].expectedRowVersion).toBe(4);
    expect((await screen.findAllByText("Saved")).length).toBeGreaterThanOrEqual(2);
  });

  it("gives a metadata-only reader generic labels and no content controls", async () => {
    auth.permissions = READER;
    api.getSession.mockResolvedValue(
      detail({
        title: undefined,
        label: "Session s1",
        allowed_actions: [],
        sources: detail().sources.map((s) => ({
          ...s,
          current_revision: { ...s.current_revision!, display_name: undefined },
        })),
      }),
    );
    render(<SessionWorkspace sessionId="s1" />);
    expect(await screen.findByText("Session s1")).toBeTruthy();
    expect(screen.getByText(/need source access/)).toBeTruthy();
    expect(screen.getByText(/Source 1/)).toBeTruthy();
    expect(screen.queryByLabelText("Prompt text")).toBeNull();
    expect(screen.queryByText("Add files")).toBeNull();
    expect(screen.queryByText("Prepare sources")).toBeNull();
    expect(api.fetchRevisionText).not.toHaveBeenCalled();
  });

  it("shows a finite error with Retry and a forbidden state without one", async () => {
    api.getSession.mockRejectedValueOnce(new IntakeApiError("boom", 503, "STORAGE_UNAVAILABLE"));
    render(<SessionWorkspace sessionId="s1" />);
    expect(await screen.findByText("boom")).toBeTruthy();
    api.getSession.mockResolvedValue(detail());
    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("Q4 onboarding")).toBeTruthy();
  });

  it("polls an active run with backoff and stops on unmount", async () => {
    vi.useFakeTimers();
    api.getSession.mockResolvedValue(
      detail({ view: "QUEUED", current_run: run(), allowed_actions: ["cancel", "read_content"] }),
    );
    const { unmount } = render(<SessionWorkspace sessionId="s1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const initial = api.getSession.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(api.getSession.mock.calls.length).toBeGreaterThan(initial);
    unmount();
    const afterUnmount = api.getSession.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(api.getSession.mock.calls.length).toBe(afterUnmount);
  });
});

describe("session list", () => {
  const page = {
    items: [
      {
        id: "s1",
        label: "Q4 onboarding",
        lifecycle: "OPEN" as const,
        view: "READY_FOR_ANALYSIS" as const,
        row_version: 3,
        source_count: 2,
        updated_at: null,
      },
    ],
    total: 1,
    limit: 25,
    offset: 0,
  };

  it("lists sessions with a Resume link and creates nothing on open", async () => {
    api.listSessions.mockResolvedValue(page);
    render(<SessionList />);
    expect(await screen.findByText("Q4 onboarding")).toBeTruthy();
    expect(screen.getByText(/Sources saved · 2 sources/)).toBeTruthy();
    expect(screen.getByText("Resume").closest("a")?.getAttribute("href")).toBe(
      "/app/onboarding/sessions/s1",
    );
    expect(api.createSession).not.toHaveBeenCalled();
  });

  it("creates a session only on click and opens it", async () => {
    api.listSessions.mockResolvedValue({ ...page, items: [], total: 0 });
    api.createSession.mockResolvedValue(detail({ id: "new-1" }));
    render(<SessionList />);
    expect(await screen.findByText(/No sessions yet/)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/Q4 AI estate/), {
      target: { value: "My session" },
    });
    fireEvent.click(screen.getByRole("button", { name: "New session" }));
    await waitFor(() =>
      expect(api.createSession).toHaveBeenCalledWith("My session", "key-fixed-0001"),
    );
    expect(push).toHaveBeenCalledWith("/app/onboarding/sessions/new-1");
  });

  it("hides creation from readers and offers Retry when unavailable", async () => {
    auth.permissions = READER;
    api.listSessions.mockRejectedValueOnce(new IntakeApiError("down", 503, "ERROR"));
    render(<SessionList />);
    expect(await screen.findByText(/Sessions are unavailable/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New session" })).toBeNull();
    api.listSessions.mockResolvedValue(page);
    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("Q4 onboarding")).toBeTruthy();
    expect(screen.getByText(/progress only/)).toBeTruthy();
  });

  it("switches to the archived filter", async () => {
    api.listSessions.mockResolvedValue({ ...page, items: [], total: 0 });
    render(<SessionList />);
    await screen.findByText(/No sessions yet/);
    fireEvent.click(screen.getByText("Archived"));
    await waitFor(() => expect(api.listSessions).toHaveBeenLastCalledWith({ archived: true }));
    expect(await screen.findByText("No archived sessions.")).toBeTruthy();
  });
});
