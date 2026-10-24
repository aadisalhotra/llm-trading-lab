NON-EXPERIMENTAL SIDE PROBE — not part of the LLM Trading Lab record. No portfolio impact. Not in any RQ dataset.

# SPCX Post-Cutoff-Asset Probe

A one-off, out-of-band probe of how the six experiment models react to an asset
that **postdates every model's knowledge cutoff** and is **not in the trading-lab
universe**: the SpaceX IPO (SPCX, Nasdaq, debuted 2026-06-12).

It asks each model — in its normal trading persona, at temperature 0 — what it
*would* hypothetically do with SPCX from a fresh all-cash $100k book, plus a brief
free-text view on the IPO. It is pure content/curiosity and a free pilot of the
post-cutoff-asset question ahead of the **September Phase B decontaminated-IPO
provision**.

## Why this is isolated from the experiment

This probe never touches the trading pipeline, decision logs, portfolio state, or
any RQ dataset. Concretely:

- `run_probe.py` imports **only** shared API-client config, read-only:
  `get_adapter` (the per-model adapters + keys + retry logic) and
  `load_settings` / `load_env` (model snapshots + `.env`). It imports **no**
  pipeline module that reads or writes state.
- The v2 trading persona is read straight from `prompts/v2.txt` (a static template
  file), not through the `prompt_builder` pipeline — so nothing that touches state
  is ever on the import path.
- Every file it writes stays in this directory. Nothing under `data/` or
  `reports/`.

## Probe design

- **Models:** the same six pinned snapshots and per-model adapters as the
  experiment (Claude Sonnet 4.6, GPT-5.4, Gemini 3.1 Pro, Grok 4.20 Reasoning,
  DeepSeek V4 Pro, Claude Opus 4.6), all at **temperature 0**.
- **Prompt:** the verbatim v2 trading-system prompt (role, objective, confidence
  anchors, output discipline) + a clearly-hypothetical probe addendum. The user
  block follows the v2 structure — **market context → portfolio → decision JSON** —
  with a SPCX market-context block built only from public IPO facts as of the run
  date, plus the live SPCX price fetched at runtime via yfinance (or marked
  "first-day price unavailable" — never invented).
- **Portfolio:** identical for all six — the standard $100k seed, 100% cash — so
  reactions aren't confounded by different books.
- **Runs:** each model 3× (18 calls total) — a free RQ6-style non-determinism
  mini-sample on a novel post-cutoff asset.
- **Grok:** given **no** special instruction about X data. If its native X access
  surfaces in the rationale, that is itself the observation.

Each call asks for: (a) the standard decision JSON (action, ticker, size,
confidence, rationale) under the hypothesis that SPCX is tradable; (b) a brief
free-text `ipo_view` on valuation and risks; and (c) an explicit reminder that this
is hypothetical and SPCX is not in the model's real universe.

## Running it

```bash
# from the repo root, with provider keys in .env
python side_experiments/spcx_probe/run_probe.py

# inspect the exact prompt + plan without spending any tokens
python side_experiments/spcx_probe/run_probe.py --dry-run

# a subset / different run count
python side_experiments/spcx_probe/run_probe.py --runs 3 --models claude,grok
```

## Output (this directory only)

- `responses.jsonl` — one record per call: model, snapshot, run index, timestamp,
  full prompt hash, the hypothetical decision JSON, the free-text `ipo_view` and
  overall reasoning, token/cost stats, latency, and the full raw response.
- `prompt_used.txt` — the exact system + user prompt and prompt hash, for
  reproducibility.
- A per-model summary table (action, average size, confidence, 3-run consistency)
  is printed to the console at the end of a run.

Run once, 2026-06-12 22:01-22:09Z (the IPO's first day), 18 calls, $0.76. Left
uncommitted by the session that ran it; landed 2026-10-24 on hub instruction.
In `responses.jsonl` a null `decision` with a `no_trade_reason` is an abstention
(11 of 18); the one failed call is gemini run 1 (invalid JSON after repair).
