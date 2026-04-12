<p align="center">
  <strong>AUTONOMOUS LLM TRADING LAB</strong><br>
  <em>6 frontier AI models. Identical data. Identical rules. Zero human intervention.</em>
</p>

<p align="center">
  <a href="https://aadisalhotra.github.io/llm-trading-lab"><img src="https://img.shields.io/badge/LIVE_DASHBOARD-000000?style=for-the-badge&logo=github&logoColor=white" alt="Live Dashboard"></a>
  <img src="https://img.shields.io/badge/models-6-2b8aff?style=for-the-badge" alt="6 Models">
  <img src="https://img.shields.io/badge/universe-75_stocks-00d488?style=for-the-badge" alt="75 Stocks">
  <img src="https://img.shields.io/badge/phase-paper_trading-ffd23f?style=for-the-badge" alt="Paper Trading">
  <img src="https://img.shields.io/github/actions/workflow/status/aadisalhotra/llm-trading-lab/intraday.yml?style=for-the-badge&label=pipeline" alt="Pipeline Status">
</p>

---

A 1.5-year research experiment that puts the world's leading frontier AI models head-to-head in fully autonomous stock trading. Each model manages its own $100K portfolio, makes its own buy/sell decisions every 15 minutes during market hours, and is evaluated on the same metrics — returns, risk-adjusted performance, cost efficiency, and whether it can beat a passive SPY benchmark. No human touches the trades. The models sink or swim on their own judgment.

## The Research Question

> When given identical market data, identical constraints, and identical execution infrastructure, which frontier LLM makes the best investment decisions — and can any of them consistently beat a passive index?

This isn't a backtest or a simulation of past decisions. Every trade executes in real time against live market data. The models see the same 75 stocks, the same news headlines, the same technical indicators — and make independent choices. The experiment captures something no academic paper can: how these models actually behave as autonomous agents managing capital over months and years.

## The Lineup

| # | Portfolio | Provider | Model | Cohort |
|---|-----------|----------|-------|--------|
| 1 | **Claude Sonnet** | Anthropic | `claude-sonnet-4-6` | Core |
| 2 | **GPT** | OpenAI | `gpt-5.4` | Core |
| 3 | **Gemini** | Google | `gemini-3.1-pro-preview` | Core |
| 4 | **Grok** | xAI | `grok-4.20-reasoning` | Core |
| 5 | **DeepSeek** | DeepSeek | `deepseek-reasoner` | Core |
| 6 | **Claude Opus** | Anthropic | `claude-opus-4-6` | Expansion |
| — | **SPY** | — | S&P 500 ETF (buy & hold) | Benchmark |

Model lineup is reviewed on the first trading day of each month. Each provider's portfolio always runs on its latest flagship. New providers can join as expansion cohorts but never replace a core slot.

## Three-Phase Structure

| Phase | Period | Capital | Purpose |
|-------|--------|---------|---------|
| **Paper** | Jul 2026 – Dec 2026 | $100K simulated per model | Validate pipeline, collect baseline data, tune risk controls |
| **Live** | Jan 2027 – Jan 2028 | $1K real per model | Real execution with real slippage, fees, and consequences |
| **Scale** | 2028+ (optional) | TBD | Increase capital if results warrant it |

## The 75-Stock Universe

Full GICS sector coverage across U.S. large and mega-cap equities:

| Sector | Stocks | Names |
|--------|--------|-------|
| Technology | 11 | AAPL, MSFT, NVDA, AVGO, ORCL, CRM, ADBE, AMD, INTC, QCOM, CSCO |
| Financials | 9 | JPM, BRK-B, V, MA, BAC, GS, MS, AXP, BLK |
| Healthcare | 9 | UNH, LLY, JNJ, ABBV, MRK, PFE, TMO, ABT, ISRG |
| Consumer Discretionary | 8 | AMZN, TSLA, HD, MCD, NKE, LOW, SBUX, TJX |
| Industrials | 8 | CAT, GE, UNP, HON, RTX, DE, LMT, UPS |
| Communication Services | 6 | GOOGL, META, NFLX, DIS, CMCSA, TMUS |
| Consumer Staples | 6 | WMT, COST, PG, KO, PEP, PM |
| Energy | 5 | XOM, CVX, COP, SLB, EOG |
| Materials | 5 | LIN, APD, SHW, FCX, NEM |
| Real Estate | 4 | AMT, PLD, CCI, EQIX |
| Utilities | 4 | NEE, DUK, SO, D |

## Two-Step Screening Process

With 75 stocks, sending full data for every ticker on every 15-minute tick would be expensive and dilute model attention. The pipeline uses a two-step approach:

1. **Screening call** — All 75 stocks with minimal data per stock (price, daily change, volume ratio, sentiment score, one-line headline). The model returns a JSON shortlist of its top 20 picks with a one-sentence reason for each. Fast, cheap, and captures what each model chooses to focus on.

2. **Trading call** — Full data (OHLCV, RSI, moving averages, MACD, volume, complete news headlines, sentiment, portfolio state) for only the 20 shortlisted stocks. The model outputs buy/sell/hold decisions with confidence scores and reasoning.

Stock order is randomized on every run so no ticker is consistently buried at the bottom of the list. The same random order is used across all 6 models within a single tick for fairness.

## Intraday Trading

The pipeline runs every 15 minutes during NYSE market hours (9:30 AM – 4:00 PM ET) via GitHub Actions cron. Each tick:

- Fetches live 15-minute bars for all 75 stocks
- Runs the two-step screening → trading process for each model
- Executes trades via Alpaca (paper or live)
- Logs decisions, reasoning, confidence scores, and screening shortlists
- Rebuilds the dashboard payload and deploys to GitHub Pages

An end-of-day pass at 5:30 PM ET writes daily performance snapshots, generates research reports, and sends summary alerts.

## Features

**[Live Terminal Dashboard](https://aadisalhotra.github.io/llm-trading-lab)** — Bloomberg-style dark terminal UI with real-time equity curves, model mini-cards, full leaderboard, and live ET clock with countdown to next run.

**Confidence Calibration** — Tracks whether each model's self-reported confidence scores (1–10) actually predict trade returns. Scatter charts per model with a Pearson correlation calibration score.

**Consensus Picks** — Shows which stocks 3+ models agree on. Tracks a Model Agreement Index: do high-agreement trades (4+ models) outperform contrarian bets?

**News Intelligence** — Multi-provider news pipeline (Finnhub → Alpha Vantage → NewsAPI) with VADER sentiment scoring per stock. Models see headlines + sentiment in their trading prompts.

**API Cost Tracking** — Per-model cost breakdown (today / 7-day / month / total), cost per trade, gross P&L vs API spend, ROI visualization, budget cap warnings.

**Monthly Research Reports** — Auto-generated Markdown reports with performance rankings, trade activity, risk events, cohort comparison, cost analysis, consensus analysis, confidence calibration, and screening analysis.

## Leaderboard

<!-- This section updates as the experiment progresses -->
> Leaderboard data populates after the first full trading week. See the [live dashboard](https://aadisalhotra.github.io/llm-trading-lab) for real-time standings.

## Tech Stack

| Layer | Tools |
|-------|-------|
| Language | Python 3.11 |
| Market data | yfinance (daily + intraday bars) |
| News | Finnhub, Alpha Vantage, NewsAPI (with hourly caching) |
| Execution | Alpaca (paper + live) |
| LLM providers | Anthropic SDK, OpenAI SDK, Google GenAI SDK, REST for xAI + DeepSeek |
| Dashboard | Static HTML/CSS/JS, TradingView lightweight-charts, GitHub Pages |
| Scheduling | GitHub Actions cron (every 15 min during market hours) |
| Data format | JSONL trade logs, JSON state files, Markdown reports |

## Project Timeline

```
Apr 9, 2026   Phase A — Paper trading begins ($100K per model)
Oct 9, 2026   Phase A ends — 6 months of paper data
Mid-Oct 2026  Month 6 Paper Trading Final Report
Nov 1, 2026   Phase B — Live trading begins ($1K real per model)
Nov 1, 2027   Phase B ends — 12 months of live data
Nov 2027      Month 18 Final Capstone Report
```

## Running Locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
python -m src.pipeline --intraday --force
```

## Repo Structure

```
config/      Settings, universe, risk parameters
src/         Pipeline, adapters, analytics, dashboard builder, reports
prompts/     Versioned system prompts (v1.txt)
data/        Trade logs, performance snapshots, intraday ticks, state files
reports/     Daily + monthly research reports
dashboard/   Static terminal frontend (deployed to GitHub Pages)
.github/     CI workflows (intraday pipeline, keepalive)
```

---

<p align="center">
  <strong>This is a personal research experiment. Not financial advice.</strong><br>
  All trading is autonomous. All risk is the operator's own.<br>
  Past performance does not predict future results.
</p>
