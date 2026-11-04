# Cutover finding: the mode flip is not one line

**Status:** FINDING + PROPOSAL, for a Research ruling. Nothing here has been executed. `config/settings.json` still reads `"mode": "paper"`, and `data/state/` is untouched.
**Date:** 2026-11-04, evening (after the close).
**Raised by:** Operations, while preparing the cutover the hub dispatched for tonight ("the change is one line: mode from `paper` to `broker_paper`").
**Tree:** `main` at `9c1237708`.
**Flip target:** Thursday 2026-11-05 09:35 ET if a ruling lands tonight, Friday 2026-11-06 otherwise.

## 1. Finding

Flipping `mode` alone is not enough. The first venue-mode session would refuse to load any of the five live-cohort books, so validation day one would run with no venue trading.

The 2026-11-03 cutover-delta entry (`operational_events.2026-11[prompt_cutover_delta_precomputed_2026_11_03].candidate`) said the cutover needs both pieces: "re-inception is the inception-epoch guard plus a human archive of data/state". Tonight's dispatch carried only the first one.

## 2. The guard and what it refuses

`src/portfolio/portfolio.py`:

- `load_portfolio()` calls `_assert_epoch(model_key, path, data)` (line 430) on every existing state file.
- `_assert_epoch()` (line 399) compares the file's stored `inception_epoch` (absent means `"paper"`) with `effective_mode(model_key)` (`src/config_loader.py:206`). On a mismatch it raises `InceptionEpochError` (line 406): *"Books do not carry across a phase boundary: archive data/state read-only and let the run incept fresh at the registered capital…"*
- The `InceptionEpochError` docstring (line 387) gives the reason. The failure it prevents is silent: a leftover Phase A file "loads perfectly well and the run continues with a $100,000 paper book quietly standing in for a freshly funded one." It also says why the step is manual: **"Archiving is deliberately a human step: deleting book state is not something a pipeline should do on its own initiative."**
- `effective_mode()` keeps a book outside `live_cohort_keys` on `"paper"` in a venue mode. That is why DeepSeek is unaffected.

**No archive procedure is registered anywhere.** I searched the integrity ledger, `docs/`, `scripts/` and `src/` for an archive layout, destination or file list. The only references are the guard's own error text and the 11-03 entry's one clause. Both say an archive must happen; neither says how.

## 3. Simulated-flip evidence

The settings were copied in memory with `mode` set to `broker_paper`. No file was written. Each enabled book's stored epoch was then read from `data/state/<key>.json` and checked against `effective_mode`:

| Book | effective mode after flip | stored epoch | cash in file | holdings | result |
|---|---|---|---|---|---|
| claude | broker_paper | paper | 10,751.40 | 15 | **InceptionEpochError** |
| gpt | broker_paper | paper | 7,455.20 | 16 | **InceptionEpochError** |
| gemini | broker_paper | paper | 39,068.56 | 8 | **InceptionEpochError** |
| grok | broker_paper | paper | 22,271.03 | 14 | **InceptionEpochError** |
| claude_opus | broker_paper | paper | 6,034.25 | 18 | **InceptionEpochError** |
| deepseek | paper | paper | 0.03 | 17 | OK (paper-only, unaffected) |

The same simulation also answers the baseline check the hub asked for:

- `live_cohort_keys` → `['claude', 'claude_opus', 'gpt', 'gemini', 'grok']`
- `reconciliation_params` → `{'cash_tolerance_usd': 1.0, 'unallocated_cash_usd': 89999.99}`. This resolves without raising `PendingReconciliationBaselineError`.
- `starting_capital(settings, "broker_paper")` → `2000.0`

On a flip-only Thursday, each cohort model thread would raise at `load_portfolio` (`src/pipeline.py:168`, inside `run_one_model`). That raise is caught at `src/pipeline.py:723` and turned into a CRITICAL "Unhandled error in <model>", one per book (deduped per model per day), and no venue orders are placed. Level-2 reconciliation also loads books, so it would most likely add a CRITICAL "G-BOOK reconciliation could not run" (`src/pipeline.py:764`). This is loud, not unsafe. It would cost the first validation day.

## 4. Proposed archive procedure (written, NOT executed)

All of the following would happen in one session, after the close and before the first venue-mode session.

1. **Precondition.** Run `git status --short`; the tree must be clean. Confirm on `origin/main` that no intraday or probe job is running or queued.
2. **Archive, nothing deleted.**
   ```
   mkdir -p data/state_archive/paper_epoch_2026-11-04
   git mv data/state/claude.json data/state/claude_opus.json data/state/gpt.json \
          data/state/gemini.json data/state/grok.json \
          data/state_archive/paper_epoch_2026-11-04/
   ```
   Untouched: `data/state/deepseek.json` (it stays on the paper epoch), `data/state/handled_boundaries.json` and `data/state/spacing_guard_skips.jsonl`. `git mv` keeps the files' history, and the archived bytes stay identical. Verify with `git ls-files -s` blob hashes before and after.
3. **Flip.** Change `config/settings.json` `"mode": "paper"` to `"broker_paper"`: one line, nothing else.
4. **Sandbox proof before commit.** In a throwaway git worktree of the candidate tree, call `load_portfolio(k)` for each cohort key and assert:
   - `inception_epoch == "broker_paper"`
   - `cash == inception_value == 2000.0`
   - `holdings == {}`

   Also assert `load_portfolio("deepseek")` still loads its paper book unchanged.

   Then re-run `scripts/prompt_cutover_delta.py` against the candidate `settings.json`. It must MATCH all four hashes, because settings changed after the 11-03 pre-compute (tonight's provenance note).
5. **Commit and ledger.** One commit for the archive plus the flip. Then a ledger entry recording:
   - both boundary dates: landing date 2026-11-04, first effective session 2026-11-05 09:35 ET;
   - the gate list with its evidence;
   - the archive file list with blob hashes;
   - 2026-11-05 as the first day of the validation month.

   Push on sign-off.

The archive is the boundary event and must be in place before 09:35 Thursday. The fresh books are created by the pipeline on its first venue-mode `load_portfolio`, not by this procedure.

## 5. Reporting side: what must be checked before or after the flip

Trading is safe. The portfolio stop measures drawdown against the book's own `inception_value` (`src/portfolio/risk.py:370-373`), so a fresh book is judged against $2,000.

Reporting is not. Several readers use a single lab-wide capital anchor taken from `settings["mode"]` and read a performance history that runs back to 2026-04-09. In each item below, *measured* means I computed the figure tonight against the simulated settings; everything else is identified from the code. Research needs to rule on whether this audit **gates** the flip or **follows** it.

**R1. Leaderboard, dashboard and daily-report cumulative return and alpha.** `src/analytics/performance.py:289` computes `cumulative_return = values[-1] / starting_capital - 1`. Here `starting_capital = _starting_capital(settings)` uses the lab-wide mode, so it becomes **$2,000 for every model, DeepSeek included**. Measured on the existing history, with no new data at all:

| Model | Cumulative return now (paper settings) | After the flip (same history) | Alpha after the flip |
|---|---|---|---|
| DeepSeek | 28.38% | **6,319.16%** | 6,306.46 pp |
| GPT | 20.06% | **5,903.11%** | 5,890.41 pp |

This would appear on the first dashboard rebuild after the flip. The dashboard is published on Pages from a public repo. Consumers: `build_leaderboard` (`src/dashboard/build_data.py:1107`), the daily report (`src/reports/daily_report.py`, which imports `compute_metrics` and `build_leaderboard`), and the digest.

**R2. Series discontinuity in the same metrics.** `data/performance/<model>.jsonl` is sliced from `experiment_start_date` (2026-04-09) and continues straight from about $100k paper values into $2,000 venue values. Code inspection (not measured):

- `daily_returns` gains one day of roughly −98%;
- `max_drawdown` reads roughly −98%;
- `sharpe_30d` and `sharpe_90d`, `win_rate` and the streak all mix the two epochs.

**R3. SPY benchmark curve and cost tracker.** `src/dashboard/build_data.py:1123/1152` passes the lab-wide `starting_capital` into `compute_spy_benchmark_metrics`, so the SPY line is rescaled to $2,000 against seven months of paper-scale lines. `_build_cost_tracker` (line 212) computes `gross_pnl_usd = last total_value - starting_capital`. For DeepSeek's paper book that becomes about $126,000 of "P&L".

**R4. Fresh books take April 9 as their inception date.** `init_portfolio` sets `inception_date = settings["experiment_start_date"]` (`src/portfolio/portfolio.py:372`), which is 2026-04-09, not 2026-11-05. Every "since inception" label or window read from the state file would place a Nov 5 book at April 9.

**R5. Monthly data layer.** `scripts/build_monthly_data_layer.py:97` hard-codes `INCEPTION_CAPITAL = 100_000.0`, used at line 2145 (`cumulative = vals[-1] / INCEPTION_CAPITAL - 1`), line 2186 (SPY portfolio value) and line 2530 (`inception_anchor`). The builder never reads `execution_mode`, even though every decision-log row stamps it (`src/logging/decision_log.py:92`). The November layer would therefore:

- mix Nov 2–4 paper rows with Nov 5+ broker-paper rows;
- show the five cohort books near −98% against the $100k anchor.

The registered return denominator for Phase B is `live_cohort_book_count × starting_capital[mode]`, read through `config_loader.registered_capital_base()` (`config/settings.json` capital-structure `_note`). R5 is out of line with that rule.

**R6. End-of-day alert detectors (likely false emails Thursday evening).** Per-row `cumulative_return` is measured against each book's own `inception_value` (`src/portfolio/portfolio.py:208`), so it resets to about 0 for a fresh book. `detect_negative_crossings` (`src/alerts/events.py:721`) compares today's row with the previous one. That previous row is the paper book at roughly +20%, so any small loss on day one reads as a crossing into negative and sends an alert email. Milestones and new-ATH are once-ever or peak-based and would not fire on a reset to 0. Whether a crossing alert is WARN or CRITICAL should be confirmed in that function.

**R7. Reports already published (no change expected).** The July and October PDFs and their data layers are frozen artifacts, and nothing in this flip rewrites them. Confirm that `reproduce-May` (`tests/test_reproduce_may_data_layer.py`) still passes after the flip, since the builder reads `settings`.

**Suggested split for the ruling:**

- **Could gate the flip:** R1 and R6. R1 is public on the first rebuild. R6 sends false alerts on the first unsupervised evening, which is exactly what the alerting work tonight was meant to make trustworthy.
- **Could follow it:** R2, R3, R4 and R5. They affect derived and monthly reporting; the November layer is not built until December.
- **Least invasive fix for R1, R3 and R6, if Research wants them before the flip:** make the capital anchor per book (`effective_mode(model_key)`), and start each venue book's series at its venue inception. This is a code change on the reporting path the night before the flip, so it needs its own ruling.

## 6. What is NOT affected

- Order routing, the cross-book barrier and level-2 reconciliation all key off `effective_mode` and `venue_book_keys`. The equation closes at 10,000.00 + 89,999.99 − 0.18 = 99,999.81 once the five books incept at $2,000 each.
- The rendered prompts. The 11-03 delta showed `mode` is not among the keys the render reads, and step 4 above re-confirms it on the current tree.
- DeepSeek's paper book and its trading.
