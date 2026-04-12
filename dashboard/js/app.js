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
  h2hA: null,   // model_key for left side of H2H comparison
  h2hB: null,   // model_key for right side of H2H comparison
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
// Convert a UTC ISO timestamp to US/Eastern 12-hour AM/PM format.
// Pipeline writes all timestamps in UTC; this converts for display.
function fmtTime(iso, showSeconds = true) {
  if (!iso) return "—";
  // Ensure the string parses as UTC — append "Z" if no timezone indicator
  let utcStr = iso;
  if (!/[Zz+\-]/.test(iso.slice(10))) utcStr = iso + "Z";
  const d = new Date(utcStr);
  if (isNaN(d.getTime())) return iso;
  const opts = {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "numeric", minute: "2-digit",
    hour12: true,
  };
  if (showSeconds) opts.second = "2-digit";
  // toLocaleString gives "MM/DD/YYYY, h:MM:SS AM" — reformat to YYYY-MM-DD
  const parts = d.toLocaleDateString("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }); // "YYYY-MM-DD"
  const timePart = d.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "numeric", minute: "2-digit",
    ...(showSeconds ? { second: "2-digit" } : {}),
    hour12: true,
  }); // "h:MM:SS AM"
  return `${parts} ${timePart}`;
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
      priceFormat: { type: "custom", formatter: (v) => (v * 100).toFixed(2) + "%", minMove: 0.0001 },
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
    priceFormat: { type: "custom", formatter: (v) => (v * 100).toFixed(2) + "%", minMove: 0.0001 },
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
// ===== Market Brief banner =====
function renderMarketBrief(d) {
  const panel = document.getElementById("market-brief-panel");
  const textEl = document.getElementById("brief-text");
  const movesEl = document.getElementById("brief-moves");
  if (!panel || !textEl) return;

  const mb = d.market_brief || {};
  const brief = mb.brief || "";
  if (!brief) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";

  // Check if market is currently open — if not, show stale note
  const now = new Date();
  const etStr = now.toLocaleString("en-US", { timeZone: "America/New_York" });
  const et = new Date(etStr);
  const etDay = et.getDay();
  const etMin = et.getHours() * 60 + et.getMinutes();
  const isOpen = etDay >= 1 && etDay <= 5 && etMin >= 570 && etMin < 960;

  const staleNote = !isOpen && mb.as_of_date
    ? `<span class="brief-stale">(Last trading day: ${mb.as_of_date})</span>`
    : "";

  // Highlight "Welcome." prefix
  const formatted = brief.replace(
    /^Welcome\./,
    '<span class="brief-welcome">Welcome.</span>',
  );
  textEl.innerHTML = formatted + staleNote;

  if (movesEl) {
    const moves = mb.key_moves || "";
    if (moves) {
      movesEl.innerHTML = `<span class="moves-label">KEY MOVES</span>${moves}`;
      movesEl.style.display = "";
    } else {
      movesEl.style.display = "none";
    }
  }
}

// ===== MVP Trade =====
function renderMvpTrade(d) {
  const panel = document.getElementById("mvp-panel");
  const content = document.getElementById("mvp-content");
  const meta = document.getElementById("mvp-meta");
  if (!panel || !content) return;

  const mvp = d.mvp_trade;
  if (!mvp) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";

  const color = MODEL_COLORS[mvp.model_key] || TEXT;
  const sideClass = mvp.side === "SELL" ? "sell" : "buy";
  const sideLabel = mvp.side === "SELL" ? "SOLD" : "BOUGHT";

  // Prices
  const entryPrice = mvp.fill_price ? `$${mvp.fill_price.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : "—";
  const exitPrice = mvp.current_price ? `$${mvp.current_price.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : "—";
  const priceLabel = mvp.side === "SELL" ? "EXIT" : "NOW";

  // P&L
  let pnlHtml = "";
  if (mvp.pnl_pct != null) {
    const pct = mvp.pnl_pct;
    const sign = pct > 0 ? "+" : "";
    const cls = pct > 0 ? "pos" : pct < 0 ? "neg" : "flat";
    pnlHtml = `<span class="mvp-pnl ${cls}">${sign}${(pct * 100).toFixed(2)}%</span>`;
  }

  // Confidence
  const confHtml = mvp.confidence != null
    ? `<span class="mvp-conf">CONF ${mvp.confidence}/10</span>`
    : "";

  // Summary
  const summary = mvp.summary || "";
  const summaryHtml = summary
    ? `<div class="mvp-summary">"${summary}"</div>`
    : "";

  // Meta label
  const reason = mvp.selection_reason;
  if (reason === "highest_conviction") {
    meta.textContent = "HIGHEST CONVICTION (no closes yet)";
  } else {
    meta.textContent = mvp.date || "—";
  }

  content.innerHTML = `
    <span class="mvp-model" style="color:${color}">${mvp.display_name}</span>
    <span class="mvp-action ${sideClass}">${sideLabel}</span>
    <span class="mvp-ticker">${mvp.ticker}</span>
    <span class="mvp-prices">${entryPrice} <span class="mvp-arrow">\u2192</span> ${exitPrice} <span class="mvp-arrow">(${priceLabel})</span></span>
    ${pnlHtml}
    ${confHtml}
    ${summaryHtml}
  `;
}

// ===== Ticker Tape =====
function renderTickerTape(d) {
  const track = document.getElementById("ticker-track");
  if (!track) return;

  const tape = d.ticker_tape || [];
  if (!tape.length) {
    document.getElementById("ticker-tape").style.display = "none";
    return;
  }
  document.getElementById("ticker-tape").style.display = "";

  // Build one set of items
  const buildItems = () => tape.map(t => {
    const pct = t.change_pct || 0;
    const sign = pct > 0 ? "+" : "";
    const cls = pct > 0 ? "pos" : pct < 0 ? "neg" : "flat";
    const arrow = pct > 0 ? "\u25B2" : pct < 0 ? "\u25BC" : "";
    return `<span class="ticker-item">`
      + `<span class="tk-symbol">${t.symbol}</span>`
      + `<span class="tk-price">$${t.price.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>`
      + `<span class="tk-change ${cls}">${arrow} ${sign}${(pct * 100).toFixed(2)}%</span>`
      + `<span class="tk-sep">|</span>`
      + `</span>`;
  }).join("");

  // Duplicate content so the scroll loops seamlessly
  const items = buildItems();
  track.innerHTML = items + items;

  // Adjust speed based on content width: ~60px/sec for smooth reading
  requestAnimationFrame(() => {
    const halfWidth = track.scrollWidth / 2;
    const speed = 60; // px per second
    const duration = halfWidth / speed;
    track.style.animationDuration = `${duration}s`;
  });
}

function renderStatus(d) {
  document.getElementById("phase").textContent = d.phase || "—";
  document.getElementById("mode").textContent = (d.mode || "—").toUpperCase();
  document.getElementById("day").textContent =
    d.experiment_day ? `${d.experiment_day} / ${d.experiment_total_days}` : "—";

  // Market hours: 9:30 AM – 4:00 PM ET, Mon–Fri. Convert to ET properly
  // so DST shifts don't break the check. Does not detect holidays —
  // the pipeline handles that server-side via pandas_market_calendars.
  const now = new Date();
  const etStr = now.toLocaleString("en-US", { timeZone: "America/New_York" });
  const et = new Date(etStr);
  const etDay = et.getDay(); // 0=Sun, 6=Sat
  const etMinutes = et.getHours() * 60 + et.getMinutes(); // minutes since midnight ET
  const isWeekday = etDay >= 1 && etDay <= 5;
  const inHours = etMinutes >= 570 && etMinutes < 960; // 9:30=570, 16:00=960
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

    // Streak badge
    let streakLabel = "–";
    if (cohort !== "benchmark" && row.streak_count > 0 && row.streak_type) {
      if (row.streak_type === "W") streakLabel = `<span class="streak-win">\u{1F525} ${row.streak_count}W</span>`;
      else streakLabel = `<span class="streak-loss">\u274C ${row.streak_count}L</span>`;
    }

    const daysBadge = (cohort !== "benchmark" && row.days != null)
      ? `<span class="days-badge" title="Days of EOD data">${row.days}d</span>`
      : "";

    tr.innerHTML = `
      <td>${rankCell}</td>
      <td>
        <span class="model-name">${displayName}</span>
        ${badge}
        ${daysBadge}
      </td>
      <td class="streak-cell">${streakLabel}</td>
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


// ===== Consensus Picks =====
function renderConsensusPicks(d) {
  const tbody = document.getElementById("consensus-body");
  if (!tbody) return;
  tbody.innerHTML = "";

  const picks = d.consensus_picks || [];
  const agr = d.agreement_returns || {};
  const meta = document.getElementById("consensus-meta");
  if (meta) {
    meta.textContent = picks.length
      ? `${picks.length} STOCKS HELD BY 3+ MODELS`
      : "NO CONSENSUS POSITIONS";
  }

  // Agreement index stat bar
  const statEl = document.getElementById("agreement-stat");
  if (statEl) {
    const highAvg = agr.high_avg != null ? fmtPct(agr.high_avg) : "—";
    const lowAvg = agr.low_avg != null ? fmtPct(agr.low_avg) : "—";
    const hc = agr.high_count || 0;
    const lc = agr.low_count || 0;
    statEl.innerHTML = `
      <div>
        <span class="stat-label">HIGH AGREEMENT (4+ MODELS)</span>
        <span class="stat-value">${highAvg} avg return (${hc} trades)</span>
      </div>
      <div>
        <span class="stat-label">LOW AGREEMENT (1–2 MODELS)</span>
        <span class="stat-value">${lowAvg} avg return (${lc} trades)</span>
      </div>
    `;
  }

  if (!picks.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--text-dim);font-style:italic;padding:12px 10px">// no stocks held by 3+ models</td></tr>`;
    return;
  }

  const modelsCfg = d.models || {};
  picks.forEach(p => {
    const tr = document.createElement("tr");
    const total = p.total_models || 6;
    if (p.model_count >= total) tr.className = "agreement-6";
    else if (p.model_count >= total - 1) tr.className = "agreement-5";
    else if (p.model_count === 3) tr.className = "agreement-3";

    const modelNames = (p.models || []).map(k => {
      const cfg = modelsCfg[k] || {};
      return cfg.display_name || k.toUpperCase();
    }).join(", ");

    tr.innerHTML = `
      <td><span class="ticker-name">${escapeHtml(p.ticker)}</span>
          <span class="models-label"> ${modelNames}</span></td>
      <td class="num">${p.model_count}/${total}</td>
      <td class="num">${fmtPct(p.avg_weight, false)}</td>
      <td class="num">${p.avg_confidence != null ? p.avg_confidence.toFixed(1) + "/10" : "—"}</td>
      <td class="num ${colorClass(p.avg_pl_pct)}">${fmtPct(p.avg_pl_pct)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ===== Head-to-Head Comparison =====
function initH2HSelectors(d) {
  const selA = document.getElementById("h2h-select-a");
  const selB = document.getElementById("h2h-select-b");
  if (!selA || !selB) return;

  const models = d.models || {};
  const lb = (d.leaderboard || []).filter(r => (r.cohort || "core") !== "benchmark");

  // Build options from leaderboard order (already sorted by rank)
  const buildOpts = (sel) => {
    sel.innerHTML = "";
    lb.forEach(row => {
      const cfg = models[row.model_key] || {};
      const opt = document.createElement("option");
      opt.value = row.model_key;
      opt.textContent = cfg.display_name || row.model_key.toUpperCase();
      sel.appendChild(opt);
    });
  };
  buildOpts(selA);
  buildOpts(selB);

  // Default: two closest models by cumulative return
  if (!state.h2hA || !state.h2hB) {
    let minDiff = Infinity;
    let bestI = 0, bestJ = 1;
    for (let i = 0; i < lb.length; i++) {
      for (let j = i + 1; j < lb.length; j++) {
        const diff = Math.abs((lb[i].cumulative_return || 0) - (lb[j].cumulative_return || 0));
        if (diff < minDiff) { minDiff = diff; bestI = i; bestJ = j; }
      }
    }
    state.h2hA = lb[bestI] ? lb[bestI].model_key : (lb[0] ? lb[0].model_key : null);
    state.h2hB = lb[bestJ] ? lb[bestJ].model_key : (lb[1] ? lb[1].model_key : null);
  }

  selA.value = state.h2hA;
  selB.value = state.h2hB;

  selA.onchange = () => { state.h2hA = selA.value; renderHeadToHead(state.data); };
  selB.onchange = () => { state.h2hB = selB.value; renderHeadToHead(state.data); };
}

function renderHeadToHead(d) {
  const content = document.getElementById("h2h-content");
  if (!content) return;
  content.innerHTML = "";

  const keyA = state.h2hA;
  const keyB = state.h2hB;
  if (!keyA || !keyB) return;

  const models = d.models || {};
  const lb = d.leaderboard || [];
  const rowA = lb.find(r => r.model_key === keyA) || {};
  const rowB = lb.find(r => r.model_key === keyB) || {};
  const cfgA = models[keyA] || {};
  const cfgB = models[keyB] || {};
  const nameA = cfgA.display_name || keyA.toUpperCase();
  const nameB = cfgB.display_name || keyB.toUpperCase();
  const colorA = MODEL_COLORS[keyA] || TEXT;
  const colorB = MODEL_COLORS[keyB] || TEXT;

  // Winner badge
  const retA = rowA.cumulative_return || 0;
  const retB = rowB.cumulative_return || 0;
  const winnerKey = retA >= retB ? keyA : keyB;
  const badgeA = winnerKey === keyA ? '<span class="h2h-winner-badge">LEADER</span>' : "";
  const badgeB = winnerKey === keyB ? '<span class="h2h-winner-badge">LEADER</span>' : "";

  // --- 1. Equity curve chart ---
  const chartWrap = document.createElement("div");
  chartWrap.className = "h2h-chart-wrap";
  const canvas = document.createElement("canvas");
  chartWrap.appendChild(canvas);
  content.appendChild(chartWrap);

  // Legend
  const legend = document.createElement("div");
  legend.className = "h2h-chart-legend";
  legend.innerHTML = `
    <span><span class="swatch" style="background:${colorA}"></span> ${nameA}</span>
    <span><span class="swatch" style="background:${colorB}"></span> ${nameB}</span>
    <span><span class="swatch dashed"></span> SPY</span>
  `;
  content.appendChild(legend);

  // Draw chart after DOM insertion
  requestAnimationFrame(() => drawH2HChart(canvas, d, keyA, keyB, colorA, colorB));

  // --- 2. Stats table ---
  const costTracker = d.cost_tracker || [];
  const costA = costTracker.find(c => c.model_key === keyA) || {};
  const costB = costTracker.find(c => c.model_key === keyB) || {};

  const cal = d.confidence_calibration || {};
  const calA = cal[keyA] || {};
  const calB = cal[keyB] || {};
  const avgConfA = computeAvgConf(calA);
  const avgConfB = computeAvgConf(calB);

  const tradesPerDayA = (costA.trades_executed_total || 0) / Math.max(rowA.days || 1, 1);
  const tradesPerDayB = (costB.trades_executed_total || 0) / Math.max(rowB.days || 1, 1);

  const stats = [
    { label: "CUMULATIVE RETURN", a: fmtPct(rowA.cumulative_return), b: fmtPct(rowB.cumulative_return),
      aWin: retA > retB, bWin: retB > retA },
    { label: "SHARPE (30D)", a: rowA.sharpe_30d != null ? fmtNum(rowA.sharpe_30d) : "—", b: rowB.sharpe_30d != null ? fmtNum(rowB.sharpe_30d) : "—",
      aWin: (rowA.sharpe_30d || 0) > (rowB.sharpe_30d || 0), bWin: (rowB.sharpe_30d || 0) > (rowA.sharpe_30d || 0) },
    { label: "MAX DRAWDOWN", a: fmtPct(rowA.max_drawdown), b: fmtPct(rowB.max_drawdown),
      aWin: (rowA.max_drawdown || 0) > (rowB.max_drawdown || 0), bWin: (rowB.max_drawdown || 0) > (rowA.max_drawdown || 0) },
    { label: "WIN RATE", a: rowA.win_rate != null ? fmtPct(rowA.win_rate, false) : "—", b: rowB.win_rate != null ? fmtPct(rowB.win_rate, false) : "—",
      aWin: (rowA.win_rate || 0) > (rowB.win_rate || 0), bWin: (rowB.win_rate || 0) > (rowA.win_rate || 0) },
    { label: "TRADES / DAY", a: fmtNum(tradesPerDayA, 1), b: fmtNum(tradesPerDayB, 1), aWin: false, bWin: false },
    { label: "AVG CONFIDENCE", a: avgConfA != null ? fmtNum(avgConfA, 1) + "/10" : "—", b: avgConfB != null ? fmtNum(avgConfB, 1) + "/10" : "—",
      aWin: (avgConfA || 0) > (avgConfB || 0), bWin: (avgConfB || 0) > (avgConfA || 0) },
    { label: "CASH %", a: fmtPct(rowA.current_cash_pct, false), b: fmtPct(rowB.current_cash_pct, false), aWin: false, bWin: false },
    { label: "TOTAL API COST", a: "$" + fmtNum(costA.cost_total_usd || 0), b: "$" + fmtNum(costB.cost_total_usd || 0),
      aWin: (costA.cost_total_usd || 0) < (costB.cost_total_usd || 0), bWin: (costB.cost_total_usd || 0) < (costA.cost_total_usd || 0) },
  ];

  const statsRows = stats.map(s => `
    <tr>
      <td>${s.label}</td>
      <td class="${s.aWin ? "better" : ""}">${s.a}</td>
      <td class="${s.bWin ? "better" : ""}">${s.b}</td>
    </tr>
  `).join("");

  const statsTable = document.createElement("table");
  statsTable.className = "h2h-stats";
  statsTable.innerHTML = `
    <thead><tr>
      <th>METRIC</th>
      <th><span style="color:${colorA}">${nameA}</span>${badgeA}</th>
      <th><span style="color:${colorB}">${nameB}</span>${badgeB}</th>
    </tr></thead>
    <tbody>${statsRows}</tbody>
  `;
  content.appendChild(statsTable);

  // --- 3. Bottom row: trade overlap + sector bars ---
  const bottom = document.createElement("div");
  bottom.className = "h2h-bottom";

  // Trade overlap
  const overlap = computeTradeOverlap(d, keyA, keyB);
  const overlapDiv = document.createElement("div");
  overlapDiv.className = "h2h-overlap";
  overlapDiv.innerHTML = `
    <div class="h2h-overlap-title">TRADE OVERLAP</div>
    <div class="h2h-overlap-value">${overlap.pct}%</div>
    <div class="h2h-overlap-detail">${overlap.matched} of ${overlap.total} trades same ticker + direction + day</div>
  `;
  bottom.appendChild(overlapDiv);

  // Sector allocation bars
  const universe = d.universe || {};
  const sectorMap = {};
  (universe.tickers || []).forEach(t => { sectorMap[t.symbol] = t.sector; });

  // Index portfolios by MODEL_ORDER position (model_key in snapshot may
  // differ — e.g. claude_opus reports model_key="claude" in its snapshot).
  const portfolios = d.portfolios || [];
  const portA = portfolios[MODEL_ORDER.indexOf(keyA)] || {};
  const portB = portfolios[MODEL_ORDER.indexOf(keyB)] || {};

  const sectorsA = computeSectorWeights(portA, sectorMap);
  const sectorsB = computeSectorWeights(portB, sectorMap);

  // Merge all sectors
  const allSectors = [...new Set([...Object.keys(sectorsA), ...Object.keys(sectorsB)])].sort();

  bottom.appendChild(buildSectorCol(nameA, colorA, sectorsA, allSectors));
  bottom.appendChild(buildSectorCol(nameB, colorB, sectorsB, allSectors));

  content.appendChild(bottom);
}

function computeAvgConf(calData) {
  const buckets = calData.buckets || [];
  let totalConf = 0, totalCount = 0;
  buckets.forEach(b => {
    if (b.count > 0) {
      totalConf += b.confidence * b.count;
      totalCount += b.count;
    }
  });
  return totalCount > 0 ? totalConf / totalCount : null;
}

function computeTradeOverlap(d, keyA, keyB) {
  const trades = d.recent_trades || [];
  // Group trades by (date, ticker, side) per model
  const setA = new Set();
  const setB = new Set();
  const allA = [];
  const allB = [];
  trades.forEach(t => {
    const sig = `${t.date}|${t.ticker}|${t.side}`;
    if (t.model_key === keyA) { setA.add(sig); allA.push(sig); }
    if (t.model_key === keyB) { setB.add(sig); allB.push(sig); }
  });
  let matched = 0;
  setA.forEach(sig => { if (setB.has(sig)) matched++; });
  const total = new Set([...setA, ...setB]).size;
  const pct = total > 0 ? Math.round((matched / total) * 100) : 0;
  return { matched, total, pct };
}

function computeSectorWeights(portfolio, sectorMap) {
  const weights = {};
  (portfolio.holdings || []).forEach(h => {
    const sec = sectorMap[h.ticker] || "Other";
    // Shorten sector names
    const short = {
      "Technology": "Tech",
      "Communication Services": "Comms",
      "Consumer Discretionary": "Cons Disc",
      "Consumer Staples": "Staples",
      "Healthcare": "Health",
      "Financials": "Finance",
      "Industrials": "Indust",
      "Energy": "Energy",
      "Materials": "Material",
      "Real Estate": "REIT",
      "Utilities": "Utility",
    }[sec] || sec;
    weights[short] = (weights[short] || 0) + (h.weight || 0);
  });
  return weights;
}

function buildSectorCol(name, color, weights, allSectors) {
  const col = document.createElement("div");
  col.className = "h2h-sector-col";
  col.innerHTML = `<div class="h2h-sector-title" style="color:${color}">${name} SECTORS</div>`;

  // Remap allSectors through the same shortening
  const shortMap = {
    "Technology": "Tech", "Communication Services": "Comms",
    "Consumer Discretionary": "Cons Disc", "Consumer Staples": "Staples",
    "Healthcare": "Health", "Financials": "Finance", "Industrials": "Indust",
    "Energy": "Energy", "Materials": "Material", "Real Estate": "REIT", "Utilities": "Utility",
  };
  const shortSectors = [...new Set(allSectors.map(s => shortMap[s] || s))];

  shortSectors.forEach(sec => {
    const w = weights[sec] || 0;
    const pct = (w * 100).toFixed(0);
    const row = document.createElement("div");
    row.className = "h2h-sector-row";
    row.innerHTML = `
      <span class="h2h-sector-label">${sec}</span>
      <div class="h2h-sector-bar-wrap">
        <div class="h2h-sector-bar" style="width:${Math.min(w * 100, 100)}%;background:${color}"></div>
      </div>
      <span class="h2h-sector-pct">${pct}%</span>
    `;
    col.appendChild(row);
  });
  return col;
}

function drawH2HChart(canvas, d, keyA, keyB, colorA, colorB) {
  const rect = canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const cssW = rect.width;
  const cssH = rect.height;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  canvas.style.width = cssW + "px";
  canvas.style.height = cssH + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const curves = d.equity_curves || {};
  const curveA = curves[keyA] || [];
  const curveB = curves[keyB] || [];
  const curveSpy = curves["spy_benchmark"] || [];

  if (!curveA.length && !curveB.length) {
    ctx.fillStyle = TEXT_DIM;
    ctx.font = "11px 'JetBrains Mono', monospace";
    ctx.fillText("No equity data available", 20, cssH / 2);
    return;
  }

  // Unify date range — use all dates from both curves
  const allDates = new Set();
  curveA.forEach(p => allDates.add(p.date));
  curveB.forEach(p => allDates.add(p.date));
  curveSpy.forEach(p => allDates.add(p.date));
  const dates = [...allDates].sort();

  // Build value maps
  const mapA = {}; curveA.forEach(p => { mapA[p.date] = p.value; });
  const mapB = {}; curveB.forEach(p => { mapB[p.date] = p.value; });
  const mapSpy = {}; curveSpy.forEach(p => { mapSpy[p.date] = p.value; });

  // Convert to % return from first available value
  const toReturns = (map) => {
    let base = null;
    return dates.map(d => {
      const v = map[d];
      if (v == null) return null;
      if (base == null) base = v;
      return base > 0 ? (v / base - 1) * 100 : 0;
    });
  };

  const retA = toReturns(mapA);
  const retB = toReturns(mapB);
  const retSpy = toReturns(mapSpy);

  // Find Y range
  const allVals = [...retA, ...retB, ...retSpy].filter(v => v != null);
  if (!allVals.length) return;
  let yMin = Math.min(...allVals);
  let yMax = Math.max(...allVals);
  const yPad = (yMax - yMin) * 0.15 || 1;
  yMin -= yPad;
  yMax += yPad;

  const padL = 50, padR = 10, padT = 10, padB = 24;
  const plotW = cssW - padL - padR;
  const plotH = cssH - padT - padB;
  const xStep = dates.length > 1 ? plotW / (dates.length - 1) : plotW;

  // Grid
  ctx.strokeStyle = GRID;
  ctx.lineWidth = 0.5;
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const y = padT + (plotH / yTicks) * i;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(cssW - padR, y); ctx.stroke();
    const label = (yMax - (yMax - yMin) * (i / yTicks)).toFixed(1) + "%";
    ctx.fillStyle = TEXT_DIM;
    ctx.font = "9px 'JetBrains Mono', monospace";
    ctx.textAlign = "right";
    ctx.fillText(label, padL - 4, y + 3);
  }

  // Zero line
  if (yMin < 0 && yMax > 0) {
    const zeroY = padT + plotH * (yMax / (yMax - yMin));
    ctx.strokeStyle = "#334155";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(padL, zeroY); ctx.lineTo(cssW - padR, zeroY); ctx.stroke();
    ctx.setLineDash([]);
  }

  const drawLine = (returns, color, dashed) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = dashed ? 1 : 1.8;
    if (dashed) ctx.setLineDash([5, 4]);
    else ctx.setLineDash([]);
    ctx.beginPath();
    let started = false;
    returns.forEach((v, i) => {
      if (v == null) return;
      const x = padL + i * xStep;
      const y = padT + plotH * ((yMax - v) / (yMax - yMin));
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  };

  drawLine(retSpy, SPY_COLOR, true);
  drawLine(retA, colorA, false);
  drawLine(retB, colorB, false);

  // Date labels on x-axis
  ctx.fillStyle = TEXT_DIM;
  ctx.font = "9px 'JetBrains Mono', monospace";
  ctx.textAlign = "center";
  const labelStep = Math.max(1, Math.floor(dates.length / 6));
  dates.forEach((dt, i) => {
    if (i % labelStep === 0 || i === dates.length - 1) {
      const x = padL + i * xStep;
      ctx.fillText(dt.slice(5), x, cssH - 4);  // MM-DD
    }
  });
}

// ===== Confidence Calibration =====
function renderConfidenceCalibration(d) {
  const grid = document.getElementById("calibration-grid");
  if (!grid) return;
  grid.innerHTML = "";

  const cal = d.confidence_calibration || {};
  const modelsCfg = d.models || {};

  MODEL_ORDER.forEach(key => {
    const data = cal[key];
    if (!data) return;
    const cfg = modelsCfg[key] || {};
    const displayName = cfg.display_name || key.toUpperCase();
    const card = document.createElement("div");
    card.className = "cal-card";

    const score = data.calibration_score;
    const total = data.total_trades || 0;
    const minTrades = data.min_trades || 20;

    let scoreHtml;
    if (score != null) {
      const cls = score > 0.05 ? "pos" : (score < -0.05 ? "neg" : "neutral");
      scoreHtml = `<span class="cal-score ${cls}">${score >= 0 ? "+" : ""}${score.toFixed(3)}</span>`;
    } else {
      scoreHtml = `<span class="cal-score neutral">—</span>`;
    }

    card.innerHTML = `
      <div class="cal-header">
        <span class="cal-model"><span class="swatch" style="background:${MODEL_COLORS[key]};display:inline-block;width:10px;height:10px;margin-right:6px;vertical-align:-1px"></span>${displayName}</span>
        ${scoreHtml}
      </div>
      <div class="cal-trades">${total} TRADES // CALIBRATION SCORE</div>
    `;

    if (total < minTrades) {
      const insuffEl = document.createElement("div");
      insuffEl.className = "cal-insufficient";
      insuffEl.textContent = `Insufficient data (${total}/${minTrades} trades)`;
      card.appendChild(insuffEl);
    } else {
      const canvas = document.createElement("canvas");
      card.appendChild(canvas);
      grid.appendChild(card);
      requestAnimationFrame(() => drawCalibrationChart(canvas, data.buckets));
      return;
    }
    grid.appendChild(card);
  });
}

function drawCalibrationChart(canvas, buckets) {
  if (!canvas || !buckets) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 200;
  const cssH = canvas.clientHeight || 100;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  // Only draw buckets that have data
  const active = buckets.filter(b => b.count > 0 && b.avg_return != null);
  if (!active.length) return;

  const returns = active.map(b => b.avg_return);
  let yMin = Math.min(0, ...returns);
  let yMax = Math.max(0, ...returns);
  if (yMin === yMax) { yMin -= 0.01; yMax += 0.01; }
  const pad = (yMax - yMin) * 0.15;
  yMin -= pad;
  yMax += pad;

  const barW = (cssW - 20) / 10;  // 10 confidence levels
  const topPad = 12;
  const botPad = 16;
  const chartH = cssH - topPad - botPad;
  const zeroY = topPad + chartH * (1 - (0 - yMin) / (yMax - yMin));

  // Zero line
  ctx.strokeStyle = "#1a2235";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(10, zeroY);
  ctx.lineTo(cssW - 10, zeroY);
  ctx.stroke();

  // Bars
  buckets.forEach(b => {
    if (b.count === 0 || b.avg_return == null) return;
    const x = 10 + (b.confidence - 1) * barW;
    const valY = topPad + chartH * (1 - (b.avg_return - yMin) / (yMax - yMin));
    const barH = Math.abs(valY - zeroY);
    const barTop = b.avg_return >= 0 ? valY : zeroY;

    ctx.fillStyle = b.avg_return >= 0
      ? "rgba(0, 212, 136, 0.65)"
      : "rgba(255, 51, 85, 0.65)";
    ctx.fillRect(x + 2, barTop, barW - 4, barH);

    // Count label above/below bar
    ctx.fillStyle = "#5f6b80";
    ctx.font = `${8 * (dpr > 1 ? 1 : 1)}px JetBrains Mono, monospace`;
    ctx.textAlign = "center";
    const labelY = b.avg_return >= 0 ? barTop - 2 : barTop + barH + 8;
    ctx.fillText(`${b.count}`, x + barW / 2, labelY);
  });

  // X-axis labels (confidence 1-10)
  ctx.fillStyle = "#5f6b80";
  ctx.font = "9px JetBrains Mono, monospace";
  ctx.textAlign = "center";
  for (let i = 1; i <= 10; i++) {
    const x = 10 + (i - 1) * barW + barW / 2;
    ctx.fillText(String(i), x, cssH - 3);
  }
}

// ===== Model Correlation Matrix =====
function renderCorrelationMatrix(d) {
  const container = document.getElementById("corr-content");
  if (!container) return;
  container.innerHTML = "";

  const corr = d.correlation_matrix;
  if (!corr) return;

  if (corr.insufficient) {
    container.innerHTML = `<div class="corr-insufficient">Insufficient data — need 5+ overlapping trading days across all models (currently ${corr.common_days || 0})</div>`;
    return;
  }

  const keys = corr.model_keys || [];
  const matrix = corr.matrix || [];
  const models = d.models || {};
  const n = keys.length;
  if (!n || !matrix.length) return;

  // Short display names for headers
  const shortNames = keys.map(k => {
    const full = (models[k] || {}).display_name || k.toUpperCase();
    // Take first word only if too long, e.g. "Claude Sonnet 4.6" -> "Sonnet"
    const parts = full.split(" ");
    if (parts.length >= 2 && parts[0] === "Claude") return parts[1];
    return parts[0];
  });

  // Build grid: (n+1) columns (header col + n data cols), (n+1) rows
  const grid = document.createElement("div");
  grid.className = "corr-grid";
  grid.style.gridTemplateColumns = `64px repeat(${n}, 56px)`;

  // Top-left empty corner
  grid.appendChild(createDiv("corr-cell corr-header", ""));

  // Column headers
  for (let j = 0; j < n; j++) {
    const cell = createDiv("corr-cell corr-header corr-header-row", shortNames[j]);
    cell.style.color = MODEL_COLORS[keys[j]] || TEXT;
    grid.appendChild(cell);
  }

  // Data rows
  for (let i = 0; i < n; i++) {
    // Row header
    const rh = createDiv("corr-cell corr-header corr-header-col", shortNames[i]);
    rh.style.color = MODEL_COLORS[keys[i]] || TEXT;
    grid.appendChild(rh);

    for (let j = 0; j < n; j++) {
      const v = matrix[i][j];
      const cell = createDiv("corr-cell", v != null ? v.toFixed(2) : "—");

      if (i === j) {
        // Diagonal
        cell.style.background = "var(--bg-row)";
        cell.style.color = "var(--text-dim)";
      } else if (v != null) {
        cell.style.background = corrColor(v);
        cell.style.color = v >= 0.5 ? "#f0f4fb" : v < 0 ? "#f0f4fb" : "#c8d4e6";
      }
      grid.appendChild(cell);
    }
  }

  container.appendChild(grid);

  // Insight line
  const highest = corr.highest;
  const lowest = corr.lowest;
  if (highest && lowest) {
    const hNames = highest.pair.map(k => {
      const full = (models[k] || {}).display_name || k;
      const parts = full.split(" ");
      return parts.length >= 2 && parts[0] === "Claude" ? parts[1] : parts[0];
    });
    const lNames = lowest.pair.map(k => {
      const full = (models[k] || {}).display_name || k;
      const parts = full.split(" ");
      return parts.length >= 2 && parts[0] === "Claude" ? parts[1] : parts[0];
    });
    const insight = document.createElement("div");
    insight.className = "corr-insight";
    insight.innerHTML = `Highest: <strong>${hNames[0]} \u2194 ${hNames[1]} (${highest.value.toFixed(2)})</strong> | Lowest: <strong>${lNames[0]} \u2194 ${lNames[1]} (${lowest.value.toFixed(2)})</strong> | ${corr.common_days} overlapping days`;
    container.appendChild(insight);
  }
}

function createDiv(className, text) {
  const div = document.createElement("div");
  div.className = className;
  div.textContent = text;
  return div;
}

function corrColor(v) {
  // +0.8 to +1.0: dark blue, +0.5 to +0.8: medium blue,
  // 0 to +0.5: gray, negative: red
  if (v >= 0.8)  return "rgba(43, 138, 255, 0.55)";
  if (v >= 0.65) return "rgba(43, 138, 255, 0.38)";
  if (v >= 0.5)  return "rgba(43, 138, 255, 0.22)";
  if (v >= 0.3)  return "rgba(95, 107, 128, 0.25)";
  if (v >= 0)    return "rgba(95, 107, 128, 0.12)";
  if (v >= -0.3) return "rgba(255, 51, 85, 0.15)";
  return "rgba(255, 51, 85, 0.35)";
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
    <div class="health-row"><span class="k">UNIVERSE</span><span class="v">${d.universe_coverage ? `${d.universe_coverage.total_tracked} tracked / ${d.universe_coverage.actively_held} held` : (d.universe?.tickers || []).length}</span></div>
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
  renderTickerTape(d);
  renderMarketBrief(d);
  renderMvpTrade(d);
  refreshMasterChart();
  renderHeroMiniCards();
  renderLeaderboard(d);
  renderConsensusPicks(d);
  initH2HSelectors(d);
  renderHeadToHead(d);
  renderConfidenceCalibration(d);
  renderCorrelationMatrix(d);
  renderCostTracker(d);
  renderHealth(d);
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

// ===== Live ET clock =====
function updateClock() {
  const el = document.getElementById("liveclock");
  if (!el) return;
  el.textContent = new Date().toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "numeric", minute: "2-digit", second: "2-digit",
    hour12: true,
  });
}

updateClock();
updateCountdown();
setInterval(() => { updateClock(); updateCountdown(); }, 1000);

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
