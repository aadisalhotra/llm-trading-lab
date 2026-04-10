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
// Data source: data/dashboard.json — refreshed every 5 minutes.
// Path is relative to the Pages site root (the dashboard/ dir becomes /).

const DATA_URL = "data/dashboard.json";
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
// Convert an ISO timestamp (or "YYYY-MM-DD HH:MM:SS") to 12-hour AM/PM.
// showSeconds: include ":SS" (default true). Returns "YYYY-MM-DD h:MM:SS AM".
function fmtTime(iso, showSeconds = true) {
  if (!iso) return "—";
  const s = iso.replace("T", " ");
  const datepart = s.slice(0, 10);
  const timepart = s.slice(11, 19);
  if (!timepart) return datepart;
  const [hh, mm, ss] = timepart.split(":");
  let h = parseInt(hh, 10);
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  const time = showSeconds ? `${h}:${mm}:${ss} ${ampm}` : `${h}:${mm} ${ampm}`;
  return `${datepart} ${time}`;
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
      scaleMargins: { top: 0.02, bottom: 0.02 },
      autoScale: true,
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

// Raw SPY benchmark prices for sparkline — mirrors buildRawPointsForSpark
// but pulls from the benchmark field embedded in equity/intraday curves.
function buildRawSPYPointsForSpark(data, mode) {
  if (mode === "TODAY") {
    const curves = data.intraday_curves || {};
    const refModel = MODEL_ORDER.find(k => (curves[k] || []).some(p => p.benchmark));
    if (!refModel) return [];
    return (curves[refModel] || [])
      .filter(p => p.benchmark)
      .map(p => ({ raw: p.benchmark }));
  }
  const curves = data.equity_curves || {};
  const refModel = MODEL_ORDER.find(k => (curves[k] || []).some(p => p.benchmark));
  if (!refModel) return [];
  let pts = curves[refModel] || [];
  if (state.timeframe !== "ALL" && TIMEFRAME_DAYS[state.timeframe]) {
    pts = pts.slice(-TIMEFRAME_DAYS[state.timeframe]);
  }
  return pts.filter(p => p.benchmark).map(p => ({ raw: p.benchmark }));
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

  // Single % scale on the right — every series is rebased to 0% at window start.
  // Tight margins (2%) so even small return differences produce visible movement
  // instead of a flat-looking chart when all models are within fractions of a percent.
  masterChart.priceScale("right").applyOptions({
    visible: true,
    borderColor: GRID,
    scaleMargins: { top: 0.02, bottom: 0.02 },
    autoScale: true,
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

  // SPY benchmark mini card — same layout as model cards
  const spyLine = buildSPYLineSeries(state.data, mode);
  const spyLastVal = spyLine.length ? spyLine[spyLine.length - 1].value : 0;
  const spyColor = spyLastVal > 0 ? GREEN : (spyLastVal < 0 ? RED : TEXT_DIM);
  const spyCard = document.createElement("div");
  spyCard.className = "hero-mini-card cohort-benchmark";
  spyCard.innerHTML = `
    <div class="hmc-name">
      <span class="hmc-name-text">
        <span class="swatch" style="background:${SPY_COLOR}"></span>SPY
      </span>
      <span class="cohort-badge cohort-bench">BENCH</span>
    </div>
    <div class="hmc-return" style="color:${spyColor}">${fmtPct(spyLastVal)}</div>
    <canvas class="hmc-spark"></canvas>
  `;
  grid.appendChild(spyCard);
  requestAnimationFrame(() => {
    const spyCanvas = spyCard.querySelector(".hmc-spark");
    const spySparkPoints = buildRawSPYPointsForSpark(state.data, mode);
    drawSparkline(spyCanvas, spySparkPoints);
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
    d.generated_at ? fmtTime(d.generated_at) : "—";
  document.getElementById("generated").textContent =
    "GENERATED " + fmtTime(d.generated_at);
}

function renderLeaderboard(d) {
  const tbody = document.getElementById("leaderboard-body");
  tbody.innerHTML = "";
  const lbAll = d.leaderboard || [];

  // Split competing models from benchmark rows. Benchmarks are pinned to the
  // bottom regardless of sort order — they're not competing for rank.
  const competing = lbAll.filter(r => (r.cohort || "core") !== "benchmark");
  const benchmarks = lbAll.filter(r => (r.cohort || "core") === "benchmark");

  const coreCount = competing.filter(r => (r.cohort || "core") === "core").length;
  const expCount = competing.length - coreCount;
  const benchLabel = benchmarks.length ? `  +  ${benchmarks.length} BENCHMARK` : "";
  document.getElementById("lb-meta").textContent =
    `${competing.length} MODELS  //  ${coreCount} CORE  //  ${expCount} EXPANSION${benchLabel}`;

  const curves = d.equity_curves || {};
  const ordered = [...competing, ...benchmarks];

  ordered.forEach((row, i) => {
    const tr = document.createElement("tr");
    const cohort = row.cohort || "core";
    if (i === 0 && cohort !== "benchmark") tr.className = "rank-1";
    if (cohort === "expansion") tr.classList.add("cohort-expansion");
    if (cohort === "benchmark") tr.classList.add("cohort-benchmark");

    const cfg = (d.models || {})[row.model_key] || {};
    const ret = row.cumulative_return;
    const dailyPnl = row.daily_pnl_pct;
    const winRate = row.win_rate;
    const alpha = row.alpha_vs_spy;
    const dd = row.max_drawdown;
    const displayName = row.display_name || cfg.display_name || row.model_key.toUpperCase();

    let badge;
    if (cohort === "benchmark") badge = `<span class="cohort-badge cohort-bench">BENCH</span>`;
    else if (cohort === "expansion") badge = `<span class="cohort-badge cohort-exp">EXP</span>`;
    else badge = `<span class="cohort-badge cohort-core">CORE</span>`;

    // Benchmarks render in neutral gray — no green/red coloring on the
    // return columns since they aren't competing for performance.
    const numClass = (val) => cohort === "benchmark" ? "num neutral" : `num ${colorClass(val)}`;

    const versionLabel = cohort === "benchmark"
      ? `<span class="model-version">S&amp;P 500 ETF</span>`
      : `<span class="model-version">${cfg.model || "—"}</span>`;

    const positionsCell = cohort === "benchmark" ? "—" : (row.num_positions ?? "—");
    const cashCell = cohort === "benchmark"
      ? "—"
      : (row.current_cash_pct != null ? fmtPct(row.current_cash_pct, false) : "—");
    const rankCell = cohort === "benchmark" ? "—" : row.rank;

    tr.innerHTML = `
      <td>${rankCell}</td>
      <td>
        <span class="model-name">${displayName}</span>
        ${badge}
      </td>
      <td>${versionLabel}</td>
      <td class="${numClass(ret)}">${fmtPct(ret)}</td>
      <td class="${numClass(dailyPnl)}">${dailyPnl != null ? fmtPct(dailyPnl) : "—"}</td>
      <td class="spark-cell"><canvas data-spark="${row.model_key}"></canvas></td>
      <td class="num">${row.sharpe_30d != null ? fmtNum(row.sharpe_30d) : "—"}</td>
      <td class="num ${cohort === "benchmark" ? "neutral" : (dd != null && dd < 0 ? "neg" : "neutral")}">${dd != null ? fmtPct(dd) : "—"}</td>
      <td class="num">${winRate != null ? fmtPct(winRate, false) : "—"}</td>
      <td class="${numClass(alpha)}">${alpha != null ? fmtPct(alpha) : "—"}</td>
      <td class="num">${positionsCell}</td>
      <td class="num">${cashCell}</td>
      <td class="num">${fmtMoney(row.current_value)}</td>
    `;
    // Hover tooltip — only for competing models (benchmarks have no trades)
    if (cohort !== "benchmark") {
      attachLeaderboardTooltip(tr, displayName, row.recent_summaries || []);
    }
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
  const modelsCfg = d.models || {};
  trades.forEach(t => {
    const row = document.createElement("div");
    row.className = "trade-row";
    const sideClass = t.side === "BUY" ? "side-buy" : "side-sell";
    const ts = fmtTime(t.timestamp);
    // Prefer the new one-sentence summary; fall back to truncated reasoning
    const summary = (t.summary || t.reasoning || "").trim();
    const cfg = modelsCfg[t.model_key] || {};
    const modelLabel = cfg.display_name || t.model_key.toUpperCase();
    row.innerHTML = `
      <div class="trade-line">
        <span class="ts">[${ts}]</span>
        <span class="model">${modelLabel}</span>
        <span class="${sideClass}">${t.side}</span>
        <span class="ticker">${t.ticker}</span>
        x${fmtNum(t.shares, 2)}
        @ <span class="price">$${fmtNum(t.fill_price)}</span>
        ${t.confidence != null ? `<span class="conf">Confidence: ${t.confidence}/10</span>` : ""}
      </div>
      ${summary ? `<div class="trade-summary">"${escapeHtml(summary)}"</div>` : ""}
    `;
    feed.appendChild(row);
  });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// ===== Leaderboard hover tooltip — shows last 3 trade summaries =====
let _leaderboardTooltip = null;
function _ensureLeaderboardTooltip() {
  if (_leaderboardTooltip) return _leaderboardTooltip;
  _leaderboardTooltip = document.createElement("div");
  _leaderboardTooltip.className = "leaderboard-tooltip";
  _leaderboardTooltip.style.display = "none";
  document.body.appendChild(_leaderboardTooltip);
  return _leaderboardTooltip;
}

function attachLeaderboardTooltip(tr, modelLabel, summaries) {
  if (!summaries || !summaries.length) return;
  const tip = _ensureLeaderboardTooltip();
  tr.addEventListener("mouseenter", (e) => {
    const rows = summaries.map(s => {
      const ts = fmtTime(s.timestamp, false);
      const sideClass = s.side === "BUY" ? "side-buy" : "side-sell";
      const conf = s.confidence != null ? `<span class="conf">[c${s.confidence}]</span>` : "";
      return `
        <div class="lt-trade">
          <div class="lt-trade-head">
            <span class="ts">${ts}</span>
            <span class="${sideClass}">${s.side}</span>
            <span class="ticker">${escapeHtml(s.ticker)}</span>
            ${conf}
          </div>
          <div class="lt-trade-summary">"${escapeHtml(s.summary || "")}"</div>
        </div>
      `;
    }).join("");
    tip.innerHTML = `
      <div class="lt-header">${escapeHtml(modelLabel)} // LAST ${summaries.length} TRADES</div>
      ${rows}
    `;
    tip.style.display = "block";
  });
  tr.addEventListener("mousemove", (e) => {
    // Position tooltip near cursor, clamped to viewport
    const margin = 14;
    let x = e.clientX + margin;
    let y = e.clientY + margin;
    const rect = tip.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 8) x = e.clientX - rect.width - margin;
    if (y + rect.height > window.innerHeight - 8) y = e.clientY - rect.height - margin;
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  });
  tr.addEventListener("mouseleave", () => {
    tip.style.display = "none";
  });
}

// ===== API Cost Tracker panel =====
function renderCostTracker(d) {
  const tbody = document.getElementById("cost-tracker-body");
  if (!tbody) return;
  tbody.innerHTML = "";
  const rows = d.cost_tracker || [];
  const modelsCfg = d.models || {};

  // Aggregate totals for the panel header
  let totalToday = 0, totalMonth = 0, totalAll = 0;
  rows.forEach(r => {
    totalToday += r.cost_today_usd || 0;
    totalMonth += r.cost_month_usd || 0;
    totalAll += r.cost_total_usd || 0;
  });
  const meta = document.getElementById("cost-meta");
  if (meta) {
    meta.textContent =
      `TODAY $${totalToday.toFixed(4)}  //  MONTH $${totalMonth.toFixed(2)}  //  TOTAL $${totalAll.toFixed(2)}`;
  }

  // Find the maximum bar magnitude across rows so the bars are
  // scaled relative to the largest spender. Min floor of $0.01 so
  // a single cent renders visibly.
  let maxMagnitude = 0.01;
  rows.forEach(r => {
    if ((r.cost_total_usd || 0) > maxMagnitude) maxMagnitude = r.cost_total_usd;
    if (r.gross_pnl_usd != null && Math.abs(r.gross_pnl_usd) > maxMagnitude) {
      maxMagnitude = Math.abs(r.gross_pnl_usd);
    }
  });

  // Order: by net P&L descending so the most cost-efficient model floats
  // to the top of the cost panel (different from the leaderboard sort,
  // which is on gross return)
  const ordered = [...rows].sort((a, b) => {
    const an = a.net_pnl_usd != null ? a.net_pnl_usd : -Infinity;
    const bn = b.net_pnl_usd != null ? b.net_pnl_usd : -Infinity;
    return bn - an;
  });

  ordered.forEach(r => {
    const tr = document.createElement("tr");
    const cfg = modelsCfg[r.model_key] || {};
    const cohort = cfg.cohort || "core";
    if (cohort === "expansion") tr.classList.add("cohort-expansion");
    if (r.is_profitable) tr.classList.add("profitable");
    else if (r.net_pnl_usd != null) tr.classList.add("unprofitable");

    const displayName = cfg.display_name || r.model_key.toUpperCase();
    const cohortBadge = cohort === "expansion"
      ? `<span class="cohort-badge cohort-exp">EXP</span>`
      : "";

    const cost$ = (v) => v != null ? `$${Number(v).toFixed(4)}` : "—";
    const dollar = (v) => {
      if (v == null) return "—";
      const sign = v >= 0 ? "+" : "-";
      return `${sign}$${Math.abs(v).toFixed(2)}`;
    };

    tr.innerHTML = `
      <td>
        <span class="model-name">${escapeHtml(displayName)}</span>
        ${cohortBadge}
      </td>
      <td class="num">${cost$(r.cost_today_usd)}</td>
      <td class="num">${cost$(r.cost_week_usd)}</td>
      <td class="num">${cost$(r.cost_month_usd)}</td>
      <td class="num">${cost$(r.cost_total_usd)}</td>
      <td class="num">${r.cost_per_trade_usd != null ? cost$(r.cost_per_trade_usd) : "—"}</td>
      <td class="num ${r.gross_pnl_usd != null && r.gross_pnl_usd >= 0 ? "pos" : (r.gross_pnl_usd != null ? "neg" : "neutral")}">${dollar(r.gross_pnl_usd)}</td>
      <td class="num net-cell">${dollar(r.net_pnl_usd)}</td>
      <td>${_renderRoiBar(r, maxMagnitude)}</td>
    `;
    tbody.appendChild(tr);
  });

  // Budget warnings — surfaced under the table when any provider is at/over
  // its monthly cap thresholds
  const warningsEl = document.getElementById("budget-warnings");
  if (warningsEl) {
    warningsEl.innerHTML = "";
    const bs = d.budget_status || {};
    const providers = bs.providers || {};
    Object.entries(providers).forEach(([provider, info]) => {
      if (info.status === "ok") return;
      const div = document.createElement("div");
      div.className = info.status === "critical" ? "budget-critical" : "budget-warn";
      const pct = (info.pct_of_cap * 100).toFixed(0);
      div.textContent = `${info.status === "critical" ? "[CRITICAL]" : "[WARN]"} ${provider.toUpperCase()} — $${info.spend_usd.toFixed(2)} of $${info.cap_usd.toFixed(2)} monthly cap (${pct}% used). Models: ${(info.models || []).join(", ")}`;
      warningsEl.appendChild(div);
    });
  }
}

function _renderRoiBar(row, maxMagnitude) {
  // Two stacked horizontal bars: API cost (amber) and gross P&L (green/red).
  // Both share the same max-magnitude scale so they're visually comparable.
  // Whichever bar is longer wins — if P&L > Cost the model is making money
  // net of API spend. The accent breakeven line marks the cost-equals-pnl
  // point on the P&L bar.
  const cost = row.cost_total_usd || 0;
  const pnl = row.gross_pnl_usd;
  const scale = (v) => Math.min(100, (Math.abs(v) / maxMagnitude) * 100);

  const costPct = scale(cost);
  const pnlPct = pnl != null ? scale(pnl) : 0;
  const pnlClass = pnl != null && pnl >= 0 ? "pnl-pos" : "pnl-neg";

  // Breakeven line position on the P&L bar = where cost magnitude sits on
  // the same scale. If P&L bar reaches past it, the model is profitable.
  const breakevenPct = scale(cost);

  return `
    <div class="roi-bar">
      <div class="roi-row">
        <span class="roi-label">COST</span>
        <div class="roi-track">
          <div class="roi-fill cost" style="width:${costPct.toFixed(1)}%"></div>
        </div>
      </div>
      <div class="roi-row">
        <span class="roi-label">P&amp;L</span>
        <div class="roi-track">
          <div class="roi-fill ${pnlClass}" style="width:${pnlPct.toFixed(1)}%"></div>
          <div class="breakeven-line" style="left:${breakevenPct.toFixed(1)}%"></div>
        </div>
      </div>
    </div>
  `;
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
  renderCostTracker(d);
  renderHealth(d);
  renderPortfolios(d);
  renderTradeFeed(d);
  renderVersionTicker(d);
}

// ===== Countdown timer to next pipeline run =====
// Market hours: 9:00 AM – 4:30 PM ET, Mon–Fri.
// During market hours: count down to next :00/:15/:30/:45.
// Outside market hours: count down to 9:00 AM ET next trading day.
function updateCountdown() {
  const el = document.getElementById("nextrun");
  if (!el) return;

  const now = new Date();

  // Convert to ET. America/New_York handles DST automatically.
  const etStr = now.toLocaleString("en-US", { timeZone: "America/New_York" });
  const et = new Date(etStr);
  const etHour = et.getHours();
  const etMin = et.getMinutes();
  const etSec = et.getSeconds();
  const etDay = et.getDay(); // 0=Sun, 6=Sat

  const isWeekday = etDay >= 1 && etDay <= 5;
  const marketOpen = 9 * 60;           // 9:00 AM in minutes
  const marketClose = 16 * 60 + 30;    // 4:30 PM in minutes
  const nowMin = etHour * 60 + etMin;
  const duringMarket = isWeekday && nowMin >= marketOpen && nowMin < marketClose;

  let remainSec;

  if (duringMarket) {
    // Next :00, :15, :30, or :45 mark
    const nextQuarter = (Math.floor(etMin / 15) + 1) * 15;
    remainSec = (nextQuarter - etMin) * 60 - etSec;
    if (remainSec <= 0) remainSec += 15 * 60;
  } else {
    // Time until 9:00 AM ET next trading day
    const target = new Date(et);
    target.setHours(9, 0, 0, 0);

    if (isWeekday && nowMin < marketOpen) {
      // Before open today — target is today 9:00 AM
    } else if (etDay === 5) {
      // Friday after close — next Monday
      target.setDate(target.getDate() + 3);
    } else if (etDay === 6) {
      // Saturday — next Monday
      target.setDate(target.getDate() + 2);
    } else if (etDay === 0) {
      // Sunday — next Monday
      target.setDate(target.getDate() + 1);
    } else {
      // Weekday after close — next day
      target.setDate(target.getDate() + 1);
    }

    remainSec = Math.floor((target.getTime() - et.getTime()) / 1000);
    if (remainSec < 0) remainSec = 0;
  }

  // Format the countdown
  if (remainSec < 3600) {
    const m = Math.floor(remainSec / 60);
    const s = remainSec % 60;
    el.textContent = `${m}m ${s.toString().padStart(2, "0")}s`;
  } else {
    const h = Math.floor(remainSec / 3600);
    const m = Math.floor((remainSec % 3600) / 60);
    el.textContent = `${h}h ${m.toString().padStart(2, "0")}m`;
  }

  el.className = duringMarket ? "value open" : "value";
}

updateCountdown();
setInterval(updateCountdown, 1000);

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
