# Architecture

## Overview
The Autonomous LLM Trading Lab is a daily-cadence pipeline that runs 5 frontier LLMs as independent paper/live trading portfolios with zero human intervention.

```
         ┌────────────────────┐
         │  GitHub Actions    │  cron: weekdays 10:00 ET
         │  daily.yml         │
         └─────────┬──────────┘
                   │
                   ▼
         ┌────────────────────┐
         │  src/pipeline.py   │ ── main entry
         └─────────┬──────────┘
                   │
   ┌───────────────┼────────────────────────┐
   ▼               ▼                        ▼
 Market         Portfolio                Model Version
 Data           State (per model)        Checker (1st of mo)
 (yfinance)     /data/state/*.json
   │               │                        │
   └─────┬─────────┘                        │
         ▼                                  │
 ┌───────────────┐                          │
 │ prompt_builder│ ←── prompts/v1.txt       │
 └───────┬───────┘                          │
         │                                  │
         ▼                                  │
 ┌────────────────────────────────┐         │
 │ adapters/  (one per provider)  │ ◄───────┘
 │ Anthropic OpenAI Google xAI DS │
 └───────┬────────────────────────┘
         │ structured JSON decisions
         ▼
 ┌────────────────────┐
 │ portfolio/risk.py  │ enforces hard rules
 └───────┬────────────┘
         ▼
 ┌────────────────────┐
 │ execution/         │ paper or live (Alpaca)
 └───────┬────────────┘
         ▼
 ┌────────────────────┐
 │ logging/           │ decisions + fills
 └───────┬────────────┘
         ▼
 ┌────────────────────┐
 │ analytics/         │ performance metrics
 └───────┬────────────┘
         ▼
 ┌────────────────────┐
 │ dashboard/build    │ writes /data/*.json
 └───────┬────────────┘
         ▼
   git commit + push  →  GitHub Pages rebuilds dashboard
```

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `src/data/market_data.py` | Pulls OHLCV via yfinance for the universe + benchmark. Handles holidays. |
| `src/adapters/` | One adapter per LLM provider with a unified `generate_decision()` interface. |
| `src/prompt_builder.py` | Constructs the universal prompt + per-model portfolio context. |
| `src/portfolio/portfolio.py` | Tracks holdings, cash, valuations. Persisted as JSON. |
| `src/portfolio/risk.py` | Enforces hard rules (max positions, max alloc, stop-losses, daily trade cap). |
| `src/execution/executor.py` | Paper or live execution. Single config switch. |
| `src/logging/decision_log.py` | Append-only JSONL of all decisions and fills. |
| `src/analytics/performance.py` | Sharpe, drawdown, alpha, hit rate, leaderboard. |
| `src/model_versions.py` | Detects + logs provider model upgrades on the 1st trading day each month. |
| `src/alerts/alerter.py` | Hooks for daily summary + risk alerts (email TBD). |
| `src/dashboard/build_data.py` | Generates the JSON consumed by the static dashboard. |
| `src/pipeline.py` | The single entry point that orchestrates one daily run. |

## Data Layout

```
data/
├── trades/                # JSONL per model per day
├── performance/           # daily snapshots per model
├── leaderboard/           # current + historical rankings
├── model_versions/        # provider model transition log
├── state/                 # current portfolio state (cash + holdings) per model
└── dashboard.json         # consolidated payload for the live terminal
```

## Phase Switching
`config/settings.json` → `mode: "paper" | "live"` toggles capital + execution path. No code changes required.
