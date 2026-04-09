// LLM Trading Lab — Bloomberg-style terminal dashboard renderer
// Pure-JS canvas charts: master equity chart (interactive, zoomable, hover crosshair),
// per-row sparklines, and per-model mini charts. No external libraries.

const DATA_URL = "../data/dashboard.json";
const REFRESH_MS = 5 * 60 * 1000;

const MODEL_COLORS = {
  claude:   "#ff7733",
  gpt:      "#00d4aa",
  gemini:   "#ffd23f",
  grok:     "#b478ff",
  deepseek: "#ff5599",
};
const SPY_COLOR = "#6b7585";

const MODEL_ORDER = ["claude", "gpt", "gemini", "grok", "deepseek"];

// Global UI state
const state = {
  timeframe: "ALL",
  mutedSeries: new Set(),
  data: null,
};

// ===== Formatters =====
function fmtPct(x, signed = true) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  const v = (x * 100).toFixed(2);
  return (signed && x >= 0 ? "+" : "") + v + "%";
}
function fmtNum(x, digits = 2) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  return Number(x).toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}
function fmtMoney(x) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  return "$" + fmtNum(x, 2);
}
function colorClass(x) {
  if (x === null || x === undefined || isNaN(x)) return "neutral";
  if (x > 0) return "pos";
  if (x < 0) return "neg";
  return "neutral";
}

// ===== Data loading =====
async function loadData() {
  try {
    const r = await fetch(DATA_URL + "?t=" + Date.now());
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } catch (e) {
    console.error("Failed to load dashboard data:", e);
    const sys = document.getElementById("system");
    sys.textContent = "OFFLINE";
    sys.className = "value offline";
    return null;
  }
}

// ===== Series builders =====
// Build normalized series (rebased to 0 at first point of the *filtered* window).
// Each series: { key, color, dashed, points: [{date, value}] }
function buildSeries(data) {
  const curves = data.equity_curves || {};
  const series = [];
  MODEL_ORDER.forEach(key => {
    const points = curves[key];
    if (!points || points.length < 1) return;
    series.push({
      key,
      label: key.toUpperCase(),
      color: MODEL_COLORS[key],
      dashed: false,
      raw: points.map(p => ({ date: p.date, raw: p.value })),
    });
  });

  // Synthesize SPY series from any model's benchmark prices
  let spyPoints = null;
  for (const key of MODEL_ORDER) {
    const c = curves[key];
    if (c && c.length && c[0].benchmark != null) {
      spyPoints = c.map(p => ({ date: p.date, raw: p.benchmark }));
      break;
    }
  }
  if (spyPoints) {
    series.push({
      key: "spy",
      label: "SPY",
      color: SPY_COLOR,
      dashed: true,
      raw: spyPoints,
    });
  }
  return series;
}

// Filter raw series by timeframe and rebase to 0 at window start
function filterAndRebase(series, timeframe) {
  const tfMap = { "1W": 5, "1M": 21, "3M": 63 };
  return series.map(s => {
    let raw = s.raw;
    if (timeframe !== "ALL") {
      const n = tfMap[timeframe] || raw.length;
      raw = raw.slice(-n);
    }
    if (!raw.length) return { ...s, points: [] };
    const base = raw[0].raw;
    const points = base
      ? raw.map(p => ({ date: p.date, value: (p.raw / base) - 1 }))
      : raw.map(p => ({ date: p.date, value: 0 }));
    return { ...s, points };
  });
}

// ===== Generic line chart engine =====
class LineChart {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.opts = Object.assign({
      pad: { l: 56, r: 16, t: 12, b: 28 },
      showAxes: true,
      showGrid: true,
      interactive: false,
      tooltipEl: null,
      lineWidth: 1.8,
      smallMode: false,
    }, opts);
    this.series = [];
    this.hoverIdx = null;
    if (this.opts.interactive) {
      canvas.addEventListener("mousemove", e => this._onMove(e));
      canvas.addEventListener("mouseleave", () => {
        this.hoverIdx = null;
        if (this.opts.tooltipEl) this.opts.tooltipEl.style.display = "none";
        this.draw();
      });
    }
  }

  setSeries(series) {
    this.series = series;
    this.hoverIdx = null;
    this.draw();
  }

  _xRange() {
    // Use the longest series for x axis count
    const lens = this.series.filter(s => !s.muted).map(s => s.points.length);
    return Math.max(0, ...lens);
  }

  _yRange() {
    let yMin = Infinity, yMax = -Infinity;
    this.series.forEach(s => {
      if (s.muted) return;
      s.points.forEach(p => {
        if (p.value < yMin) yMin = p.value;
        if (p.value > yMax) yMax = p.value;
      });
    });
    if (yMin === Infinity) { yMin = -0.01; yMax = 0.01; }
    if (yMin === yMax) { yMin -= 0.01; yMax += 0.01; }
    const pad = (yMax - yMin) * 0.12;
    return { yMin: yMin - pad, yMax: yMax + pad };
  }

  _onMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const { l, r } = this.opts.pad;
    const w = rect.width - l - r;
    const maxLen = this._xRange();
    if (maxLen < 2) return;
    const rel = (x - l) / w;
    if (rel < 0 || rel > 1) {
      this.hoverIdx = null;
      if (this.opts.tooltipEl) this.opts.tooltipEl.style.display = "none";
      this.draw();
      return;
    }
    this.hoverIdx = Math.round(rel * (maxLen - 1));
    this.hoverIdx = Math.max(0, Math.min(maxLen - 1, this.hoverIdx));
    this.draw();
    this._renderTooltip(e);
  }

  _renderTooltip(e) {
    const tt = this.opts.tooltipEl;
    if (!tt) return;
    const idx = this.hoverIdx;
    if (idx == null) { tt.style.display = "none"; return; }
    // Pick a date from any series that has this index
    let date = "—";
    for (const s of this.series) {
      if (s.points[idx]) { date = s.points[idx].date; break; }
    }
    let html = `<div class="tt-date">${date}</div>`;
    this.series.forEach(s => {
      if (s.muted) return;
      const p = s.points[idx];
      if (!p) return;
      const cls = p.value >= 0 ? "pos" : "neg";
      const sign = p.value >= 0 ? "+" : "";
      const valStr = sign + (p.value * 100).toFixed(2) + "%";
      html += `
        <div class="tt-row">
          <span class="tt-label"><span class="tt-swatch" style="background:${s.color}"></span>${s.label}</span>
          <span class="tt-val ${cls}" style="color:${p.value >= 0 ? '#00d488' : '#ff3355'}">${valStr}</span>
        </div>`;
    });
    tt.innerHTML = html;
    tt.style.display = "block";
    // Position: clamp to canvas
    const rect = this.canvas.getBoundingClientRect();
    const wrapRect = this.canvas.parentElement.getBoundingClientRect();
    const ttW = tt.offsetWidth;
    const ttH = tt.offsetHeight;
    let left = e.clientX - wrapRect.left + 14;
    let top = e.clientY - wrapRect.top + 14;
    if (left + ttW > wrapRect.width - 4) left = e.clientX - wrapRect.left - ttW - 14;
    if (top + ttH > wrapRect.height - 4) top = wrapRect.height - ttH - 4;
    tt.style.left = left + "px";
    tt.style.top = top + "px";
  }

  draw() {
    const canvas = this.canvas;
    const ctx = this.ctx;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 800;
    const cssH = canvas.clientHeight || 280;
    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const { l: padL, r: padR, t: padT, b: padB } = this.opts.pad;
    const w = cssW - padL - padR;
    const h = cssH - padT - padB;

    const visible = this.series.filter(s => !s.muted && s.points.length >= 1);
    if (!visible.length) {
      ctx.fillStyle = "#5f6b80";
      ctx.font = "10px JetBrains Mono, monospace";
      ctx.fillText("// no data", padL, padT + h / 2);
      return;
    }

    const { yMin, yMax } = this._yRange();
    const maxLen = this._xRange();

    // Grid
    ctx.strokeStyle = "#1a2235";
    ctx.lineWidth = 1;
    ctx.font = (this.opts.smallMode ? "9px" : "10px") + " JetBrains Mono, monospace";
    ctx.fillStyle = "#5f6b80";
    const gridLines = this.opts.smallMode ? 3 : 5;
    for (let i = 0; i <= gridLines - 1; i++) {
      const y = padT + (h * i) / (gridLines - 1);
      const v = yMax - ((yMax - yMin) * i) / (gridLines - 1);
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + w, y);
      ctx.stroke();
      const label = (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
      ctx.fillText(label.padStart(7), 4, y + 3);
    }

    // Zero line — emphasized
    if (yMin < 0 && yMax > 0) {
      const yz = padT + h * (yMax / (yMax - yMin));
      ctx.strokeStyle = "#243049";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(padL, yz);
      ctx.lineTo(padL + w, yz);
      ctx.stroke();
    }

    // Plot series
    visible.forEach(s => {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = this.opts.lineWidth;
      if (s.dashed) ctx.setLineDash([4, 4]); else ctx.setLineDash([]);
      ctx.beginPath();
      const pts = s.points;
      pts.forEach((p, i) => {
        const x = padL + (w * i) / (maxLen - 1 || 1);
        const y = padT + h * (1 - (p.value - yMin) / (yMax - yMin));
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    });

    // Date axis (first + last)
    if (this.opts.showAxes) {
      ctx.fillStyle = "#5f6b80";
      const ref = visible[0].points;
      if (ref.length) {
        ctx.fillText(ref[0].date, padL, cssH - 8);
        const last = ref[ref.length - 1].date;
        const m = ctx.measureText(last);
        ctx.fillText(last, padL + w - m.width, cssH - 8);
      }
    }

    // Crosshair + hover dots
    if (this.hoverIdx != null && maxLen > 1) {
      const x = padL + (w * this.hoverIdx) / (maxLen - 1);
      ctx.strokeStyle = "#2b8aff";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + h);
      ctx.stroke();
      ctx.setLineDash([]);
      // Dots
      visible.forEach(s => {
        const p = s.points[this.hoverIdx];
        if (!p) return;
        const y = padT + h * (1 - (p.value - yMin) / (yMax - yMin));
        ctx.fillStyle = s.color;
        ctx.beginPath();
        ctx.arc(x, y, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "#05070b";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      });
    }
  }
}

// ===== Sparkline (lightweight, non-interactive) =====
function drawSparkline(canvas, points) {
  if (!canvas || !points || points.length < 1) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 100;
  const cssH = canvas.clientHeight || 26;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const base = points[0].raw;
  if (!base) return;
  const norm = points.map(p => (p.raw / base) - 1);
  let yMin = Math.min(...norm);
  let yMax = Math.max(...norm);
  if (yMin === yMax) { yMin -= 0.001; yMax += 0.001; }
  const pad = (yMax - yMin) * 0.15;
  yMin -= pad; yMax += pad;

  const last = norm[norm.length - 1];
  const color = last >= 0 ? "#00d488" : "#ff3355";
  const fill = last >= 0 ? "rgba(0,212,136,0.18)" : "rgba(255,51,85,0.18)";

  // Fill area
  ctx.beginPath();
  norm.forEach((v, i) => {
    const x = (cssW * i) / (norm.length - 1 || 1);
    const y = cssH * (1 - (v - yMin) / (yMax - yMin));
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.lineTo(cssW, cssH);
  ctx.lineTo(0, cssH);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();

  // Line
  ctx.beginPath();
  norm.forEach((v, i) => {
    const x = (cssW * i) / (norm.length - 1 || 1);
    const y = cssH * (1 - (v - yMin) / (yMax - yMin));
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.stroke();

  // Endpoint dot
  const lx = cssW;
  const ly = cssH * (1 - (last - yMin) / (yMax - yMin));
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(lx - 1, ly, 1.8, 0, Math.PI * 2);
  ctx.fill();
}

// ===== Renderers =====
function renderStatus(d) {
  document.getElementById("phase").textContent = d.phase || "—";
  document.getElementById("mode").textContent = (d.mode || "—").toUpperCase();
  document.getElementById("day").textContent =
    d.experiment_day ? `${d.experiment_day} / ${d.experiment_total_days}` : "—";

  const now = new Date();
  const dow = now.getUTCDay();
  const hour = now.getUTCHours();
  const isWeekday = dow >= 1 && dow <= 5;
  const inHours = hour >= 13 && hour < 21;
  const open = isWeekday && inHours;
  const m = document.getElementById("market");
  m.textContent = open ? "OPEN" : "CLOSED";
  m.className = open ? "value open" : "value closed";

  document.getElementById("lastrun").textContent =
    d.generated_at ? d.generated_at.replace("T", " ").slice(0, 19) : "—";
  document.getElementById("generated").textContent =
    "GENERATED " + (d.generated_at || "—");
}

function renderLeaderboard(d) {
  const tbody = document.getElementById("leaderboard-body");
  tbody.innerHTML = "";
  const lb = d.leaderboard || [];
  document.getElementById("lb-meta").textContent = `${lb.length} MODELS`;

  const curves = d.equity_curves || {};

  lb.forEach((row, i) => {
    const tr = document.createElement("tr");
    if (i === 0) tr.className = "rank-1";
    const cfg = (d.models || {})[row.model_key] || {};
    const ret = row.cumulative_return;
    const alpha = row.alpha_vs_spy;
    const dd = row.max_drawdown;
    tr.innerHTML = `
      <td>${row.rank}</td>
      <td><span class="model-name">${row.model_key.toUpperCase()}</span></td>
      <td><span class="model-version">${cfg.model || "—"}</span></td>
      <td class="num ${colorClass(ret)}">${fmtPct(ret)}</td>
      <td class="spark-cell"><canvas data-spark="${row.model_key}"></canvas></td>
      <td class="num">${row.sharpe_30d != null ? fmtNum(row.sharpe_30d) : "—"}</td>
      <td class="num ${dd != null && dd < 0 ? "neg" : "neutral"}">${dd != null ? fmtPct(dd) : "—"}</td>
      <td class="num ${colorClass(alpha)}">${alpha != null ? fmtPct(alpha) : "—"}</td>
      <td class="num">${row.num_positions ?? "—"}</td>
      <td class="num">${row.current_cash_pct != null ? fmtPct(row.current_cash_pct, false) : "—"}</td>
      <td class="num">${fmtMoney(row.current_value)}</td>
    `;
    tbody.appendChild(tr);
  });

  // Draw sparklines after rows are in DOM
  requestAnimationFrame(() => {
    document.querySelectorAll("[data-spark]").forEach(canvas => {
      const key = canvas.getAttribute("data-spark");
      const points = (curves[key] || []).map(p => ({ date: p.date, raw: p.value }));
      drawSparkline(canvas, points);
    });
  });
}

function renderPortfolios(d) {
  const grid = document.getElementById("portfolio-grid");
  grid.innerHTML = "";
  (d.portfolios || []).forEach(p => {
    const card = document.createElement("div");
    card.className = "portfolio-card";
    const halted = p.halted ? `<span class="halted-badge">HALTED</span>` : "";
    let holdingsTbl = "";
    if (p.holdings && p.holdings.length) {
      holdingsTbl = `
        <table class="holdings">
          <thead><tr>
            <th>TICKER</th><th class="num">SHRS</th><th class="num">COST</th>
            <th class="num">PRICE</th><th class="num">WT</th><th class="num">P/L</th>
          </tr></thead>
          <tbody>
            ${p.holdings.map(h => `
              <tr>
                <td>${h.ticker}</td>
                <td class="num">${fmtNum(h.shares, 2)}</td>
                <td class="num">${fmtNum(h.avg_cost)}</td>
                <td class="num">${fmtNum(h.current_price)}</td>
                <td class="num">${fmtPct(h.weight, false)}</td>
                <td class="num ${colorClass(h.unrealized_pl_pct)}">${fmtPct(h.unrealized_pl_pct)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    } else {
      holdingsTbl = `<div class="empty">// no open positions — 100% cash</div>`;
    }
    card.innerHTML = `
      <div class="card-header">
        <div>
          <span class="name">${p.model_key.toUpperCase()}</span>
          <span class="provider"> // ${p.provider || ""} ${p.model_id || ""}</span>
        </div>
        ${halted}
      </div>
      <div class="summary">
        <span class="k">VALUE</span><span class="v">${fmtMoney(p.total_value)}</span>
        <span class="k">RETURN</span><span class="v ${colorClass(p.cumulative_return)}">${fmtPct(p.cumulative_return)}</span>
        <span class="k">CASH</span><span class="v">${fmtMoney(p.cash)} (${fmtPct(p.cash_pct, false)})</span>
        <span class="k">POSITIONS</span><span class="v">${(p.holdings || []).length} / 10</span>
      </div>
      ${holdingsTbl}
    `;
    grid.appendChild(card);
  });
}

function renderTradeFeed(d) {
  const feed = document.getElementById("trade-feed");
  feed.innerHTML = "";
  const trades = d.recent_trades || [];
  if (!trades.length) {
    feed.innerHTML = `<div class="trade-row"><span class="reason">// no trades yet</span></div>`;
    return;
  }
  trades.forEach(t => {
    const row = document.createElement("div");
    row.className = "trade-row";
    const sideClass = t.side === "BUY" ? "side-buy" : "side-sell";
    const ts = (t.timestamp || "").replace("T", " ").slice(0, 19);
    const reason = (t.reasoning || "").slice(0, 80);
    row.innerHTML = `
      <span class="ts">[${ts}]</span>
      <span class="model">${t.model_key.toUpperCase()}</span>
      <span class="${sideClass}">${t.side}</span>
      <span class="ticker">${t.ticker}</span>
      x${fmtNum(t.shares, 2)}
      @ <span class="price">$${fmtNum(t.fill_price)}</span>
      ${t.confidence != null ? `<span class="conf">[c${t.confidence}]</span>` : ""}
      <span class="reason"> // ${reason}</span>
    `;
    feed.appendChild(row);
  });
}

function renderHealth(d) {
  const c = document.getElementById("health-content");
  const lb = d.leaderboard || [];
  const halted = lb.filter(r => r.halted).length;
  const total = lb.length;
  c.innerHTML = `
    <div class="health-row"><span class="k">PROMPT_VERSION</span><span class="v">${d.prompt_version || "—"}</span></div>
    <div class="health-row"><span class="k">BENCHMARK</span><span class="v">${d.benchmark_ticker || "—"}</span></div>
    <div class="health-row"><span class="k">MODELS_ACTIVE</span><span class="v">${total - halted} / ${total}</span></div>
    <div class="health-row"><span class="k">MODELS_HALTED</span><span class="v">${halted}</span></div>
    <div class="health-row"><span class="k">UNIVERSE_SIZE</span><span class="v">${(d.universe?.tickers || []).length}</span></div>
    <div class="health-row"><span class="k">EXPERIMENT_START</span><span class="v">${d.experiment_start || "—"}</span></div>
    <div class="health-row"><span class="k">EXPERIMENT_END</span><span class="v">${d.experiment_end || "—"}</span></div>
    <div class="health-row"><span class="k">PHASE_A_END</span><span class="v">2026-12-31</span></div>
  `;
}

function renderVersionTicker(d) {
  const t = document.getElementById("version-ticker");
  t.innerHTML = "";
  Object.entries(d.models || {}).forEach(([key, cfg]) => {
    const div = document.createElement("div");
    div.className = "version-item";
    div.innerHTML = `<span class="k">${key.toUpperCase()}</span><span class="v">${cfg.model}</span>`;
    t.appendChild(div);
  });
}

// ===== Master interactive equity chart =====
let masterChart = null;

function renderMasterChart() {
  if (!state.data) return;
  const canvas = document.getElementById("equity-chart");
  if (!masterChart) {
    masterChart = new LineChart(canvas, {
      pad: { l: 56, r: 16, t: 16, b: 30 },
      interactive: true,
      tooltipEl: document.getElementById("equity-tooltip"),
      lineWidth: 1.8,
    });
  }
  const allSeries = buildSeries(state.data);
  const filtered = filterAndRebase(allSeries, state.timeframe);
  filtered.forEach(s => { s.muted = state.mutedSeries.has(s.key); });
  masterChart.setSeries(filtered);
  renderLegend(filtered);
}

function renderLegend(series) {
  const legend = document.getElementById("chart-legend");
  legend.innerHTML = "";
  series.forEach(s => {
    const item = document.createElement("div");
    item.className = "legend-item" + (s.muted ? " muted" : "") + (s.dashed ? " dashed" : "");
    item.innerHTML = s.dashed
      ? `<span class="legend-swatch"></span><span>${s.label}</span>`
      : `<span class="legend-swatch" style="background:${s.color}"></span><span>${s.label}</span>`;
    item.addEventListener("click", () => {
      if (state.mutedSeries.has(s.key)) state.mutedSeries.delete(s.key);
      else state.mutedSeries.add(s.key);
      renderMasterChart();
    });
    legend.appendChild(item);
  });
}

// ===== Per-model mini charts =====
const miniCharts = {};

function renderModelCharts() {
  if (!state.data) return;
  const grid = document.getElementById("model-charts-grid");
  const allSeries = buildSeries(state.data);
  const spy = allSeries.find(s => s.key === "spy");

  // Build/refresh cards
  grid.innerHTML = "";
  MODEL_ORDER.forEach(key => {
    const modelSeries = allSeries.find(s => s.key === key);
    if (!modelSeries) return;

    const card = document.createElement("div");
    card.className = "mini-chart-card";

    // Compute return over the active timeframe (not since-inception)
    const filtered = filterAndRebase([modelSeries], state.timeframe)[0];
    const lastVal = filtered.points.length ? filtered.points[filtered.points.length - 1].value : 0;
    const cls = lastVal > 0 ? "pos" : (lastVal < 0 ? "neg" : "neutral");
    const color = lastVal > 0 ? "#00d488" : (lastVal < 0 ? "#ff3355" : "#5f6b80");

    let spyReturn = "—";
    if (spy) {
      const spyFiltered = filterAndRebase([spy], state.timeframe)[0];
      if (spyFiltered.points.length) {
        const sv = spyFiltered.points[spyFiltered.points.length - 1].value;
        spyReturn = fmtPct(sv);
      }
    }

    card.innerHTML = `
      <div class="mc-header">
        <span class="mc-name"><span class="swatch" style="background:${MODEL_COLORS[key]}"></span>${key.toUpperCase()}</span>
        <span class="mc-return" style="color:${color}">${fmtPct(lastVal)}</span>
      </div>
      <div class="mc-sub">${state.timeframe} // vs SPY</div>
      <canvas></canvas>
      <div class="mc-spy">
        <span>SPY ${state.timeframe}</span>
        <span>${spyReturn}</span>
      </div>
    `;
    grid.appendChild(card);

    const canvas = card.querySelector("canvas");
    const chart = new LineChart(canvas, {
      pad: { l: 38, r: 8, t: 8, b: 18 },
      interactive: false,
      smallMode: true,
      lineWidth: 1.6,
    });
    const seriesForCard = [
      { ...filterAndRebase([modelSeries], state.timeframe)[0] },
    ];
    if (spy) seriesForCard.push({ ...filterAndRebase([spy], state.timeframe)[0] });
    chart.setSeries(seriesForCard);
    miniCharts[key] = chart;
  });

  // Add a dedicated SPY card at the end
  if (spy) {
    const spyFiltered = filterAndRebase([spy], state.timeframe)[0];
    const lastVal = spyFiltered.points.length ? spyFiltered.points[spyFiltered.points.length - 1].value : 0;
    const color = lastVal > 0 ? "#00d488" : (lastVal < 0 ? "#ff3355" : "#5f6b80");

    const card = document.createElement("div");
    card.className = "mini-chart-card";
    card.innerHTML = `
      <div class="mc-header">
        <span class="mc-name"><span class="swatch" style="background:${SPY_COLOR}"></span>SPY // BENCHMARK</span>
        <span class="mc-return" style="color:${color}">${fmtPct(lastVal)}</span>
      </div>
      <div class="mc-sub">${state.timeframe} // S&P 500 ETF</div>
      <canvas></canvas>
      <div class="mc-spy">
        <span>REFERENCE INDEX</span>
        <span>—</span>
      </div>
    `;
    grid.appendChild(card);
    const canvas = card.querySelector("canvas");
    const chart = new LineChart(canvas, {
      pad: { l: 38, r: 8, t: 8, b: 18 },
      interactive: false,
      smallMode: true,
      lineWidth: 1.6,
    });
    // Render SPY as solid (not dashed) when it's the focus
    const focusSpy = { ...spyFiltered, dashed: false, color: "#4da3ff" };
    chart.setSeries([focusSpy]);
    miniCharts.spy = chart;
  }
}

// ===== Timeframe controls =====
function wireControls() {
  document.querySelectorAll(".tf-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.timeframe = btn.getAttribute("data-tf");
      renderMasterChart();
      renderModelCharts();
    });
  });
}

// ===== Refresh loop =====
async function refresh() {
  const d = await loadData();
  if (!d) return;
  state.data = d;
  renderStatus(d);
  renderLeaderboard(d);
  renderHealth(d);
  renderMasterChart();
  renderModelCharts();
  renderPortfolios(d);
  renderTradeFeed(d);
  renderVersionTicker(d);
}

wireControls();
refresh();
setInterval(refresh, REFRESH_MS);

window.addEventListener("resize", () => {
  if (masterChart) masterChart.draw();
  Object.values(miniCharts).forEach(c => c.draw());
});
