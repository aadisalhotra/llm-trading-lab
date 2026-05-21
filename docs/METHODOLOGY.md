# Methodology

## Research Question
Given identical information, identical constraints, and identical execution infrastructure, which frontier LLM makes the best investment decisions — and can any of them beat a passive benchmark or a non-AI control?

## Experimental Controls

**Identical inputs.** Every model receives the same market data, the same prompt content, the same portfolio constraints, and the same per-model portfolio state. The only differences across providers are API-format adjustments (message structure, system prompt handling).

**Identical rules.** Max 50 positions, max 20% per name, no leverage, no shorting, no options. $100k paper / $1k live starting capital. 50-trade daily cap. Universe is 79 assets across 12 sectors (75 large-cap U.S. equities plus 4 commodity ETFs: GLD, SLV, USO, CPER).

**Identical execution.** All trades route through the same `executor.py`. Paper mode prices off real market quotes; live mode submits real orders to Alpaca.

## Acknowledged Variables (intentional, not bugs)

1. **Provider velocity.** If Anthropic ships an upgrade in August but OpenAI doesn't update until December, that asymmetry is a measured variable, not noise to control.
2. **Native data access.** Grok has live X/Twitter access baked into the model. This is logged and analyzed, not stripped out.
3. **Reasoning style differences.** Each model's reasoning chain is captured in full and analyzed qualitatively in monthly reports.

## Phases

| Phase | Dates | Capital | Purpose |
|---|---|---|---|
| 1: Build | Pre-launch (completed) | $0 | Pipeline + dashboard |
| 2: Test | Pre-launch (completed) | $0 | Dry runs, validate guardrails |
| A: Paper | Apr 9 – Oct 31, 2026 | $100k each | Validate viability (pilot) |
| Paper Final Report | Late Oct 2026 | — | Go/no-go for live |
| B: Live | Nov 1, 2026 – Nov 1, 2027 | $1k each | Real capital |
| Capstone | Nov 2027 | — | Final report |
| C: Scale | 2028+ | TBD | Optional |

## Model Evolution Policy

- **Core 5 are permanent.** Anthropic, OpenAI, Google, xAI, DeepSeek slots never close.
- **Models auto-upgrade monthly.** First trading day of each month, the pipeline checks each provider's flagship and transitions if a newer version exists. Logged with 30-day before/after comparison.
- **Expansion cohort, never replacement.** New providers can join with their own portfolio but never displace a core 5 model. This preserves head-to-head continuity.

## Reporting

- **Daily**: dashboard auto-update
- **Weekly**: snapshot commit
- **Monthly**: full research report in `/reports`
- **Late Oct 2026**: Paper Trading Final Report (go/no-go for live)
- **Nov 2027**: Capstone

## Compliance
Personal investment experiment. Not financial advice. All risk is the operator's own.
