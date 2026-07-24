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

// One thread per page load: a refresh starts a fresh conversation; questions
// asked within the same load share memory (follow-ups work). Guarded so the
// module can be required in Node (tests) without browser globals.
const THREAD_ID =
  typeof window === "undefined"
    ? "node"
    : "demo-" +
      ((window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(performance.now()).replace(".", ""));

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
      const r = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
  bubble.textContent = "Searching reports…";
  clearDashboard();

  let answer = "";
  let currentMid = null;
  let runId = null;
  let errorMsg = null;
  const chipsById = {};

  const handle = (evt) => {
    if (evt.type === "run_id") {
      runId = evt.run_id;
    } else if (evt.type === "tool") {
      const chip = toolChip(evt.name, evt.summary);
      if (evt.id) chipsById[evt.id] = chip;
      activity.appendChild(chip);
      log.scrollTop = log.scrollHeight;
    } else if (evt.type === "tool_result") {
      const chip = chipsById[evt.id];
      if (chip && chip._setResult) chip._setResult(evt.content);
    } else if (evt.type === "widget") {
      appendWidget(evt.widget);
      if (!answer) bubble.textContent = "Building your dashboard…";
    } else if (evt.type === "answer_reset") {
      // Server dropped a preamble ("I'll build…") now that tools have started.
      answer = ""; currentMid = null;
      bubble.textContent = "Building your dashboard…";
    } else if (evt.type === "answer_delta") {
      if (evt.mid && evt.mid !== currentMid) { currentMid = evt.mid; answer = ""; }
      if (answer === "") bubble.textContent = "";  // clear placeholder
      answer += evt.text;
      bubble.textContent = answer;
      log.scrollTop = log.scrollHeight;
    } else if (evt.type === "error") {
      errorMsg = evt.error;  // don't clobber a streamed answer; decide at the end
    }
  };

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, thread_id: THREAD_ID }),
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || ("HTTP " + res.status));
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) { try { handle(JSON.parse(line)); } catch (e) {} }
      }
    }
    if (buf.trim()) { try { handle(JSON.parse(buf.trim())); } catch (e) {} }

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

function applyConfig(cfg) {
  const root = document.documentElement.style;
  root.setProperty("--brand-blue", cfg.accent);
  root.setProperty("--brand-blue-dark", shadeHex(cfg.accent, -0.2));
  const nameEl = document.getElementById("brand-name");
  if (nameEl) nameEl.textContent = cfg.name;
  if (cfg.name) document.title = cfg.name;
  const logoEl = document.getElementById("brand-logo");
  if (logoEl) {
    const logo = (cfg.logo || "").trim();
    if (/^(https?:|data:)/i.test(logo)) {
      const img = document.createElement("img");
      img.src = logo;
      img.alt = "logo";
      logoEl.innerHTML = "";
      logoEl.appendChild(img);
    } else {
      logoEl.textContent = logo || "🌐";
    }
  }
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
    cfg.actions = collectActions();
    commit();
  };

  const populate = () => {
    nameI.value = cfg.name;
    accentI.value = /^#[0-9a-f]{6}$/i.test(cfg.accent) ? cfg.accent : "#0072BC";
    accentT.value = cfg.accent;
    logoI.value = cfg.logo;
    actionsWrap.innerHTML = "";
    (cfg.actions || []).forEach((a) => actionsWrap.appendChild(buildActionRow(a)));
  };
  populate();

  gear.addEventListener("click", () => overlay.classList.toggle("hidden"));
  document.getElementById("sp-close").addEventListener("click", () => overlay.classList.add("hidden"));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.classList.add("hidden"); });

  nameI.addEventListener("input", syncFromInputs);
  logoI.addEventListener("input", syncFromInputs);
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

  setupSettings();  // applies saved config + renders the quick-action presets
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", initUI);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { chartConfig, formatNumber, mdToHtml, trendColor, PALETTE };
}
