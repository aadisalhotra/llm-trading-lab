# Methodology

## Research Question
Given identical information, identical constraints, and identical execution infrastructure, which frontier LLM makes the best investment decisions — and can any of them beat a passive benchmark or a non-AI control?

## Experimental Controls

**Identical inputs.** Every model receives the same market data, the same prompt content, the same portfolio constraints, and the same per-model portfolio state. The only differences across providers are API-format adjustments (message structure, system prompt handling).

**Identical rules.** Max 10 positions, max 20% per name, no leverage, no shorting, no options. $100k paper / $1k live starting capital. 30-trade daily cap. Universe is 20 large-cap U.S. equities.

**Identical execution.** All trades route through the same `executor.py`. Paper mode prices off real market quotes; live mode submits real orders to Alpaca.

## Acknowledged Variables (intentional, not bugs)

1. **Provider velocity.** If Anthropic ships an upgrade in August but OpenAI doesn't update until December, that asymmetry is a measured variable, not noise to control.
2. **Native data access.** Grok has live X/Twitter access baked into the model. This is logged and analyzed, not stripped out.
3. **Reasoning style differences.** Each model's reasoning chain is captured in full and analyzed qualitatively in monthly reports.

## Phases

| Phase | Dates | Capital | Purpose |
|---|---|---|---|
| 1: Build | Apr–May 2026 | $0 | Pipeline + dashboard |
| 2: Test | May–Jun 2026 | $0 | Dry runs, validate guardrails |
| A: Paper | Jul–Dec 2026 | $100k each | Validate viability |
| Month 6 Report | Jan 2027 | — | Go/no-go for live |
| B: Live | Jan 2027–Jan 2028 | $1k each | Real capital |
| Capstone | Jan 2028 | — | Final report |
| C: Scale | TBD | TBD | Optional |

## Model Evolution Policy

- **Core 5 are permanent.** Anthropic, OpenAI, Google, xAI, DeepSeek slots never close.
- **Models auto-upgrade monthly.** First trading day of each month, the pipeline checks each provider's flagship and transitions if a newer version exists. Logged with 30-day before/after comparison.
- **Expansion cohort, never replacement.** New providers can join with their own portfolio but never displace a core 5 model. This preserves head-to-head continuity.

## Reporting

- **Daily**: dashboard auto-update
- **Weekly**: snapshot commit
- **Monthly**: full research report in `/reports`
- **Month 6**: Paper Trading Final Report
- **Month 18**: Capstone

## Compliance
Personal investment experiment. Not financial advice. All risk is the operator's own.
