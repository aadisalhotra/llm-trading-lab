// LLM Trading Lab — Bloomberg-style intraday terminal dashboard
// Powered by TradingView's lightweight-charts (loaded as a global from CDN).
// Renders three layers:
//   1. Master chart: SPY candlesticks + 20-SMA + model equity overlays + volume
//   2. RSI(14) panel under the master chart
//   3. Per-model mini line charts (TradingView line series)
// Plus the existing leaderboard sparklines (still hand-drawn canvas) and
// trade/portfolio/version panels.
//
// Data source: ../data/dashboard.json — refreshed every 5 minutes.

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
const ACCENT = "#2b8aff";
const GREEN = "#00d488";
const RED = "#ff3355";
const TEXT = "#c8d4e6";
const TEXT_DIM = "#5f6b80";
const BG = "#05070b";
const GRID = "#1a2235";

const MODEL_ORDER = ["claude", "gpt", "gemini", "grok", "deepseek"];

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
      fontSize: 10,
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
// lightweight-charts wants seconds-since-epoch (UTC) for intraday and a
// date string for daily. We unify on UNIX seconds throughout — daily series
// use the bar's UTC midnight.
function dateToUnix(dateStr) {
  // dateStr is "YYYY-MM-DD"
  return Math.floor(new Date(dateStr + "T00:00:00Z").getTime() / 1000);
}
function isoToUnix(iso) {
  return Math.floor(new Date(iso).getTime() / 1000);
}

// ===== Indicators =====
function computeSMA(values, window) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    if (i >= window - 1) out[i] = sum / window;
  }
  return out;
}

function computeRSI(closes, period = 14) {
  const out = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return out;
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) avgGain += diff; else avgLoss -= diff;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = 100 - 100 / (1 + (avgLoss === 0 ? 100 : avgGain / avgLoss));
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    out[i] = 100 - 100 / (1 + rs);
  }
  return out;
}

// ===== Series builders =====
// Build per-model normalized line series in lightweight-charts format:
//   [{time: unixSeconds, value: pctReturn}, ...]
//
// `mode` is "EOD" (uses equity_curves) or "TODAY" (uses intraday_curves).
function buildModelLineSeries(data, mode) {
  if (mode === "TODAY") {
    const curves = data.intraday_curves || {};
    const out = [];
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
  const tfMap = { "1W": 5, "1M": 21, "3M": 63 };
  const out = [];
  MODEL_ORDER.forEach(key => {
    let points = curves[key];
    if (!points || points.length < 1) return;
    if (state.timeframe !== "ALL" && tfMap[state.timeframe]) {
      points = points.slice(-tfMap[state.timeframe]);
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

// Build SPY candlestick + volume series. We don't have OHLCV in the dashboard
// payload — only a single benchmark price per row — so for the EOD view we
// fake candles by reusing the close as O/H/L/C (a flat tick), and for the
// TODAY view we use the intraday benchmark prices the same way. This still
// gives the visual frame and lets the model overlays sit on a recognizable
// candle chart, even though the wicks/bodies are degenerate. Real OHLCV
// would require expanding the backend payload — phase 2.
function buildSPYCandles(data, mode) {
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
      const tfMap = { "1W": 5, "1M": 21, "3M": 63 };
      if (state.timeframe !== "ALL" && tfMap[state.timeframe]) {
        pts = pts.slice(-tfMap[state.timeframe]);
      }
      prices = pts
        .filter(p => p.benchmark)
        .map(p => ({ time: dateToUnix(p.date), price: p.benchmark }));
    }
  }
  // Build degenerate candles (flat O=H=L=C) — better than no chart
  const candles = prices.map((p, i) => {
    const prev = i > 0 ? prices[i - 1].price : p.price;
    const open = prev;
    const close = p.price;
    return {
      time: p.time,
      open,
      high: Math.max(open, close),
      low: Math.min(open, close),
      close,
    };
  });
  return candles;
}

function buildSMAOverlay(candles, window) {
  if (!candles.length) return [];
  const closes = candles.map(c => c.close);
  const sma = computeSMA(closes, window);
  return candles
    .map((c, i) => sma[i] != null ? { time: c.time, value: sma[i] } : null)
    .filter(Boolean);
}

function buildRSISeries(candles, period = 14) {
  if (!candles.length) return [];
  const closes = candles.map(c => c.close);
  const rsi = computeRSI(closes, period);
  return candles
    .map((c, i) => rsi[i] != null ? { time: c.time, value: rsi[i] } : null)
    .filter(Boolean);
}

// ===== Master chart (TradingView) =====
let masterChart = null;
let rsiChart = null;
let masterSeries = {}; // { spyCandle, sma, rsi, modelLines: {key: series} }

function initMasterChart() {
  const el = document.getElementById("master-chart");
  el.innerHTML = "";
  masterChart = LightweightCharts.createChart(el, chartOptions({
    width: el.clientWidth,
    height: 340,
  }));

  masterSeries.spyCandle = masterChart.addCandlestickSeries({
    upColor: GREEN,
    downColor: RED,
    borderUpColor: GREEN,
    borderDownColor: RED,
    wickUpColor: GREEN,
    wickDownColor: RED,
    priceScaleId: "right",
  });
  // SPY candles use the absolute price scale on the right
  masterChart.priceScale("right").applyOptions({ visible: true, borderColor: GRID });

  masterSeries.sma = masterChart.addLineSeries({
    color: ACCENT,
    lineWidth: 1,
    lineStyle: 0,
    priceScaleId: "right",
    lastValueVisible: false,
    priceLineVisible: false,
  });

  // Model equity overlays go on a SEPARATE % scale on the left so percentages
  // and SPY price live in the same chart without one squashing the other
  masterSeries.modelLines = {};
  MODEL_ORDER.forEach(key => {
    const line = masterChart.addLineSeries({
      color: MODEL_COLORS[key],
      lineWidth: 2,
      priceScaleId: "left",
      lastValueVisible: true,
      priceLineVisible: false,
      priceFormat: { type: "percent", precision: 2, minMove: 0.0001 },
    });
    masterSeries.modelLines[key] = line;
  });
  masterChart.priceScale("left").applyOptions({
    visible: true,
    borderColor: GRID,
    scaleMargins: { top: 0.1, bottom: 0.1 },
  });

  // RSI panel
  const rsiEl = document.getElementById("rsi-chart");
  rsiEl.innerHTML = "";
  rsiChart = LightweightCharts.createChart(rsiEl, chartOptions({
    width: rsiEl.clientWidth,
    height: 90,
    rightPriceScale: { borderColor: GRID, scaleMargins: { top: 0.1, bottom: 0.1 } },
    timeScale: { borderColor: GRID, timeVisible: true, secondsVisible: false, visible: false },
  }));
  masterSeries.rsi = rsiChart.addLineSeries({
    color: "#b478ff",
    lineWidth: 1.4,
    priceFormat: { type: "price", precision: 1, minMove: 0.1 },
  });
  // 30/70 reference lines for RSI
  masterSeries.rsi.createPriceLine({ price: 70, color: RED, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "70" });
  masterSeries.rsi.createPriceLine({ price: 30, color: GREEN, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "30" });

  // Sync the two charts on the time axis so the crosshair lines up
  masterChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range && rsiChart) rsiChart.timeScale().setVisibleLogicalRange(range);
  });
}

function refreshMasterChart() {
  if (!state.data) return;
  if (!masterChart) initMasterChart();

  const mode = state.timeframe === "TODAY" ? "TODAY" : "EOD";
  const candles = buildSPYCandles(state.data, mode);
  const sma = buildSMAOverlay(candles, 20);
  const rsi = buildRSISeries(candles, 14);
  const modelLines = buildModelLineSeries(state.data, mode);

  masterSeries.spyCandle.setData(candles);
  masterSeries.sma.setData(sma);
  masterSeries.rsi.setData(rsi);

  // Reset all model lines, then populate visible ones
  MODEL_ORDER.forEach(key => {
    masterSeries.modelLines[key].setData([]);
  });
  modelLines.forEach(s => {
    if (state.mutedSeries.has(s.key)) return;
    masterSeries.modelLines[s.key].setData(s.data);
  });

  if (candles.length) masterChart.timeScale().fitContent();
  renderLegend(modelLines);
}

function renderLegend(modelLines) {
  const legend = document.getElementById("chart-legend");
  legend.innerHTML = "";
  // SPY swatch
  const spy = document.createElement("div");
  spy.className = "legend-item dashed";
  spy.innerHTML = `<span class="legend-swatch" style="background:${SPY_COLOR}"></span><span>SPY (candle)</span>`;
  legend.appendChild(spy);

  modelLines.forEach(s => {
    const item = document.createElement("div");
    item.className = "legend-item" + (state.mutedSeries.has(s.key) ? " muted" : "");
    item.innerHTML = `<span class="legend-swatch" style="background:${s.color}"></span><span>${s.key.toUpperCase()}</span>`;
    item.addEventListener("click", () => {
      if (state.mutedSeries.has(s.key)) state.mutedSeries.delete(s.key);
      else state.mutedSeries.add(s.key);
      refreshMasterChart();
    });
    legend.appendChild(item);
  });

  // SMA + RSI labels
  const sma = document.createElement("div");
  sma.className = "legend-item";
  sma.innerHTML = `<span class="legend-swatch" style="background:${ACCENT}"></span><span>SMA(20)</span>`;
  legend.appendChild(sma);
  const rsi = document.createElement("div");
  rsi.className = "legend-item";
  rsi.innerHTML = `<span class="legend-swatch" style="background:#b478ff"></span><span>RSI(14)</span>`;
  legend.appendChild(rsi);
}

// ===== Per-model mini charts (TradingView line series) =====
const miniCharts = {};

function renderModelMiniCharts() {
  if (!state.data) return;
  const grid = document.getElementById("model-charts-grid");
  grid.innerHTML = "";
  // Tear down old chart instances to avoid leaks
  Object.values(miniCharts).forEach(c => { try { c.remove(); } catch (e) {} });
  Object.keys(miniCharts).forEach(k => delete miniCharts[k]);

  const mode = state.timeframe === "TODAY" ? "TODAY" : "EOD";
  const lines = buildModelLineSeries(state.data, mode);
  const linesByKey = Object.fromEntries(lines.map(s => [s.key, s]));

  MODEL_ORDER.forEach(key => {
    const series = linesByKey[key];
    const card = document.createElement("div");
    card.className = "mini-chart-card";

    const lastVal = series && series.data.length
      ? series.data[series.data.length - 1].value
      : 0;
    const color = lastVal > 0 ? GREEN : (lastVal < 0 ? RED : TEXT_DIM);

    card.innerHTML = `
      <div class="mc-header">
        <span class="mc-name"><span class="swatch" style="background:${MODEL_COLORS[key]}"></span>${key.toUpperCase()}</span>
        <span class="mc-return" style="color:${color}">${fmtPct(lastVal)}</span>
      </div>
      <div class="mc-sub">${state.timeframe} // vs SPY</div>
      <div class="tv-mini"></div>
      <div class="mc-spy">
        <span>${state.timeframe} window</span>
        <span>${series ? series.data.length + " pts" : "no data"}</span>
      </div>
    `;
    grid.appendChild(card);

    const mountEl = card.querySelector(".tv-mini");
    const chart = LightweightCharts.createChart(mountEl, chartOptions({
      width: mountEl.clientWidth,
      height: 110,
      rightPriceScale: {
        borderColor: GRID,
        scaleMargins: { top: 0.1, bottom: 0.1 },
        visible: false,
      },
      timeScale: { borderColor: GRID, timeVisible: true, secondsVisible: false, visible: false },
      handleScroll: false,
      handleScale: false,
    }));
    const lineSeries = chart.addLineSeries({
      color: MODEL_COLORS[key],
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: "percent", precision: 2, minMove: 0.0001 },
    });
    if (series) lineSeries.setData(series.data);
    chart.timeScale().fitContent();
    miniCharts[key] = chart;
  });

  // SPY benchmark card
  const spyCandles = buildSPYCandles(state.data, mode);
  if (spyCandles.length) {
    const first = spyCandles[0].close;
    const last = spyCandles[spyCandles.length - 1].close;
    const ret = first ? (last / first) - 1 : 0;
    const color = ret > 0 ? GREEN : (ret < 0 ? RED : TEXT_DIM);

    const card = document.createElement("div");
    card.className = "mini-chart-card";
    card.innerHTML = `
      <div class="mc-header">
        <span class="mc-name"><span class="swatch" style="background:${SPY_COLOR}"></span>SPY // BENCHMARK</span>
        <span class="mc-return" style="color:${color}">${fmtPct(ret)}</span>
      </div>
      <div class="mc-sub">${state.timeframe} // S&P 500 ETF</div>
      <div class="tv-mini"></div>
      <div class="mc-spy">
        <span>REFERENCE INDEX</span>
        <span>${spyCandles.length} pts</span>
      </div>
    `;
    grid.appendChild(card);

    const mountEl = card.querySelector(".tv-mini");
    const chart = LightweightCharts.createChart(mountEl, chartOptions({
      width: mountEl.clientWidth,
      height: 110,
      rightPriceScale: { borderColor: GRID, scaleMargins: { top: 0.1, bottom: 0.1 }, visible: false },
      timeScale: { borderColor: GRID, timeVisible: true, secondsVisible: false, visible: false },
      handleScroll: false,
      handleScale: false,
    }));
    const candleSeries = chart.addCandlestickSeries({
      upColor: GREEN, downColor: RED,
      borderUpColor: GREEN, borderDownColor: RED,
      wickUpColor: GREEN, wickDownColor: RED,
    });
    candleSeries.setData(spyCandles);
    chart.timeScale().fitContent();
    miniCharts.spy = chart;
  }
}

// ===== Sparklines (still hand-drawn canvas — small enough to not need TV) =====
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
    div.innerHTML = `<span class="k">${key.toUpperCase()}</span><span class="v">${cfg.model}</span>`;
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
      renderModelMiniCharts();
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
  refreshMasterChart();
  renderModelMiniCharts();
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
    masterChart.applyOptions({ width: el.clientWidth, height: 340 });
  }
  if (rsiChart) {
    const el = document.getElementById("rsi-chart");
    rsiChart.applyOptions({ width: el.clientWidth, height: 90 });
  }
  Object.values(miniCharts).forEach((c, i) => {
    try {
      const el = c.chartElement?.() || c._private__chartWidget?._private__element;
      // lightweight-charts doesn't expose a direct element accessor, so the
      // mini grid is small and we just trigger a fit
      c.timeScale().fitContent();
    } catch (e) {}
  });
});
