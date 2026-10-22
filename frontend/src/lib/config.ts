/**
 * Connection config for the LangGraph Agent Server.
 *
 * Base URL precedence: localStorage "lgUrl" → `window.LG.url` (config.js, which
 * the deployment writes as its own origin and token, see custom_demo/web/spa.py) →
 * build-time `VITE_LG_URL` (frontend/.env.local for a local dev server) →
 * http://127.0.0.1:2024. The assistant id is a UUID (a specific assistant
 * variant) or the graph id "dashboard_agent" (the graph's default assistant).
 */

/** The registered graph id on the Agent Server. */
// Deliberately NOT the Python package name (`custom_demo`). This is the key in
// langgraph.json's `graphs` object and the graph_id every stored assistant is bound
// to, so it cannot be renamed to match. See AGENTS.md, "The graph id is frozen".
export const GRAPH_ID = "dashboard_agent";

/** Build-time overrides (baked in by Vite from VITE_* env vars at build). */
const ENV_URL = (import.meta.env.VITE_LG_URL as string | undefined) || "";
const ENV_API_KEY = (import.meta.env.VITE_LG_API_KEY as string | undefined) || "";

/** Default Agent Server base URL (build-time env, else local dev). */
export const DEFAULT_API_BASE = ENV_URL || "http://127.0.0.1:2024";

/**
 * Where the agent runs when it is on Managed Deep Agents rather than this
 * deployment, and a token that deployment accepts (custom_demo/agent_auth.py).
 * `expiresAt` is unix seconds.
 */
export interface AgentConnection {
  url: string;
  token: string;
  expiresAt: number;
}

/** Shape of the optional `window.LG` base config injected via config.js. */
export interface LGGlobal {
  url?: string;
  assistantId?: string;
  apiKey?: string;
  agent?: AgentConnection;
}

declare global {
  interface Window {
    LG?: LGGlobal;
  }
}

const LG_URL_KEY = "lgUrl";
const LG_API_KEY_KEY = "lgApiKey";
const LG_ASSISTANT_KEY = "lgAssistantId";

function windowLG(): LGGlobal {
  return (typeof window !== "undefined" && window.LG) || {};
}

function readLS(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writeLS(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* ignore quota / privacy-mode errors */
  }
}

/** Resolved Agent Server base URL, with any trailing slashes stripped. */
export function getApiBase(): string {
  const raw = readLS(LG_URL_KEY) || windowLG().url || DEFAULT_API_BASE;
  return String(raw).replace(/\/+$/, "");
}


/** Resolved assistant id (UUID or the "dashboard_agent" graph default). */
export function getAssistantId(): string {
  return readLS(LG_ASSISTANT_KEY) || windowLG().assistantId || agentGraphId();
}

/** Persist the active assistant id to localStorage. */
export function setAssistantId(id: string): void {
  writeLS(LG_ASSISTANT_KEY, (id || "").trim());
}

/** Optional API key for a secured/cloud deployment (sent as x-api-key). */
export function getApiKey(): string {
  return readLS(LG_API_KEY_KEY) || windowLG().apiKey || ENV_API_KEY;
}


/**
 * True when `id` is a real assistant row (a UUID) that can be PATCHed/DELETEd.
 * The "dashboard_agent" graph-default pseudo-entry is not a stored assistant.
 */
export function isAssistantId(id: string | undefined | null): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id || "");
}

/** JSON headers plus x-api-key when an API key is configured. */
export function apiHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const k = getApiKey();
  if (k) h["x-api-key"] = k;
  return h;
}

/* ------------------------- The agent's deployment ------------------------- */
/*
 * The agent (its assistants, threads and runs) can live on a Managed Deep Agents
 * deployment while everything else (setup, files, evals, MCP proxy) stays on this
 * one. config.js says which: `window.LG.agent` is present only when it is MDA.
 * MDA turns the agent name `dashboard_agent` into the graph id `dashboard-agent`.
 */

const MDA_GRAPH_ID = "dashboard-agent";
// Refresh this long before expiry, so a token never lapses mid-request.
const TOKEN_SLACK_SECONDS = 60;

let agentConnection: AgentConnection | undefined;

function currentAgent(): AgentConnection | undefined {
  return agentConnection || windowLG().agent;
}

/** Whether the agent runs on a Managed Deep Agents deployment. */
export function agentOnMda(): boolean {
  return Boolean(currentAgent()?.url);
}

/** Base URL for the agent's assistants, threads and runs. */
export function getAgentBase(): string {
  const agent = currentAgent();
  return agent?.url ? agent.url.replace(/\/+$/, "") : getApiBase();
}

/** The graph id assistants are bound to on the agent's deployment. */
export function agentGraphId(): string {
  return agentOnMda() ? MDA_GRAPH_ID : GRAPH_ID;
}

/** Fetch a fresh MDA token from this deployment (POST /agent-auth/token). */
async function refreshAgentToken(): Promise<AgentConnection> {
  const res = await fetch(`${getApiBase()}/agent-auth/token`, {
    method: "POST",
    headers: apiHeaders(),
    body: "{}",
  });
  if (!res.ok) throw new Error(`could not get a token for the agent: HTTP ${res.status}`);
  const body = (await res.json()) as { url: string; token: string; expires_at: number };
  agentConnection = { url: body.url, token: body.token, expiresAt: body.expires_at };
  return agentConnection;
}

/**
 * JSON headers for the agent's deployment: a bearer token on MDA (refreshed
 * before it expires), otherwise the same headers as every other route.
 */
export async function agentHeaders(): Promise<Record<string, string>> {
  let agent = currentAgent();
  if (!agent?.url) return apiHeaders();
  if (agent.expiresAt - TOKEN_SLACK_SECONDS <= Date.now() / 1000) {
    agent = await refreshAgentToken();
  }
  return { "Content-Type": "application/json", Authorization: `Bearer ${agent.token}` };
}
