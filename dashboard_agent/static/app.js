/* Dashboard Agent dashboard front-end.
 *
 * Pure functions (chartConfig, formatNumber, mdToHtml, trendColor) are exported
 * for Node tests. Everything else runs in the browser: it streams NDJSON from
 * /api/chat/stream and builds the dashboard progressively.
 */

const PALETTE = [
  "#0072BC", "#00A651", "#F5A623", "#D0021B",
  "#8E44AD", "#16A085", "#E67E22", "#2C3E50",
];

function formatNumber(n) {
  if (typeof n !== "number" || !isFinite(n)) return String(n);
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1).replace(/\.0$/, "") + "B";
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (abs >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
}

/* widget spec -> Chart.js config (bar/line/pie). Pure. Returns null otherwise. */
function chartConfig(widget) {
  if (!widget || !["bar", "line", "pie"].includes(widget.type)) return null;
  const series = widget.series || [];

  if (widget.type === "pie") {
    const points = series[0]?.points || [];
    return {
      type: "pie",
      data: {
        labels: points.map((p) => p.label),
        datasets: [{
          data: points.map((p) => p.value),
          backgroundColor: points.map((_, i) => PALETTE[i % PALETTE.length]),
          borderColor: "#fff", borderWidth: 2,
        }],
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right" } } },
    };
  }

  const labels = (series[0]?.points || []).map((p) => p.label);
  const datasets = series.map((s, i) => ({
    label: s.name,
    data: (s.points || []).map((p) => p.value),
    backgroundColor: widget.type === "line" ? "transparent" : PALETTE[i % PALETTE.length],
    borderColor: PALETTE[i % PALETTE.length],
    borderWidth: 2, fill: false, tension: 0.3, pointRadius: 3,
  }));

  return {
    type: widget.type,
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: series.length > 1 } },
      scales: {
        x: { title: { display: !!widget.x_label, text: widget.x_label || "" } },
        y: { title: { display: !!widget.y_label, text: widget.y_label || "" }, ticks: { callback: (v) => formatNumber(v) } },
      },
    },
  };
}

function trendColor(trend) {
  return { up: "#00A651", down: "#D0021B", flat: "#666" }[trend] || "#666";
}

function mdToHtml(md) {
  const escaped = String(md).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = escaped.split("\n");
  let html = "", inList = false;
  for (const line of lines) {
    const t = line.trim();
    if (/^[-*]\s+/.test(t)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += "<li>" + t.replace(/^[-*]\s+/, "") + "</li>";
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (t) html += "<p>" + t + "</p>";
    }
  }
  if (inList) html += "</ul>";
  return html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

/* ---------------- DOM (browser only) ---------------- */

function el(tag, cls, html) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

function renderWidget(widget) {
  const card = el("div", "widget widget-" + widget.type);
  if (widget.type === "kpi") {
    card.classList.add("kpi-card");
    card.appendChild(el("div", "kpi-title", widget.title));
    card.appendChild(el("div", "kpi-value", widget.value + (widget.unit ? `<span class="kpi-unit"> ${widget.unit}</span>` : "")));
    if (widget.delta) {
      const d = el("div", "kpi-delta", widget.delta);
      d.style.color = trendColor(widget.trend);
      card.appendChild(d);
    }
    if (widget.description) card.appendChild(el("div", "kpi-desc", widget.description));
    return card;
  }

  card.appendChild(el("div", "widget-title", widget.title));

  if (["bar", "line", "pie"].includes(widget.type)) {
    const wrap = el("div", "chart-wrap");
    const canvas = document.createElement("canvas");
    wrap.appendChild(canvas);
    card.appendChild(wrap);
    if (typeof Chart !== "undefined") {
      new Chart(canvas.getContext("2d"), chartConfig(widget));
    } else {
      card.appendChild(el("div", "chart-fallback", "[chart: " + widget.type + "]"));
    }
    return card;
  }

  if (widget.type === "table") {
    const table = el("table", "data-table");
    const thead = el("thead"), htr = el("tr");
    widget.columns.forEach((c) => htr.appendChild(el("th", null, c)));
    thead.appendChild(htr);
    table.appendChild(thead);
    const tbody = el("tbody");
    widget.rows.forEach((row) => {
      const tr = el("tr");
      row.forEach((cell) => tr.appendChild(el("td", null, cell)));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    card.appendChild(table);
    return card;
  }

  if (widget.type === "text") {
    const body = el("div", "text-body");
    body.innerHTML = mdToHtml(widget.content);
    card.appendChild(body);
    return card;
  }
  return card;
}

function ensureContainers() {
  const dash = document.getElementById("dashboard");
  let kpi = document.getElementById("kpi-row");
  let grid = document.getElementById("widget-grid");
  if (!kpi) { kpi = el("div", "kpi-row"); kpi.id = "kpi-row"; dash.appendChild(kpi); }
  if (!grid) { grid = el("div", "widget-grid"); grid.id = "widget-grid"; dash.appendChild(grid); }
  return { kpi, grid };
}

function clearDashboard() {
  document.getElementById("dashboard").innerHTML = "";
}

function revealDashboard() {
  document.getElementById("app").classList.add("has-dashboard");
  const btn = document.getElementById("export-pdf");
  if (btn) btn.disabled = false;
}

function appendWidget(w) {
  const { kpi, grid } = ensureContainers();
  const node = renderWidget(w);
  if (w.type === "kpi") {
    kpi.appendChild(node);
  } else {
    if (w.type === "text" || w.type === "table") node.classList.add("span-2");
    grid.appendChild(node);
  }
  revealDashboard();
}

const TOOL_META = {
  datasearch: { icon: "🔍", label: "Searched reports" },
  query_sql: { icon: "🗄️", label: "Ran SQL query" },
  write_todos: { icon: "📝", label: "Planned steps" },
  task: { icon: "🤖", label: "Delegated to subagent" },
  push_widget: { icon: "📊", label: "Added widget" },
};

function toolChip(name, summary) {
  const m = TOOL_META[name] || { icon: "🔧", label: name };
  const chip = el("div", "tool-chip");

  const head = el("div", "tc-head");
  head.appendChild(el("span", "tc-icon", m.icon));
  head.appendChild(el("span", "tc-label", m.label));
  // The arg (e.g. the SQL query / search terms) streams in, so keep it updatable.
  const code = el("code", "tc-arg");
  head.appendChild(code);
  chip._setArg = (s) => {
    s = String(s || "");
    code.textContent = s.length > 120 ? s.slice(0, 120) + "…" : s;
    code.style.display = s ? "" : "none";
  };
  chip._setArg(summary);
  const caret = el("span", "tc-caret", "▸");
  caret.style.visibility = "hidden";  // shown once a result arrives
  head.appendChild(caret);
  chip.appendChild(head);

  const details = el("pre", "tc-details");
  details.style.display = "none";
  chip.appendChild(details);

  let hasResult = false;
  head.addEventListener("click", () => {
    if (!hasResult) return;
    const open = details.style.display !== "none";
    details.style.display = open ? "none" : "block";
    caret.textContent = open ? "▸" : "▾";
  });

  // Called when the matching tool_result event arrives.
  chip._setResult = (content) => {
    hasResult = true;
    caret.style.visibility = "visible";
    head.classList.add("clickable");
    let text = content || "(empty result)";
    try { text = JSON.stringify(JSON.parse(content), null, 2); } catch (e) {}
    details.textContent = text;
  };
  return chip;
}

// ---- LangGraph deployment (Agent Server) connection ----
// Defaults come from config.js (window.LG); the gear panel can override url +
// assistant. assistantId may be a UUID (a specific variant) or the graph id
// "dashboard_agent" (uses that graph's default assistant).
function lgConfig() {
  const base = (typeof window !== "undefined" && window.LG) || {};
  const cfg = typeof loadConfig === "function" ? loadConfig() : {};
  return {
    url: String(cfg.lgUrl || base.url || "http://127.0.0.1:2024").replace(/\/+$/, ""),
    assistantId: cfg.assistantId || base.assistantId || "dashboard_agent",
    apiKey: base.apiKey || "",
  };
}

function lgHeaders() {
  const h = { "Content-Type": "application/json" };
  const k = lgConfig().apiKey;
  if (k) h["x-api-key"] = k;
  return h;
}

// Per-run runtime context (dashboard_agent.agent.Context) built from the gear
// panel. Only non-empty fields are sent, so an unset field falls back to the
// assistant/deployment default. The backend prefers `prompt` over `prompt_name`.
// Trace routing (ls_workspace/ls_project) also rides in context — LangGraph
// surfaces it into config.configurable for the graph factory, and sending it here
// (rather than in config) avoids the "can't set both context and configurable"
// error. ls_project defaults to the active assistant's name.
function runContext() {
  const cfg = typeof loadConfig === "function" ? loadConfig() : {};
  const ctx = {};
  if (cfg.promptMode === "inline") {
    if (cfg.systemPrompt) ctx.prompt = cfg.systemPrompt;
  } else if (cfg.promptName) {
    ctx.prompt_name = cfg.promptName;
  }
  if (cfg.dataPrompt) ctx.data_prompt = cfg.dataPrompt;
  if (cfg.dataGap) ctx.data_gap = cfg.dataGap;
  if (cfg.lsWorkspace) ctx.ls_workspace = cfg.lsWorkspace;
  const project = cfg.lsProject || activeAssistantName();
  if (project) ctx.ls_project = project;
  return ctx;
}

// Per-run `configurable` (LangGraph config). The graph factory reads these to
// route this run's LangSmith traces; only non-empty fields are sent. The project
// defaults to the active assistant's name when not explicitly set.
let ASSISTANTS = [];  // latest assistant list (kept in sync by the settings panel)

function activeAssistantName() {
  const cfg = typeof loadConfig === "function" ? loadConfig() : {};
  const id = cfg.assistantId || "dashboard_agent";
  const a = ASSISTANTS.find((x) => x.assistant_id === id);
  return (a && a.name) || id;  // graph default / unknown id → the id itself
}

// LangSmith tracing project names for the project combobox (served by webapp.py
// so the API key stays server-side).
async function lgListProjects(workspace) {
  try {
    const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    const r = await fetch(`${lgConfig().url}/projects${qs}`, { headers: lgHeaders() });
    if (!r.ok) return [];
    const d = await r.json();
    return Array.isArray(d.projects) ? d.projects : [];
  } catch (e) {
    return [];
  }
}

async function lgListWorkspaces() {
  try {
    const r = await fetch(`${lgConfig().url}/workspaces`, { headers: lgHeaders() });
    if (!r.ok) return [];
    const d = await r.json();
    return Array.isArray(d.workspaces) ? d.workspaces : [];
  } catch (e) {
    return [];
  }
}

async function lgListHubPrompts(workspace) {
  try {
    const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    const r = await fetch(`${lgConfig().url}/hub-prompts${qs}`, { headers: lgHeaders() });
    if (!r.ok) return [];
    const d = await r.json();
    return Array.isArray(d.prompts) ? d.prompts : [];
  } catch (e) {
    return [];
  }
}

async function lgCreateProject(name, workspace) {
  const r = await fetch(`${lgConfig().url}/projects`, {
    method: "POST",
    headers: lgHeaders(),
    body: JSON.stringify({ name, workspace: workspace || undefined }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.error || `HTTP ${r.status}`);
  }
  return r.json();
}

// ---- Assistants (server-side configuration instances of the graph) ----
const GRAPH_ID = "dashboard_agent";

async function lgListAssistants() {
  try {
    const r = await fetch(`${lgConfig().url}/assistants/search`, {
      method: "POST",
      headers: lgHeaders(),
      body: JSON.stringify({ graph_id: GRAPH_ID, limit: 100 }),
    });
    if (!r.ok) return [];
    const list = await r.json();
    return Array.isArray(list) ? list : [];
  } catch (e) {
    return [];
  }
}

async function lgCreateAssistant({ name, context, config, metadata }) {
  const r = await fetch(`${lgConfig().url}/assistants`, {
    method: "POST",
    headers: lgHeaders(),
    body: JSON.stringify({
      graph_id: GRAPH_ID,
      name,
      context: context || {},
      config: config || {},
      metadata: metadata || {},
    }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.error || d.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// Run the deployed assistant_setup graph and return its prepared payload
// (metadata + context + prompt_urls). The SPA then creates the assistant from it.
async function lgRunSetup(input) {
  const url = lgConfig().url;
  const t = await fetch(`${url}/threads`, { method: "POST", headers: lgHeaders(), body: "{}" });
  if (!t.ok) throw new Error("create thread failed: HTTP " + t.status);
  const tid = (await t.json()).thread_id;
  const r = await fetch(`${url}/threads/${tid}/runs/wait`, {
    method: "POST",
    headers: lgHeaders(),
    body: JSON.stringify({ assistant_id: "assistant_setup", input }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.error || d.detail || `HTTP ${r.status}`);
  }
  const out = await r.json();
  if (out && out.status === "error") throw new Error(out.error || "setup failed");
  return (out && out.result) || {};
}

// Update an existing assistant (e.g. persist branding into its metadata).
async function lgUpdateAssistant(id, body) {
  const r = await fetch(`${lgConfig().url}/assistants/${id}`, {
    method: "PATCH",
    headers: lgHeaders(),
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.error || d.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// A real assistant row (UUID) can be PATCHed; the "dashboard_agent" graph-default
// pseudo-entry cannot (it's not a stored assistant).
function isAssistantId(id) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id || "");
}

async function lgDeleteAssistant(id) {
  const r = await fetch(`${lgConfig().url}/assistants/${id}`, { method: "DELETE", headers: lgHeaders() });
  if (!r.ok && r.status !== 204) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.error || d.detail || `HTTP ${r.status}`);
  }
}

// One server-side thread per page load, so follow-up questions share memory.
let THREAD_ID = null;
async function ensureThread() {
  if (THREAD_ID) return THREAD_ID;
  const r = await fetch(`${lgConfig().url}/threads`, { method: "POST", headers: lgHeaders(), body: "{}" });
  if (!r.ok) throw new Error("create thread failed: HTTP " + r.status);
  THREAD_ID = (await r.json()).thread_id;
  return THREAD_ID;
}

function contentToText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((b) => (b && b.type === "text" ? b.text || "" : typeof b === "string" ? b : "")).join("");
  }
  return "";
}

// Only render a widget once its parsed args look complete (partials fill in).
function widgetLooksComplete(w) {
  if (!w || !w.type) return false;
  const pts = (s) => Array.isArray(s) && s.length && Array.isArray(s[0].points) && s[0].points.length &&
    s[0].points.every((p) => p && p.label !== undefined && p.value !== undefined);
  switch (w.type) {
    case "kpi": return !!w.title && w.value !== undefined && w.value !== "";
    case "bar": case "line": case "pie": return !!w.title && pts(w.series);
    case "table": return !!w.title && Array.isArray(w.columns) && w.columns.length && Array.isArray(w.rows) && w.rows.length;
    case "text": return !!w.title && !!w.content;
    default: return false;
  }
}

// ---- Threads (real Agent Server threads, listed in the sidebar) ----
const THREADS_KEY = "dashboardThreads";
let GREETING = "";

function loadThreads() {
  try { return JSON.parse(localStorage.getItem(THREADS_KEY) || "[]"); } catch (e) { return []; }
}
function saveThreads(list) {
  try { localStorage.setItem(THREADS_KEY, JSON.stringify(list.slice(0, 50))); } catch (e) {}
}
function registerThread(id, title) {
  if (!id) return;
  const list = loadThreads().filter((t) => t.id !== id);
  list.unshift({ id, title: (title || "Untitled").trim().slice(0, 60), ts: Date.now() });
  saveThreads(list);
  renderThreads();
}
function renderThreads() {
  const wrap = document.getElementById("thread-list");
  if (!wrap) return;
  wrap.innerHTML = "";
  loadThreads().forEach((t) => {
    const b = el("button", "thread" + (t.id === THREAD_ID ? " active" : ""));
    b.textContent = t.title;
    b.title = t.title;
    b.addEventListener("click", () => selectThread(t.id));
    wrap.appendChild(b);
  });
}
function resetChatLog(msg) {
  const log = document.getElementById("chat-log");
  if (!log) return;
  log.innerHTML = "";
  if (msg) addMessage("assistant", msg);
}
function newChat() {
  THREAD_ID = null;              // ensureThread() will mint a fresh server thread
  resetChatLog(GREETING);
  clearDashboard();
  document.getElementById("app").classList.remove("has-dashboard");
  renderThreads();
}
async function selectThread(id) {
  THREAD_ID = id;
  renderThreads();
  resetChatLog("");
  clearDashboard();
  document.getElementById("app").classList.remove("has-dashboard");
  const log = document.getElementById("chat-log");
  try {
    const r = await fetch(`${lgConfig().url}/threads/${id}/state`, { headers: lgHeaders() });
    const state = await r.json();
    const msgs = (state && state.values && state.values.messages) || [];
    for (const m of msgs) {
      const type = m.type || m.role;
      if (type === "human" || type === "user") addMessage("user", contentToText(m.content));
      else if ((type === "ai" || type === "assistant") && !(m.tool_calls && m.tool_calls.length)) {
        const t = contentToText(m.content);
        if (t.trim()) addMessage("assistant", t);
      }
    }
    if (!log.children.length) addMessage("assistant", "(no messages in this conversation)");
  } catch (e) {
    addMessage("assistant", "⚠️ Couldn't load this conversation: " + e.message);
  }
}

function renderFeedback(runId) {
  const log = document.getElementById("chat-log");
  const box = el("div", "feedback");
  const q = el("span", "fb-q", "Was this helpful?");
  const up = el("button", "fb-btn", "👍");
  const down = el("button", "fb-btn", "👎");
  const status = el("span", "fb-status", "");
  box.appendChild(q);
  box.appendChild(up);
  box.appendChild(down);
  box.appendChild(status);

  const commentWrap = el("div", "fb-comment");
  const input = el("input", "fb-input");
  input.placeholder = "Add a comment (optional), then Send…";
  const send = el("button", "fb-send", "Send");
  commentWrap.appendChild(input);
  commentWrap.appendChild(send);
  box.appendChild(commentWrap);

  let score = null;
  let feedbackId = null;

  const post = async (comment) => {
    if (score === null) return;
    status.textContent = "…";
    const body = { run_id: runId, score, comment: comment || "" };
    if (feedbackId) body.feedback_id = feedbackId;
    try {
      const r = await fetch(`${lgConfig().url}/feedback`, {
        method: "POST",
        headers: lgHeaders(),
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.ok) {
        if (d.feedback_id) feedbackId = d.feedback_id;
        status.textContent = comment ? "✓ Sent with comment" : "✓ Sent — add a comment?";
        status.className = "fb-status ok";
      } else {
        status.textContent = "⚠️ " + (d.error || "failed");
        status.className = "fb-status err";
      }
    } catch (e) {
      status.textContent = "⚠️ " + e.message;
      status.className = "fb-status err";
    }
  };

  const pick = (val, btn) => {
    score = val;
    up.classList.toggle("sel", btn === up);
    down.classList.toggle("sel", btn === down);
    commentWrap.classList.add("show");
    input.focus();
    post(input.value);  // submit the thumb immediately
  };
  up.addEventListener("click", () => pick(1, up));
  down.addEventListener("click", () => pick(0, down));
  send.addEventListener("click", () => post(input.value));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") post(input.value); });

  log.appendChild(box);
  log.scrollTop = log.scrollHeight;
}

function addMessage(role, text) {
  const log = document.getElementById("chat-log");
  const msg = el("div", "msg msg-" + role);
  if (text) msg.innerHTML = role === "assistant" ? mdToHtml(text) : String(text).replace(/</g, "&lt;");
  log.appendChild(msg);
  log.scrollTop = log.scrollHeight;
  return msg;
}

async function askStream(question) {
  if (!question || !question.trim()) return;
  // An explicit assistant is required — no graph default.
  if (!isAssistantId(loadConfig().assistantId)) {
    addMessage("assistant", "Pick or create an assistant in ⚙️ settings before sending.");
    const overlay = document.getElementById("settings-overlay");
    if (overlay) overlay.classList.remove("hidden");
    const sel = document.getElementById("sp-assistant-select");
    if (sel) sel.focus();
    return;
  }
  // Workspace is required — never silently route traces to a default workspace.
  if (!(loadConfig().lsWorkspace || "")) {
    addMessage("assistant", "Pick a Workspace in ⚙️ settings before sending — trace routing needs an explicit workspace.");
    const overlay = document.getElementById("settings-overlay");
    if (overlay) overlay.classList.remove("hidden");
    const ws = document.getElementById("sp-ls-workspace");
    if (ws) ws.focus();
    return;
  }
  // A system prompt is required — a Hub prompt (Prompt Hub mode) or inline text (Prompt mode).
  const _c = loadConfig();
  const _hasPrompt = _c.promptMode === "inline" ? (_c.systemPrompt || "").trim() : (_c.promptName || "");
  if (!_hasPrompt) {
    addMessage("assistant", "A system prompt is required — pick one from the Hub or switch to Prompt and write one (⚙️ → System prompt).");
    const overlay = document.getElementById("settings-overlay");
    if (overlay) overlay.classList.remove("hidden");
    return;
  }
  const log = document.getElementById("chat-log");
  addMessage("user", question);
  const activity = el("div", "activity");   // tool-call feed
  log.appendChild(activity);
  const bubble = addMessage("assistant", "");
  bubble.classList.add("cursor");
  bubble.textContent = "Working…";
  clearDashboard();

  const { url, assistantId } = lgConfig();
  let answer = "";
  let answerMid = null;
  let runId = null;
  let errorMsg = null;
  const chips = {};                  // tool_call id -> chip (datasearch/query_sql/…)
  // Widget args stream in via partials (complete events don't carry them). A
  // push_widget's args are final once the NEXT push_widget starts (tool-use blocks
  // stream sequentially), so we flush each widget when a later one appears, and
  // flush the last at stream end — progressive AND complete (no 1-point charts).
  const wOrder = [];                 // push_widget ids, in appearance order
  const wLatest = {};                // id -> latest widget spec
  const wFlushed = new Set();
  const flushWidget = (id) => {
    const w = wLatest[id];
    if (w && !wFlushed.has(id) && widgetLooksComplete(w)) {
      wFlushed.add(id);
      appendWidget(w);
      if (!answer) bubble.textContent = "Building your dashboard…";
    }
  };

  const onMessage = (msg, meta) => {
    if (!msg || typeof msg !== "object") return;
    // Only the MAIN agent's model node produces the chat answer. Tool-internal LLM
    // calls (e.g. the synthetic data source) also stream here with langgraph_node
    // "tools" — never render their output as the assistant's message.
    const node = meta && meta.langgraph_node;
    if (msg.type === "ai") {
      const tcs = msg.tool_calls || [];
      for (const tc of tcs) {
        const id = tc.id || `${msg.id}:${tc.name || ""}`;
        const name = tc.name || "";
        const args = tc.args || {};
        if (name === "push_widget") {
          wLatest[id] = args.widget || args;
          if (!wOrder.includes(id)) wOrder.push(id);
          // Flush every widget except the one still streaming (last in order).
          for (let i = 0; i < wOrder.length - 1; i++) flushWidget(wOrder[i]);
        } else {
          // Search/SQL chips: create once, then keep updating the arg as it streams.
          const summary = name === "datasearch" || name === "query_sql"
            ? String(args.query || "")
            : JSON.stringify(args).slice(0, 120);
          let chip = chips[id];
          if (!chip) { chip = toolChip(name, summary); chips[id] = chip; activity.appendChild(chip); }
          else if (chip._setArg) chip._setArg(summary);
          log.scrollTop = log.scrollHeight;
        }
      }
      // Final answer text = a MAIN-agent AI message with content and no tool calls.
      const text = contentToText(msg.content);
      if (text && tcs.length === 0 && (!node || node === "model")) {
        if (msg.id && msg.id !== answerMid) { answerMid = msg.id; }
        answer = text;  // partial content is cumulative per message id
        bubble.textContent = answer;
        log.scrollTop = log.scrollHeight;
      }
    } else if (msg.type === "tool" && msg.name !== "push_widget") {
      const chip = chips[msg.tool_call_id];
      if (chip && chip._setResult) chip._setResult(contentToText(msg.content));
    }
  };

  const onEvent = (event, dataStr) => {
    let data;
    try { data = JSON.parse(dataStr); } catch (e) { return; }
    if (event === "metadata") { if (data && data.run_id) runId = data.run_id; return; }
    if (event === "error") { errorMsg = (data && (data.error || data.message)) || "run error"; return; }
    if (event === "messages/partial" || event === "messages/complete") {
      onMessage(Array.isArray(data) ? data[0] : data, Array.isArray(data) ? data[1] : null);
    }
  };

  try {
    const tid = await ensureThread();
    if (!loadThreads().some((t) => t.id === tid)) registerThread(tid, question);
    else renderThreads();
    const res = await fetch(`${url}/threads/${tid}/runs/stream`, {
      method: "POST",
      headers: lgHeaders(),
      body: JSON.stringify({
        assistant_id: assistantId,
        input: { messages: [{ role: "user", content: question }] },
        stream_mode: "messages",
        ...(Object.keys(runContext()).length ? { context: runContext() } : {}),
      }),
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || data.detail || ("HTTP " + res.status));
    }
    // Parse the SSE stream (event:/data: blocks separated by a blank line).
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // Strip CR so CRLF/`\r\n\r\n` SSE framing normalizes to `\n` / `\n\n`.
      buf += dec.decode(value, { stream: true }).replace(/\r/g, "");
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let ev = "message";
        const dataLines = [];
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) ev = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
        }
        if (dataLines.length) onEvent(ev, dataLines.join("\n"));
      }
    }
    wOrder.forEach(flushWidget);  // flush the last (still-open) widget now the stream ended

    bubble.classList.remove("cursor");
    if (answer) bubble.innerHTML = mdToHtml(answer);
    else if (errorMsg) bubble.textContent = "⚠️ " + errorMsg;
    else if (!document.getElementById("dashboard").children.length) bubble.textContent = "(no response)";
    else bubble.textContent = "Dashboard ready.";
    if (runId) renderFeedback(runId);
  } catch (e) {
    bubble.classList.remove("cursor");
    bubble.textContent = "⚠️ Request failed: " + e.message;
  }
}

function exportPdf() {
  const dash = document.getElementById("dashboard");
  if (!dash || !dash.children.length) return;
  const PDF_WIDTH = 760;  // px; single column that fits A4 portrait
  const opt = {
    margin: 8,
    filename: "dashboard.pdf",
    image: { type: "jpeg", quality: 0.98 },
    html2canvas: {
      scale: 2,
      backgroundColor: "#f4f6f9",
      useCORS: true,
      windowWidth: PDF_WIDTH,
      onclone: (doc) => {
        // 1) Reveal/pop animations restart in the clone and get snapshotted
        //    mid-fade -> washed-out PDF. Force everything fully opaque.
        doc.querySelectorAll(".canvas-wrap, .widget, .kpi-row, .widget-grid").forEach((n) => {
          n.style.opacity = "1";
          n.style.animation = "none";
          n.style.transform = "none";
          n.style.transition = "none";
        });
        // 2) The on-screen dashboard is 2-column and too wide for A4 portrait,
        //    so charts run off the page. Reflow to a single narrow column.
        const dash = doc.getElementById("dashboard");
        if (dash) { dash.style.width = PDF_WIDTH + "px"; dash.style.maxWidth = PDF_WIDTH + "px"; }
        doc.querySelectorAll(".widget-grid").forEach((g) => { g.style.gridTemplateColumns = "1fr"; });
        doc.querySelectorAll(".span-2").forEach((n) => { n.style.gridColumn = "auto"; });
      },
    },
    jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
    pagebreak: { mode: ["css", "legacy"], avoid: ".widget" },
  };
  if (typeof html2pdf !== "undefined") {
    html2pdf().set(opt).from(dash).save();
  } else {
    window.print();
  }
}

/* ---------------- Appearance config (gear panel) ---------------- */

const CONFIG_KEY = "dashboardConfig";

const DEFAULT_CONFIG = {
  name: "Dashboard Agent — Humanitarian Insights",
  accent: "#0072BC",
  logo: "🌐",
  // Agent config (sent as per-run runtime context to the Agent Server).
  model: "",           // main agent LLM id; blank = deployment default
  promptName: "",      // system prompt name in Prompt Hub
  promptMode: "hub",   // "hub" (use promptName) | "inline" (use systemPrompt)
  systemPrompt: "",    // inline system prompt text; overrides promptName
  dataPrompt: "",      // inline synthetic data-source prompt text
  lsProject: "",       // LangSmith trace project (per-run, via config.configurable)
  lsWorkspace: "",     // LangSmith workspace id (needs cross-workspace key server-side)
  actions: [
    { label: "Donor: impact of aid in Egypt last quarter", question: "What is the impact of humanitarian aid in Egypt over the last quarter, according to the latest reports?" },
    { label: "Affected: resources for displaced families in Iran", question: "What are the available resources for displaced families in Iran as outlined in the latest situation report?" },
    { label: "NGO: water & sanitation needs in Canada", question: "Can you provide the latest data on water scarcity and sanitation needs in Canada from relevant assessments?" },
  ],
};

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function loadConfig() {
  try {
    return { ...DEFAULT_CONFIG, ...(JSON.parse(localStorage.getItem(CONFIG_KEY) || "{}")) };
  } catch (e) {
    return { ...DEFAULT_CONFIG };
  }
}

function saveConfig(cfg) {
  try { localStorage.setItem(CONFIG_KEY, JSON.stringify(cfg)); } catch (e) {}
}

// Darken (pct<0) or lighten (pct>0) a #rrggbb hex.
function shadeHex(hex, pct) {
  const m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const adj = (c) => Math.max(0, Math.min(255, Math.round(c + c * pct)));
  const r = adj((n >> 16) & 255), g = adj((n >> 8) & 255), b = adj(n & 255);
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

function renderPresets(actions) {
  const list = document.getElementById("preset-list");
  if (!list) return;
  list.innerHTML = "";
  (actions || []).forEach((a) => {
    if (!a || !a.question) return;
    const btn = el("button", "preset");
    const label = a.label || a.question;
    const i = label.indexOf(":");
    if (i > 0) {
      btn.innerHTML = "<b>" + escapeHtml(label.slice(0, i + 1)) + "</b> " + escapeHtml(label.slice(i + 1).trim());
    } else {
      btn.textContent = label;
    }
    btn.addEventListener("click", () => askStream(a.question));
    list.appendChild(btn);
  });
}

function setLogo(elId, logo) {
  const elm = document.getElementById(elId);
  if (!elm) return;
  const v = (logo || "").trim();
  if (/^(https?:|data:)/i.test(v)) {
    const img = document.createElement("img");
    img.src = v;
    img.alt = "logo";
    elm.innerHTML = "";
    elm.appendChild(img);
  } else {
    elm.textContent = v || "🌐";
  }
}

function applyConfig(cfg) {
  const root = document.documentElement.style;
  root.setProperty("--brand-blue", cfg.accent);
  root.setProperty("--brand-blue-dark", shadeHex(cfg.accent, -0.2));
  const nameEl = document.getElementById("brand-name");
  if (nameEl) nameEl.textContent = cfg.name;
  if (cfg.name) document.title = cfg.name;
  const sbName = document.getElementById("sb-name");
  if (sbName) sbName.textContent = (cfg.name || "").split("—")[0].trim() || "Dashboard Agent";
  setLogo("brand-logo", cfg.logo);
  setLogo("sb-logo", cfg.logo);
  renderPresets(cfg.actions);
}

function buildActionRow(action) {
  const row = el("div", "sp-action");
  const del = el("button", "sp-del");
  del.type = "button";
  del.textContent = "✕";
  del.title = "Remove";
  del.dataset.role = "del";
  const lab = document.createElement("input");
  lab.type = "text";
  lab.placeholder = "Button label (e.g. Donor: impact of aid)";
  lab.value = action.label || "";
  const q = document.createElement("input");
  q.type = "text";
  q.placeholder = "Question to ask";
  q.value = action.question || "";
  row.appendChild(del);
  row.appendChild(lab);
  row.appendChild(q);
  return row;
}

function setupSettings() {
  let cfg = loadConfig();
  applyConfig(cfg);

  const overlay = document.getElementById("settings-overlay");
  const gear = document.getElementById("settings-gear");
  if (!overlay || !gear) return;

  const nameI = document.getElementById("sp-name");
  const accentI = document.getElementById("sp-accent");
  const accentT = document.getElementById("sp-accent-text");
  const logoI = document.getElementById("sp-logo");
  const assistantI = document.getElementById("sp-assistant");
  const assistantSelect = document.getElementById("sp-assistant-select");
  const asstRefresh = document.getElementById("sp-asst-refresh");
  const asstNew = document.getElementById("sp-asst-new");
  const asstClone = document.getElementById("sp-asst-clone");
  const asstNewForm = document.getElementById("sp-asst-new-form");
  const asstOwner = document.getElementById("sp-asst-owner");
  const asstCustomer = document.getElementById("sp-asst-customer");
  const asstWebsite = document.getElementById("sp-asst-website");
  const asstHallu = document.getElementById("sp-asst-hallu");
  const asstCreate = document.getElementById("sp-asst-create");
  const asstCancel = document.getElementById("sp-asst-cancel");
  let assistantsCache = [];
  const dataGapI = document.getElementById("sp-data-gap");
  const promptNameI = document.getElementById("sp-prompt-name");
  const promptI = document.getElementById("sp-prompt");
  const promptModeEl = document.getElementById("sp-prompt-mode");
  const hubWrap = document.getElementById("sp-prompt-hub-wrap");
  const inlineWrap = document.getElementById("sp-prompt-inline-wrap");
  const dataPromptI = document.getElementById("sp-data-prompt");
  const lsProjectI = document.getElementById("sp-ls-project");
  const lsProjectMenu = document.getElementById("sp-ls-project-menu");
  const lsWorkspaceI = document.getElementById("sp-ls-workspace");
  const actionsWrap = document.getElementById("sp-actions");

  const collectActions = () =>
    [...actionsWrap.querySelectorAll(".sp-action")]
      .map((r) => {
        const [lab, q] = r.querySelectorAll("input");
        return { label: lab.value.trim(), question: q.value.trim() };
      })
      .filter((a) => a.question);

  const commit = () => { saveConfig(cfg); applyConfig(cfg); };

  const syncFromInputs = () => {
    cfg.name = nameI.value;
    cfg.accent = accentT.value.trim() || cfg.accent;
    cfg.logo = logoI.value;
    cfg.assistantId = assistantI.value.trim();
    cfg.promptName = promptNameI.value.trim();
    cfg.systemPrompt = promptI.value;
    cfg.dataPrompt = dataPromptI.value;
    cfg.dataGap = dataGapI.value.trim();
    cfg.lsProject = lsProjectI.value.trim();
    cfg.lsWorkspace = lsWorkspaceI.value.trim();
    cfg.actions = collectActions();
    commit();
  };

  const setPromptMode = (mode) => {
    cfg.promptMode = mode === "inline" ? "inline" : "hub";
    [...promptModeEl.querySelectorAll("button")].forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === cfg.promptMode));
    hubWrap.classList.toggle("hidden", cfg.promptMode !== "hub");
    inlineWrap.classList.toggle("hidden", cfg.promptMode !== "inline");
  };
  promptModeEl.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { setPromptMode(b.dataset.mode); syncFromInputs(); }));

  const populate = () => {
    nameI.value = cfg.name;
    accentI.value = /^#[0-9a-f]{6}$/i.test(cfg.accent) ? cfg.accent : "#0072BC";
    accentT.value = cfg.accent;
    logoI.value = cfg.logo;
    assistantI.value = cfg.assistantId || "";
    promptNameI.value = cfg.promptName || "";
    promptI.value = cfg.systemPrompt || "";
    dataPromptI.value = cfg.dataPrompt || "";
    dataGapI.value = cfg.dataGap || "";
    lsProjectI.value = cfg.lsProject || "";
    setPromptMode(cfg.promptMode || "hub");
    actionsWrap.innerHTML = "";
    (cfg.actions || []).forEach((a) => actionsWrap.appendChild(buildActionRow(a)));
  };
  populate();

  // Branding (display name / accent / logo / quick actions) is per-assistant — it
  // lives in the assistant's metadata. Apply the active assistant's branding to the
  // UI + panel inputs (falling back to DEFAULT_CONFIG for the graph default).
  const applyBrandingFor = (id) => {
    const a = assistantsCache.find((x) => x.assistant_id === id);
    const cfgWrap = document.getElementById("sp-config");
    if (!a) {
      // No assistant selected → hide + blank everything below the selector.
      if (cfgWrap) cfgWrap.classList.add("hidden");
      cfg.name = cfg.accent = cfg.logo = "";
      cfg.actions = [];
      cfg.promptName = cfg.systemPrompt = cfg.dataPrompt = cfg.dataGap = "";
      saveConfig(cfg);
      applyConfig({ name: "Dashboard Agent", accent: DEFAULT_CONFIG.accent, logo: "🤖", actions: [] });
      nameI.value = "";
      accentI.value = "#0072BC";
      accentT.value = "";
      logoI.value = "";
      actionsWrap.innerHTML = "";
      promptI.value = "";
      dataPromptI.value = "";
      dataGapI.value = "";
      promptNameI.value = "";
      setPromptMode("hub");
      return;
    }
    const m = a.metadata || {};
    const ctx = a.context || {};
    if (cfgWrap) cfgWrap.classList.remove("hidden");
    // Branding (metadata)
    cfg.name = m.display_name || DEFAULT_CONFIG.name;
    cfg.accent = m.accent || DEFAULT_CONFIG.accent;
    cfg.logo = m.logo || DEFAULT_CONFIG.logo;
    cfg.actions = Array.isArray(m.actions) && m.actions.length ? m.actions : DEFAULT_CONFIG.actions;
    // Agent config (context) — reflect what this assistant is configured with.
    cfg.promptName = ctx.prompt_name || "";
    cfg.systemPrompt = ctx.prompt || "";
    cfg.dataPrompt = ctx.data_prompt || "";
    cfg.dataGap = ctx.data_gap || "";
    saveConfig(cfg);
    applyConfig(cfg);
    nameI.value = cfg.name;
    accentI.value = /^#[0-9a-f]{6}$/i.test(cfg.accent) ? cfg.accent : "#0072BC";
    accentT.value = cfg.accent;
    logoI.value = cfg.logo;
    actionsWrap.innerHTML = "";
    (cfg.actions || []).forEach((x) => actionsWrap.appendChild(buildActionRow(x)));
    promptI.value = cfg.systemPrompt;
    dataPromptI.value = cfg.dataPrompt;
    dataGapI.value = cfg.dataGap;
    // Prompt-name <select>: ensure the assistant's prompt handle is selectable.
    if (cfg.promptName && ![...promptNameI.options].some((o) => o.value === cfg.promptName)) {
      const o = document.createElement("option");
      o.value = cfg.promptName;
      o.textContent = cfg.promptName;
      promptNameI.appendChild(o);
    }
    promptNameI.value = cfg.promptName;
    // Toggle to whichever prompt source this assistant uses.
    setPromptMode(cfg.systemPrompt ? "inline" : "hub");
  };

  // Persist branding edits back onto the active assistant's metadata (debounced).
  // No-op for the graph default (not a real assistant row) — it stays local.
  let brandingTimer = null;
  const scheduleBrandingSave = () => {
    const id = assistantI.value;
    if (!isAssistantId(id)) return;
    clearTimeout(brandingTimer);
    brandingTimer = setTimeout(async () => {
      const src = assistantsCache.find((a) => a.assistant_id === id);
      const meta = Object.assign({}, (src && src.metadata) || {}, {
        display_name: cfg.name, accent: cfg.accent, logo: cfg.logo, actions: cfg.actions,
      });
      try {
        const updated = await lgUpdateAssistant(id, { metadata: meta });
        const i = assistantsCache.findIndex((a) => a.assistant_id === id);
        if (i >= 0 && updated) { assistantsCache[i] = updated; ASSISTANTS = assistantsCache; }
      } catch (e) { /* non-fatal: branding is still applied locally */ }
    }, 600);
  };
  const onBrandingEdit = () => { syncFromInputs(); scheduleBrandingSave(); };

  // ---- Assistant picker: list / switch / new (fresh) / clone ----
  const renderAssistantOptions = (selectValue) => {
    const cur = selectValue != null ? selectValue : (assistantI.value || "");
    assistantSelect.innerHTML = "";
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "Select an assistant…";
    ph.disabled = true;
    assistantSelect.appendChild(ph);
    assistantsCache.forEach((a) => {
      const o = document.createElement("option");
      o.value = a.assistant_id;
      o.textContent = a.name || (a.metadata || {}).customer || "unnamed";
      assistantSelect.appendChild(o);
    });
    // Force an explicit real assistant — no graph-default option; unknown/stale
    // saved ids fall back to the (required) placeholder.
    assistantSelect.value = [...assistantSelect.options].some((o) => o.value === cur) ? cur : "";
  };

  const refreshAssistants = async (selectValue) => {
    assistantsCache = await lgListAssistants();
    ASSISTANTS = assistantsCache;
    renderAssistantOptions(selectValue);
    applyBrandingFor(assistantSelect.value);
    lsProjectI.placeholder = activeAssistantName() || "default project";
  };

  const selectAssistant = (id) => {
    assistantI.value = id;
    applyBrandingFor(id);
    syncFromInputs();
    lsProjectI.placeholder = activeAssistantName() || "default project";
    // Switching assistant → fresh conversation + empty dashboard on a new thread.
    THREAD_ID = null;
    resetChatLog("");
    clearDashboard();
    const app = document.getElementById("app");
    if (app) app.classList.remove("has-dashboard");
  };

  assistantSelect.addEventListener("change", () => selectAssistant(assistantSelect.value));
  if (asstRefresh) asstRefresh.addEventListener("click", () => refreshAssistants());

  const closeNewForm = () => {
    asstNewForm.classList.add("hidden");
    asstOwner.value = asstCustomer.value = asstWebsite.value = "";
    asstHallu.checked = false;
  };
  asstNew.addEventListener("click", () => {
    const willShow = asstNewForm.classList.contains("hidden");
    asstNewForm.classList.toggle("hidden");
    if (willShow) {
      // Prefill the owner from last time (cached on create).
      if (!asstOwner.value) { try { asstOwner.value = localStorage.getItem("lastOwner") || ""; } catch (e) {} }
      asstOwner.focus();
    }
  });
  asstCancel.addEventListener("click", closeNewForm);
  asstCreate.addEventListener("click", async () => {
    const owner = asstOwner.value.trim();
    const customer = asstCustomer.value.trim();
    const website = asstWebsite.value.trim();
    const hallucination = asstHallu.checked;
    const workspace = cfg.lsWorkspace || "";
    if (!customer) {
      alert("Customer is required — it's used as the assistant name.");
      asstCustomer.focus();
      return;
    }
    if (!workspace) {
      alert("Pick a Workspace first (top of the panel) — setup needs it.");
      return;
    }
    try { if (owner) localStorage.setItem("lastOwner", owner); } catch (e) {}
    asstCreate.disabled = true;
    asstCreate.textContent = "Setting up…";
    try {
      // The deployed setup agent fetches branding + generates quick actions (and
      // optionally pushes prompts); we then create the assistant from its payload.
      const result = await lgRunSetup({
        workspace, customer, owner, website, hallucination, push_prompts: true,
      });
      const a = await lgCreateAssistant({
        name: customer,
        context: result.context || { ls_workspace: workspace },
        metadata: result.metadata || { owner_name: owner, customer },
      });
      closeNewForm();
      await refreshAssistants(a.assistant_id);
      selectAssistant(a.assistant_id);
    } catch (e) {
      alert("Setup failed: " + e.message);
    } finally {
      asstCreate.disabled = false;
      asstCreate.textContent = "Create";
    }
  });

  refreshAssistants();

  gear.addEventListener("click", () => {
    const hidden = overlay.classList.toggle("hidden");
    if (!hidden) {  // panel just opened → pull latest lists
      refreshAssistants();
      refreshWorkspaces(cfg.lsWorkspace || "");
      refreshHubPrompts(promptNameI.value || "");
    }
  });
  document.getElementById("sp-close").addEventListener("click", () => overlay.classList.add("hidden"));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.classList.add("hidden"); });

  nameI.addEventListener("input", onBrandingEdit);
  logoI.addEventListener("input", onBrandingEdit);
  assistantI.addEventListener("input", () => {
    if ([...assistantSelect.options].some((o) => o.value === assistantI.value)) {
      assistantSelect.value = assistantI.value;
    }
    syncFromInputs();
  });
  dataGapI.addEventListener("input", syncFromInputs);
  promptNameI.addEventListener("change", syncFromInputs);

  // ---- System-prompt-name dropdown (Prompt Hub, scoped to the workspace) ----
  let hubPromptsCache = null;
  const renderPromptNameOptions = (val) => {
    const cur = val != null ? val : (promptNameI.value || "");
    promptNameI.innerHTML = "";
    const def = document.createElement("option");
    def.value = "";
    def.textContent = "None — write a system prompt below";
    promptNameI.appendChild(def);
    (hubPromptsCache || []).forEach((name) => {
      const o = document.createElement("option");
      o.value = name;
      o.textContent = name;
      promptNameI.appendChild(o);
    });
    if (cur && ![...promptNameI.options].some((o) => o.value === cur)) {
      const o = document.createElement("option");
      o.value = cur;
      o.textContent = cur;
      promptNameI.appendChild(o);
    }
    promptNameI.value = cur;
  };
  const refreshHubPrompts = async (val) => {
    // Prompts are workspace-scoped — only fetch once a workspace is chosen, else
    // the server falls back to the key's default tenant (leaking its prompts).
    hubPromptsCache = cfg.lsWorkspace ? await lgListHubPrompts(cfg.lsWorkspace) : [];
    renderPromptNameOptions(val);
  };
  refreshHubPrompts(cfg.promptName || "");
  promptI.addEventListener("input", syncFromInputs);
  dataPromptI.addEventListener("input", syncFromInputs);
  // ---- Workspace dropdown (populated from /workspaces; org-scoped key needed) ----
  let workspacesCache = [];
  const renderWorkspaceOptions = (val) => {
    let cur = val != null ? val : (lsWorkspaceI.value || "");
    lsWorkspaceI.innerHTML = "";
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "Select a workspace…";
    ph.disabled = true;
    lsWorkspaceI.appendChild(ph);
    (workspacesCache || []).forEach((w) => {
      const o = document.createElement("option");
      o.value = w.id;
      o.textContent = w.name || w.id;
      lsWorkspaceI.appendChild(o);
    });
    if (cur && ![...lsWorkspaceI.options].some((o) => o.value === cur)) {
      // Saved id not in this list (e.g. different org / stale) — keep it selectable.
      const o = document.createElement("option");
      o.value = cur;
      o.textContent = cur;
      lsWorkspaceI.appendChild(o);
    }
    lsWorkspaceI.value = cur;  // "" → the (required) placeholder shows; no auto-default
  };
  const refreshWorkspaces = async (val) => {
    workspacesCache = await lgListWorkspaces();
    renderWorkspaceOptions(val);
  };
  lsWorkspaceI.addEventListener("change", () => {
    syncFromInputs();
    projectsCache = null;  // projects are per-workspace → reload for the new one
    hideProjectMenu();
    refreshHubPrompts(promptNameI.value || "");  // Hub prompts are per-workspace too
  });
  refreshWorkspaces(cfg.lsWorkspace || "");

  // ---- Project combobox: filter tracing projects; no match → "Add project" ----
  let projectsCache = null;  // null = not loaded yet
  const hideProjectMenu = () => lsProjectMenu.classList.add("hidden");
  const chooseProject = (name) => {
    lsProjectI.value = name;
    hideProjectMenu();
    syncFromInputs();
  };
  const renderProjectMenu = () => {
    const q = lsProjectI.value.trim().toLowerCase();
    lsProjectMenu.innerHTML = "";
    if (projectsCache === null) {
      const d = el("div", "sp-combo-empty");
      d.textContent = "Loading…";
      lsProjectMenu.appendChild(d);
    } else {
      const matches = q ? projectsCache.filter((n) => n.toLowerCase().includes(q)) : projectsCache;
      if (matches.length) {
        matches.slice(0, 50).forEach((n) => {
          const item = el("div", "sp-combo-item");
          item.textContent = n;
          item.addEventListener("mousedown", (e) => { e.preventDefault(); chooseProject(n); });
          lsProjectMenu.appendChild(item);
        });
      } else if (lsProjectI.value.trim()) {
        const item = el("div", "sp-combo-item add");
        item.textContent = `＋ Add project: ${lsProjectI.value.trim()}`;
        item.addEventListener("mousedown", async (e) => {
          e.preventDefault();
          const name = lsProjectI.value.trim();
          item.textContent = `Creating “${name}”…`;
          try {
            await lgCreateProject(name, cfg.lsWorkspace || "");
            if (projectsCache && !projectsCache.includes(name)) projectsCache.push(name);
          } catch (err) {
            // Non-fatal: LangSmith also auto-creates on first trace.
            alert("Create project failed (it'll still be created on first run): " + err.message);
          }
          chooseProject(name);
        });
        lsProjectMenu.appendChild(item);
      } else {
        const d = el("div", "sp-combo-empty");
        d.textContent = "No projects yet";
        lsProjectMenu.appendChild(d);
      }
    }
    lsProjectMenu.classList.remove("hidden");
  };
  const openProjectMenu = async () => {
    renderProjectMenu();
    if (projectsCache === null) {
      projectsCache = await lgListProjects(cfg.lsWorkspace || "");
      renderProjectMenu();
    }
  };
  lsProjectI.addEventListener("focus", openProjectMenu);
  lsProjectI.addEventListener("input", () => { renderProjectMenu(); syncFromInputs(); });
  lsProjectI.addEventListener("blur", () => setTimeout(hideProjectMenu, 150));
  accentI.addEventListener("input", () => { accentT.value = accentI.value; onBrandingEdit(); });
  accentT.addEventListener("input", () => {
    if (/^#[0-9a-f]{6}$/i.test(accentT.value.trim())) accentI.value = accentT.value.trim();
    onBrandingEdit();
  });

  actionsWrap.addEventListener("input", onBrandingEdit);
  actionsWrap.addEventListener("click", (e) => {
    if (e.target.dataset && e.target.dataset.role === "del") {
      e.target.closest(".sp-action").remove();
      onBrandingEdit();
    }
  });
  document.getElementById("sp-add").addEventListener("click", () => {
    actionsWrap.appendChild(buildActionRow({ label: "", question: "" }));
  });
  document.getElementById("sp-asst-delete").addEventListener("click", async () => {
    const id = assistantI.value;
    if (!isAssistantId(id)) {
      alert("Select an assistant to delete.");
      return;
    }
    const a = assistantsCache.find((x) => x.assistant_id === id);
    const label = (a && ((a.metadata || {}).display_name || a.name)) || id;
    if (!confirm(`Delete assistant "${label}"? This cannot be undone.`)) return;
    try {
      await lgDeleteAssistant(id);
      assistantI.value = "";
      await refreshAssistants("");
      applyBrandingFor("");
    } catch (e) {
      alert("Delete failed: " + e.message);
    }
  });
}

function initUI() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = "";
    askStream(q);
  });
  const exportBtn = document.getElementById("export-pdf");
  if (exportBtn) exportBtn.addEventListener("click", exportPdf);

  // Dark theme for Chart.js (axis/grid/legend colors).
  if (typeof Chart !== "undefined") {
    Chart.defaults.color = "#8a8a93";
    Chart.defaults.borderColor = "#222226";
  }

  // Threads sidebar.
  const first = document.querySelector("#chat-log .msg");
  GREETING = first ? first.textContent : "";
  const nc = document.getElementById("new-chat");
  if (nc) nc.addEventListener("click", newChat);
  renderThreads();

  setupSettings();  // applies saved config + renders the quick-action presets
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", initUI);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { chartConfig, formatNumber, mdToHtml, trendColor, PALETTE };
}
