import { ADMIN_AUTH_TOKEN_STORAGE_KEY, refreshAccessTokenIfNeeded } from "@/lib/api";
import { toError } from "@/lib/onboarding-api";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";
const BASE = `${API_BASE_URL}/v1/productization/assistant`;

export interface AssistantTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ProposedCall {
  tool: string;
  args: Record<string, unknown>;
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

export interface ProposedAction {
  summary: string;
  calls: ProposedCall[];
}

export interface AssistantReply {
  reply: string;
  pending_action: ProposedAction | null;
  /** The assistant asks the UI to do something it cannot do server-side. */
  ui_action: "run_onboarding" | null;
  trace: { tool: string; ok: boolean; status: number }[];
}

export interface ExecutedCall {
  tool: string;
  ok: boolean;
  status: number;
  data: unknown;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const send = () => {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json",
    };
    const token =
      typeof window === "undefined" ? null : window.localStorage.getItem(ADMIN_AUTH_TOKEN_STORAGE_KEY);
    if (token) headers.Authorization = `Bearer ${token}`;
    return fetch(`${BASE}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      cache: "no-store",
    });
  };
  let response = await send();
  if (response.status === 401 && (await refreshAccessTokenIfNeeded())) response = await send();
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

export function chatWithAssistant(messages: AssistantTurn[]) {
  return post<AssistantReply>("/chat", { messages });
}

/** Runs writes the user confirmed. The server re-validates every call. */
export function executeProposal(calls: ProposedCall[]) {
  return post<{ results: ExecutedCall[] }>("/execute", {
    calls: calls.map((c) => ({ tool: c.tool, args: c.args })),
  });
}
