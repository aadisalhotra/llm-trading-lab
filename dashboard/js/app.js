// LLM Trading Lab — Bloomberg-style intraday terminal dashboard
// Powered by TradingView's lightweight-charts (loaded as a global from CDN).
//
// Layout (top → bottom):
//   1. Hero equity chart: 6 model lines (% return, rebased to 0 at window start)
//      + dashed SPY benchmark line. Full width, 460px tall — the page hero.
//   2. Hero mini cards: one per model, big colored return + sparkline.
//   3. Leaderboard table with per-row 30d sparklines.
//   4. Per-model portfolio panels.
//   5. Trade feed.
//   6. Reference row (system health + version ticker).
//
// Data source: ../data/dashboard.json — refreshed every 5 minutes.

const DATA_URL = "../data/dashboard.json";
const REFRESH_MS = 5 * 60 * 1000;

const MODEL_COLORS = {
  claude:      "#ff7733",  // Sonnet 4.6 — core Anthropic
  gpt:         "#00d4aa",
  gemini:      "#ffd23f",
  grok:        "#b478ff",
  deepseek:    "#ff5599",
  claude_opus: "#cc4411",  // Opus 4.6 — expansion cohort, darker burnt-orange
};
const SPY_COLOR = "#9aa4b8";   // Lighter gray than text-dim so the dashed line is legible
const ACCENT = "#2b8aff";
const GREEN = "#00d488";
const RED = "#ff3355";
const TEXT = "#c8d4e6";
const TEXT_DIM = "#5f6b80";
const BG = "#05070b";
const GRID = "#1a2235";

// Core 5 first, expansion at the end. The leaderboard sorts by performance
// so this ordering only matters for the hero mini-card grid layout.
const MODEL_ORDER = ["claude", "gpt", "gemini", "grok", "deepseek", "claude_opus"];

// Window slice sizes in trading days. TODAY is special-cased — it sources
// from intraday_curves rather than slicing equity_curves.
const TIMEFRAME_DAYS = { "1W": 5, "1M": 21, "3M": 63, "1Y": 252 };

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

// ===== TradingView shared chart options =====
function chartOptions(extra = {}) {
  return Object.assign({
    layout: {
      background: { type: "solid", color: BG },
      textColor: TEXT_DIM,
      fontSize: 11,
      fontFamily: "JetBrains Mono, monospace",
    },
    grid: {
      vertLines: { color: GRID, style: 1 },
      horzLines: { color: GRID, style: 1 },
    },
    rightPriceScale: {
      borderColor: GRID,
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      borderColor: GRID,
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      mode: 1, // magnet
      vertLine: { color: ACCENT, width: 1, style: 2, labelBackgroundColor: ACCENT },
      horzLine: { color: ACCENT, width: 1, style: 2, labelBackgroundColor: ACCENT },
    },
    handleScroll: true,
    handleScale: true,
  }, extra);
}

// ===== Time helpers =====
// lightweight-charts wants seconds-since-epoch (UTC) for intraday, or a
// date string for daily bars. We unify on UNIX seconds — daily series use
// the bar's UTC midnight.
function dateToUnix(dateStr) {
  return Math.floor(new Date(dateStr + "T00:00:00Z").getTime() / 1000);
}
function isoToUnix(iso) {
  return Math.floor(new Date(iso).getTime() / 1000);
}

// ===== Series builders =====
// Each model becomes a {key, color, data: [{time, value}]} where value is
// the % return rebased to 0 at the first point of the active window.
function buildModelLineSeries(data, mode) {
  const out = [];
  if (mode === "TODAY") {
    const curves = data.intraday_curves || {};
    MODEL_ORDER.forEach(key => {
      const points = curves[key];
      if (!points || points.length < 1) return;
      const base = points[0].value;
      if (!base) return;
      const series = points
        .filter(p => p.timestamp)
        .map(p => ({
          time: isoToUnix(p.timestamp),
          value: (p.value / base) - 1,
        }));
      if (series.length) out.push({ key, color: MODEL_COLORS[key], data: series });
    });
    return out;
  }

  // EOD mode — daily snapshots from equity_curves, optionally trimmed to a window
  const curves = data.equity_curves || {};
  MODEL_ORDER.forEach(key => {
    let points = curves[key];
    if (!points || points.length < 1) return;
    if (state.timeframe !== "ALL" && TIMEFRAME_DAYS[state.timeframe]) {
      points = points.slice(-TIMEFRAME_DAYS[state.timeframe]);
    }
    if (!points.length) return;
    const base = points[0].value;
    if (!base) return;
    const series = points.map(p => ({
      time: dateToUnix(p.date),
      value: (p.value / base) - 1,
    }));
    out.push({ key, color: MODEL_COLORS[key], data: series });
  });
  return out;
}

// SPY benchmark as a single dashed line in the same % space as the model
// lines. Extracted from the per-curve `benchmark` field already present in
// equity_curves / intraday_curves so no backend change is required.
function buildSPYLineSeries(data, mode) {
  let prices = [];
  if (mode === "TODAY") {
    const curves = data.intraday_curves || {};
    const refModel = MODEL_ORDER.find(k => (curves[k] || []).some(p => p.benchmark));
    if (refModel) {
      prices = (curves[refModel] || [])
        .filter(p => p.benchmark && p.timestamp)
        .map(p => ({ time: isoToUnix(p.timestamp), price: p.benchmark }));
    }
  } else {
    const curves = data.equity_curves || {};
    const refModel = MODEL_ORDER.find(k => (curves[k] || []).some(p => p.benchmark));
    if (refModel) {
      let pts = curves[refModel] || [];
      if (state.timeframe !== "ALL" && TIMEFRAME_DAYS[state.timeframe]) {
        pts = pts.slice(-TIMEFRAME_DAYS[state.timeframe]);
      }
      prices = pts
        .filter(p => p.benchmark)
        .map(p => ({ time: dateToUnix(p.date), price: p.benchmark }));
    }
  }
  if (!prices.length) return [];
  const base = prices[0].price;
  if (!base) return [];
  return prices.map(p => ({ time: p.time, value: (p.price / base) - 1 }));
}

// Raw equity values (not rebased) for the hand-drawn sparkline canvas. The
// drawSparkline function expects {raw: numericValue} entries and normalizes
// internally — same shape used by the leaderboard's per-row sparklines.
function buildRawPointsForSpark(data, key, mode) {
  if (mode === "TODAY") {
    const curves = data.intraday_curves || {};
    return (curves[key] || []).map(p => ({ raw: p.value }));
  }
  const curves = data.equity_curves || {};
  let points = curves[key] || [];
  if (state.timeframe !== "ALL" && TIMEFRAME_DAYS[state.timeframe]) {
    points = points.slice(-TIMEFRAME_DAYS[state.timeframe]);
  }
  return points.map(p => ({ raw: p.value }));
}

// ===== Master hero chart (TradingView) =====
let masterChart = null;
let masterSeries = {}; // { spy, modelLines: {key: lineSeries} }

function initMasterChart() {
  const el = document.getElementById("master-chart");
  el.innerHTML = "";
  masterChart = LightweightCharts.createChart(el, chartOptions({
    width: el.clientWidth,
    height: 460,
  }));

  // Single % scale on the right — every series is rebased to 0% at window start
  masterChart.priceScale("right").applyOptions({
    visible: true,
    borderColor: GRID,
    scaleMargins: { top: 0.1, bottom: 0.1 },
  });

  masterSeries.modelLines = {};
  MODEL_ORDER.forEach(key => {
    const line = masterChart.addLineSeries({
      color: MODEL_COLORS[key],
      lineWidth: 2,
      lineStyle: 0,            // solid
      priceFormat: { type: "percent", precision: 2, minMove: 0.0001 },
      lastValueVisible: true,
      priceLineVisible: false,
      crosshairMarkerRadius: 4,
    });
    masterSeries.modelLines[key] = line;
  });

  // SPY benchmark — dashed gray line
  masterSeries.spy = masterChart.addLineSeries({
    color: SPY_COLOR,
    lineWidth: 2,
    lineStyle: 2,              // dashed
    priceFormat: { type: "percent", precision: 2, minMove: 0.0001 },
    lastValueVisible: true,
    priceLineVisible: false,
    crosshairMarkerRadius: 4,
  });
}

function refreshMasterChart() {
  if (!state.data) return;
  if (!masterChart) initMasterChart();

  const mode = state.timeframe === "TODAY" ? "TODAY" : "EOD";
  const modelLines = buildModelLineSeries(state.data, mode);
  const spyLine = buildSPYLineSeries(state.data, mode);

  // Reset all model series, then populate visible (non-muted) ones
  MODEL_ORDER.forEach(key => {
    masterSeries.modelLines[key].setData([]);
  });
  modelLines.forEach(s => {
    if (state.mutedSeries.has(s.key)) return;
    masterSeries.modelLines[s.key].setData(s.data);
  });

  if (state.mutedSeries.has("spy")) {
    masterSeries.spy.setData([]);
  } else {
    masterSeries.spy.setData(spyLine);
  }

  if (modelLines.length || spyLine.length) {
    masterChart.timeScale().fitContent();
  }
  renderLegend(modelLines, spyLine.length > 0);
}

function renderLegend(modelLines, spyVisible) {
  const legend = document.getElementById("chart-legend");
  legend.innerHTML = "";

  modelLines.forEach(s => {
    const cfg = (state.data?.models || {})[s.key] || {};
    const label = cfg.display_name || s.key.toUpperCase();
    const item = document.createElement("div");
    item.className = "legend-item" + (state.mutedSeries.has(s.key) ? " muted" : "");
    item.innerHTML = `<span class="legend-swatch" style="background:${s.color}"></span><span>${label}</span>`;
    item.addEventListener("click", () => {
      if (state.mutedSeries.has(s.key)) state.mutedSeries.delete(s.key);
      else state.mutedSeries.add(s.key);
      refreshMasterChart();
    });
    legend.appendChild(item);
  });

  if (spyVisible) {
    const spy = document.createElement("div");
    spy.className = "legend-item dashed" + (state.mutedSeries.has("spy") ? " muted" : "");
    spy.innerHTML = `<span class="legend-swatch"></span><span>SPY (benchmark)</span>`;
    spy.addEventListener("click", () => {
      if (state.mutedSeries.has("spy")) state.mutedSeries.delete("spy");
      else state.mutedSeries.add("spy");
      refreshMasterChart();
    });
    legend.appendChild(spy);
  }
}

// ===== Hero mini cards (return + sparkline per model) =====
function renderHeroMiniCards() {
  if (!state.data) return;
  const grid = document.getElementById("hero-mini-cards");
  grid.innerHTML = "";

  const mode = state.timeframe === "TODAY" ? "TODAY" : "EOD";
  const lines = buildModelLineSeries(state.data, mode);
  const linesByKey = Object.fromEntries(lines.map(s => [s.key, s]));
  const modelsCfg = state.data.models || {};

  MODEL_ORDER.forEach(key => {
    const series = linesByKey[key];
    const cfg = modelsCfg[key] || {};
    const cohort = cfg.cohort || "core";
    const displayName = cfg.display_name || key.toUpperCase();

    const lastVal = series && series.data.length
      ? series.data[series.data.length - 1].value
      : 0;
    const color = lastVal > 0 ? GREEN : (lastVal < 0 ? RED : TEXT_DIM);

    const card = document.createElement("div");
    card.className = "hero-mini-card";
    if (cohort === "expansion") card.classList.add("cohort-expansion");

    const cohortBadge = cohort === "expansion"
      ? `<span class="cohort-badge cohort-exp">EXP</span>`
      : "";

    card.innerHTML = `
      <div class="hmc-name">
        <span class="hmc-name-text">
          <span class="swatch" style="background:${MODEL_COLORS[key]}"></span>${displayName}
        </span>
        ${cohortBadge}
      </div>
      <div class="hmc-return" style="color:${color}">${fmtPct(lastVal)}</div>
      <canvas class="hmc-spark"></canvas>
    `;
    grid.appendChild(card);

    // Draw sparkline with raw equity values (drawSparkline normalizes internally)
    const canvas = card.querySelector(".hmc-spark");
    requestAnimationFrame(() => {
      const sparkPoints = buildRawPointsForSpark(state.data, key, mode);
      drawSparkline(canvas, sparkPoints);
    });
  });
}

// ===== Sparklines (hand-drawn canvas — used by leaderboard rows + hero cards) =====
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
  const color = last >= 0 ? GREEN : RED;
  const fill = last >= 0 ? "rgba(0,212,136,0.18)" : "rgba(255,51,85,0.18)";

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

  ctx.beginPath();
  norm.forEach((v, i) => {
    const x = (cssW * i) / (norm.length - 1 || 1);
    const y = cssH * (1 - (v - yMin) / (yMax - yMin));
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.stroke();

  const ly = cssH * (1 - (last - yMin) / (yMax - yMin));
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(cssW - 1, ly, 1.8, 0, Math.PI * 2);
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
  const coreCount = lb.filter(r => (r.cohort || "core") === "core").length;
  const expCount = lb.length - coreCount;
  document.getElementById("lb-meta").textContent =
    `${lb.length} MODELS  //  ${coreCount} CORE  //  ${expCount} EXPANSION`;
  const curves = d.equity_curves || {};

  lb.forEach((row, i) => {
    const tr = document.createElement("tr");
    if (i === 0) tr.className = "rank-1";
    const cohort = row.cohort || "core";
    if (cohort === "expansion") tr.classList.add("cohort-expansion");
    const cfg = (d.models || {})[row.model_key] || {};
    const ret = row.cumulative_return;
    const alpha = row.alpha_vs_spy;
    const dd = row.max_drawdown;
    const displayName = row.display_name || cfg.display_name || row.model_key.toUpperCase();
    const cohortBadge = cohort === "expansion"
      ? `<span class="cohort-badge cohort-exp">EXP</span>`
      : `<span class="cohort-badge cohort-core">CORE</span>`;
    tr.innerHTML = `
      <td>${row.rank}</td>
      <td>
        <span class="model-name">${displayName}</span>
        ${cohortBadge}
      </td>
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
    if ((p.cohort || "core") === "expansion") card.classList.add("cohort-expansion");
    const halted = p.halted ? `<span class="halted-badge">HALTED</span>` : "";
    const cohortBadge = (p.cohort || "core") === "expansion"
      ? `<span class="cohort-badge cohort-exp">EXP</span>`
      : "";
    const displayName = p.display_name || p.model_key.toUpperCase();
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
          <span class="name">${displayName}</span>
          ${cohortBadge}
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
    <div class="health-row"><span class="k">SESSION</span><span class="v">${d.intraday_session_date || "—"}</span></div>
    <div class="health-row"><span class="k">EXPERIMENT_START</span><span class="v">${d.experiment_start || "—"}</span></div>
    <div class="health-row"><span class="k">EXPERIMENT_END</span><span class="v">${d.experiment_end || "—"}</span></div>
  `;
}

function renderVersionTicker(d) {
  const t = document.getElementById("version-ticker");
  t.innerHTML = "";
  Object.entries(d.models || {}).forEach(([key, cfg]) => {
    const div = document.createElement("div");
    div.className = "version-item";
    if ((cfg.cohort || "core") === "expansion") div.classList.add("cohort-expansion");
    const label = cfg.display_name || key.toUpperCase();
    div.innerHTML = `<span class="k">${label}</span><span class="v">${cfg.model}</span>`;
    t.appendChild(div);
  });
}

// ===== Timeframe controls =====
function wireControls() {
  document.querySelectorAll(".tf-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.timeframe = btn.getAttribute("data-tf");
      refreshMasterChart();
      renderHeroMiniCards();
    });
  });
}

// ===== Refresh loop =====
async function refresh() {
  const d = await loadData();
  if (!d) return;
  state.data = d;
  renderStatus(d);
  refreshMasterChart();
  renderHeroMiniCards();
  renderLeaderboard(d);
  renderHealth(d);
  renderPortfolios(d);
  renderTradeFeed(d);
  renderVersionTicker(d);
}

wireControls();
refresh();
setInterval(refresh, REFRESH_MS);

window.addEventListener("resize", () => {
  if (masterChart) {
    const el = document.getElementById("master-chart");
    const newHeight = window.innerWidth <= 800 ? 360 : 460;
    masterChart.applyOptions({ width: el.clientWidth, height: newHeight });
    masterChart.timeScale().fitContent();
  }
  // Hero mini-card sparklines need to redraw at the new container width
  if (state.data) renderHeroMiniCards();
});
