// LLM Trading Lab — terminal dashboard renderer
// Pulls /data/dashboard.json from the same repo via relative path.
// Dashboard is hosted via GitHub Pages from /dashboard, so the JSON
// lives one level up at ../data/dashboard.json.

const DATA_URL = "../data/dashboard.json";
const REFRESH_MS = 5 * 60 * 1000; // 5 minutes

const MODEL_COLORS = {
  claude:   "#ff9355",
  gpt:      "#5ad1a3",
  gemini:   "#5aa9ff",
  grok:     "#a577ff",
  deepseek: "#ff70bc",
};

function fmtPct(x) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  const v = (x * 100).toFixed(2);
  return (x >= 0 ? "+" : "") + v + "%";
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
function colorFor(x) {
  if (x === null || x === undefined || isNaN(x)) return "neutral";
  if (x > 0) return "pos";
  if (x < 0) return "neg";
  return "neutral";
}

async function loadData() {
  try {
    const r = await fetch(DATA_URL + "?t=" + Date.now());
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } catch (e) {
    console.error("Failed to load dashboard data:", e);
    document.getElementById("system").textContent = "OFFLINE";
    document.getElementById("system").className = "value offline";
    return null;
  }
}

function renderStatus(d) {
  document.getElementById("phase").textContent = d.phase || "—";
  document.getElementById("mode").textContent = (d.mode || "—").toUpperCase();
  document.getElementById("day").textContent =
    d.experiment_day ? `${d.experiment_day} / ${d.experiment_total_days}` : "—";

  // market open: simple weekday check (frontend doesn't know NYSE calendar)
  const now = new Date();
  const dow = now.getUTCDay();
  const hour = now.getUTCHours();
  const isWeekday = dow >= 1 && dow <= 5;
  const inHours = hour >= 13 && hour < 21; // 9:30-16:00 ET ≈ 13:30-20:00 UTC
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
  lb.forEach((row, i) => {
    const tr = document.createElement("tr");
    if (i === 0) tr.className = "rank-1";
    const cfg = (d.models || {})[row.model_key] || {};
    const ret = row.cumulative_return;
    const alpha = row.alpha_vs_spy;
    tr.innerHTML = `
      <td>${row.rank}</td>
      <td><span class="model-name">${row.model_key.toUpperCase()}</span></td>
      <td><span class="model-version">${cfg.model || "—"}</span></td>
      <td class="num ${colorFor(ret)}">${fmtPct(ret)}</td>
      <td class="num">${row.sharpe_30d != null ? fmtNum(row.sharpe_30d) : "—"}</td>
      <td class="num neg">${row.max_drawdown != null ? fmtPct(row.max_drawdown) : "—"}</td>
      <td class="num ${colorFor(alpha)}">${alpha != null ? fmtPct(alpha) : "—"}</td>
      <td class="num">${row.num_positions ?? "—"}</td>
      <td class="num">${row.current_cash_pct != null ? fmtPct(row.current_cash_pct) : "—"}</td>
      <td class="num">${fmtMoney(row.current_value)}</td>
    `;
    tbody.appendChild(tr);
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
                <td class="num">${fmtPct(h.weight)}</td>
                <td class="num ${colorFor(h.unrealized_pl_pct)}">${fmtPct(h.unrealized_pl_pct)}</td>
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
        <span class="k">RETURN</span><span class="v ${colorFor(p.cumulative_return)}">${fmtPct(p.cumulative_return)}</span>
        <span class="k">CASH</span><span class="v">${fmtMoney(p.cash)} (${fmtPct(p.cash_pct)})</span>
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

// Equity chart — handwritten lightweight canvas renderer (no chart libs)
function renderEquityChart(d) {
  const canvas = document.getElementById("equity-chart");
  const ctx = canvas.getContext("2d");
  // Crisp on retina
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 900;
  const cssH = 280;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  canvas.style.height = cssH + "px";
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cssW, cssH);

  const padL = 56, padR = 16, padT = 12, padB = 28;
  const w = cssW - padL - padR;
  const h = cssH - padT - padB;

  const curves = d.equity_curves || {};
  const series = [];
  Object.entries(curves).forEach(([key, points]) => {
    if (!points || points.length < 2) return;
    const base = points[0].value;
    if (!base) return;
    series.push({
      key,
      color: MODEL_COLORS[key] || "#888",
      points: points.map(p => ({ date: p.date, value: (p.value / base) - 1 })),
    });
  });

  // Background grid
  ctx.strokeStyle = "#1c2230";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#6b7585";
  ctx.font = "10px JetBrains Mono, monospace";

  if (series.length === 0) {
    ctx.fillStyle = "#6b7585";
    ctx.fillText("// no performance data yet — first run pending", padL, padT + h / 2);
    document.getElementById("chart-legend").innerHTML = "";
    return;
  }

  // Find y range
  let yMin = Infinity, yMax = -Infinity;
  series.forEach(s => s.points.forEach(p => {
    if (p.value < yMin) yMin = p.value;
    if (p.value > yMax) yMax = p.value;
  }));
  if (yMin === yMax) { yMin -= 0.01; yMax += 0.01; }
  const yPad = (yMax - yMin) * 0.1;
  yMin -= yPad; yMax += yPad;

  // X range = max length
  const maxLen = Math.max(...series.map(s => s.points.length));

  // Y grid lines (5)
  for (let i = 0; i <= 4; i++) {
    const y = padT + (h * i) / 4;
    const v = yMax - ((yMax - yMin) * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + w, y);
    ctx.stroke();
    ctx.fillText(((v * 100).toFixed(1) + "%").padStart(7), 4, y + 3);
  }
  // Zero line
  if (yMin < 0 && yMax > 0) {
    const yz = padT + h * (yMax / (yMax - yMin));
    ctx.strokeStyle = "#3a4254";
    ctx.beginPath(); ctx.moveTo(padL, yz); ctx.lineTo(padL + w, yz); ctx.stroke();
    ctx.strokeStyle = "#1c2230";
  }

  // Plot each series
  series.forEach(s => {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    s.points.forEach((p, i) => {
      const x = padL + (w * i) / (maxLen - 1 || 1);
      const y = padT + h * (1 - (p.value - yMin) / (yMax - yMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  // Date axis labels (first + last)
  ctx.fillStyle = "#6b7585";
  if (series[0].points.length) {
    ctx.fillText(series[0].points[0].date, padL, cssH - 10);
    const last = series[0].points[series[0].points.length - 1].date;
    const m = ctx.measureText(last);
    ctx.fillText(last, padL + w - m.width, cssH - 10);
  }

  // Legend
  const legend = document.getElementById("chart-legend");
  legend.innerHTML = series.map(s => `
    <div class="legend-item">
      <span class="legend-swatch" style="background:${s.color}"></span>
      <span>${s.key.toUpperCase()}</span>
    </div>
  `).join("");
}

async function refresh() {
  const d = await loadData();
  if (!d) return;
  renderStatus(d);
  renderLeaderboard(d);
  renderHealth(d);
  renderPortfolios(d);
  renderTradeFeed(d);
  renderVersionTicker(d);
  renderEquityChart(d);
}

refresh();
setInterval(refresh, REFRESH_MS);
window.addEventListener("resize", () => loadData().then(d => d && renderEquityChart(d)));
