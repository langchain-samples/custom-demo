/**
 * Connection config for the LangGraph Agent Server.
 *
 * Base URL precedence: localStorage "lgUrl" → `window.LG.url` (config.js) →
 * build-time `VITE_LG_URL` (set in the hosting env, e.g. Vercel) →
 * http://127.0.0.1:2024. The assistant id is a UUID (a specific assistant
 * variant) or the graph id "dashboard_agent" (the graph's default assistant).
 */

/** The registered graph id on the Agent Server. */
export const GRAPH_ID = "dashboard_agent";

/** Build-time overrides (baked in by Vite from VITE_* env vars at build). */
const ENV_URL = (import.meta.env.VITE_LG_URL as string | undefined) || "";
const ENV_API_KEY = (import.meta.env.VITE_LG_API_KEY as string | undefined) || "";

/** Default Agent Server base URL (build-time env, else local dev). */
export const DEFAULT_API_BASE = ENV_URL || "http://127.0.0.1:2024";

/** Shape of the optional `window.LG` base config injected via config.js. */
export interface LGGlobal {
  url?: string;
  assistantId?: string;
  apiKey?: string;
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

/** Persist a base URL override to localStorage ("lgUrl"); blank clears it. */
export function setApiBase(url: string): void {
  writeLS(LG_URL_KEY, (url || "").trim().replace(/\/+$/, ""));
}

/** Resolved assistant id (UUID or the "dashboard_agent" graph default). */
export function getAssistantId(): string {
  return readLS(LG_ASSISTANT_KEY) || windowLG().assistantId || GRAPH_ID;
}

/** Persist the active assistant id to localStorage. */
export function setAssistantId(id: string): void {
  writeLS(LG_ASSISTANT_KEY, (id || "").trim());
}

/** Optional API key for a secured/cloud deployment (sent as x-api-key). */
export function getApiKey(): string {
  return readLS(LG_API_KEY_KEY) || windowLG().apiKey || ENV_API_KEY;
}

/** Persist an API key override to localStorage. */
export function setApiKey(key: string): void {
  writeLS(LG_API_KEY_KEY, (key || "").trim());
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
