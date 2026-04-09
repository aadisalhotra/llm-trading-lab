# Autonomous LLM Trading Lab

A 1.5-year research experiment pitting the leading frontier AI models against each other in fully autonomous stock trading. Five models, identical data, identical rules, zero human intervention.

**Live dashboard:** _GitHub Pages link goes here once deployed_

## The Question
When given identical information, identical constraints, and identical execution infrastructure, which frontier LLM makes the best investment decisions — and can any of them beat a passive benchmark?

## The Lineup

| Portfolio | Provider | Current Model |
|---|---|---|
| Claude   | Anthropic | claude-opus-4-6 |
| GPT      | OpenAI    | gpt-5.4 |
| Gemini   | Google    | gemini-3.1-pro |
| Grok     | xAI       | grok-4 |
| DeepSeek | DeepSeek  | deepseek-v4 |
| Benchmark | —        | SPY (passive) |
| Control   | TBD      | non-AI baseline |

The model lineup is reviewed on the first trading day of each month. Each provider's portfolio always runs on its latest flagship. New providers can join as expansion cohorts but never replace a core slot.

## Phases

1. **Build** (Apr–May 2026) — pipeline + dashboard
2. **Test** (May–Jun 2026) — dry runs
3. **Paper** (Jul–Dec 2026) — $100k simulated per model
4. **Live** (Jan 2027–Jan 2028) — $1k real per model
5. **Capstone** (Jan 2028) — full research report
6. **Scale** (optional) — Phase C

## Rules (applied identically to every model)

- Max 10 positions
- Max 20% per name
- No shorting, no leverage, no options
- 30-trade daily cap
- Hard portfolio stop at -30%
- Hard per-position stop at -15%
- Universe: 20 large-cap U.S. equities (`config/universe.json`)

## Stack

- Python 3.x
- yfinance for market data
- Provider SDKs: `anthropic`, `openai`, `google-generativeai`, plus REST for xAI / DeepSeek
- `alpaca-py` for paper + live execution
- Static HTML/CSS/JS dashboard hosted on GitHub Pages
- GitHub Actions for daily scheduling

## Repo Layout

```
config/      settings + universe
src/         pipeline source
prompts/     versioned trading prompts
data/        published JSON (trades, performance, dashboard)
reports/     monthly + milestone research reports
docs/        architecture, methodology, changelogs
dashboard/   public terminal frontend (GitHub Pages)
.github/     CI / scheduling
tests/       unit + integration
```

## Running Locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in keys
python -m src.pipeline
```

## Reports
Monthly research reports are published to `/reports` and linked from the dashboard.

## Disclaimer
This is a personal research experiment. Not financial advice. All risk is the operator's own.
