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
  if (summary) {
    const code = el("code", "tc-arg");
    code.textContent = summary.length > 90 ? summary.slice(0, 90) + "…" : summary;
    head.appendChild(code);
  }
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
  const emittedWidgets = new Set();  // push_widget tool_call ids already rendered
  const chips = {};                  // tool_call id -> chip (datasearch/query_sql/…)

  // Handle one streamed message object (from messages/partial|complete).
  const onMessage = (msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "ai") {
      const tcs = msg.tool_calls || [];
      for (const tc of tcs) {
        const id = tc.id || `${msg.id}:${tc.name || ""}`;
        const name = tc.name || "";
        const args = tc.args || {};
        if (name === "push_widget") {
          if (emittedWidgets.has(id)) continue;
          const w = args.widget || args;
          if (widgetLooksComplete(w)) {
            emittedWidgets.add(id);
            appendWidget(w);
            if (!answer) bubble.textContent = "Building your dashboard…";
          }
        } else {
          if (chips[id]) continue;
          const summary = name === "datasearch" || name === "query_sql"
            ? String(args.query || "")
            : JSON.stringify(args).slice(0, 120);
          const chip = toolChip(name, summary);
          chips[id] = chip;
          activity.appendChild(chip);
          log.scrollTop = log.scrollHeight;
        }
      }
      // Final answer text = an AI message with content and no tool calls.
      const text = contentToText(msg.content);
      if (text && tcs.length === 0) {
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
      onMessage(Array.isArray(data) ? data[0] : data);
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
  const lgUrlI = document.getElementById("sp-lgurl");
  const assistantI = document.getElementById("sp-assistant");
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
    cfg.lgUrl = lgUrlI.value.trim();
    cfg.assistantId = assistantI.value.trim();
    cfg.actions = collectActions();
    commit();
  };

  const populate = () => {
    nameI.value = cfg.name;
    accentI.value = /^#[0-9a-f]{6}$/i.test(cfg.accent) ? cfg.accent : "#0072BC";
    accentT.value = cfg.accent;
    logoI.value = cfg.logo;
    lgUrlI.value = cfg.lgUrl || "";
    assistantI.value = cfg.assistantId || "";
    actionsWrap.innerHTML = "";
    (cfg.actions || []).forEach((a) => actionsWrap.appendChild(buildActionRow(a)));
  };
  populate();

  gear.addEventListener("click", () => overlay.classList.toggle("hidden"));
  document.getElementById("sp-close").addEventListener("click", () => overlay.classList.add("hidden"));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.classList.add("hidden"); });

  nameI.addEventListener("input", syncFromInputs);
  logoI.addEventListener("input", syncFromInputs);
  lgUrlI.addEventListener("input", syncFromInputs);
  assistantI.addEventListener("input", syncFromInputs);
  accentI.addEventListener("input", () => { accentT.value = accentI.value; syncFromInputs(); });
  accentT.addEventListener("input", () => {
    if (/^#[0-9a-f]{6}$/i.test(accentT.value.trim())) accentI.value = accentT.value.trim();
    syncFromInputs();
  });

  actionsWrap.addEventListener("input", syncFromInputs);
  actionsWrap.addEventListener("click", (e) => {
    if (e.target.dataset && e.target.dataset.role === "del") {
      e.target.closest(".sp-action").remove();
      syncFromInputs();
    }
  });
  document.getElementById("sp-add").addEventListener("click", () => {
    actionsWrap.appendChild(buildActionRow({ label: "", question: "" }));
  });
  document.getElementById("sp-reset").addEventListener("click", () => {
    cfg = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
    populate();
    commit();
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
