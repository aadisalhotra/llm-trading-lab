# Cutover surface sweep: one capital, one inception

**Status:** COMPLETE. Operations, 2026-11-05, on the hub's cutover dispatch.
**Question:** which surfaces assume a single lab-wide starting capital or a single inception date, now that five books are $2,000 broker-paper books incepted 2026-11-06 and DeepSeek is a $100,000 paper book incepted 2026-04-09?
**Rule applied:** fix only what gates. Everything else is classified and left.
**Companions:** `docs/registrations/cutover_reinception_finding_2026-11-04.md` (R1 to R7), `docs/registrations/paper_epoch_retirement.md`.

## What moved the line between gating and following

Research's carve-out treats the public dashboard as the one surface that can misrepresent within hours. The sweep found three more that act before anyone reviews them, so they gate too.

1. **The publish pause covers Pages only.** `reports/daily/*.md`, `data/leaderboard/*.json` and `data/dashboard.json` are committed to a public repo by every EOD run, and the digest is emailed. Leaving R1 behind the pause, which the dispatch allowed, would have put figures of about +6,000% in a public file on Friday evening. So R1 is fixed instead of held.
2. **The late EOD runs were not no-ops.** See below.
3. **Rolling memory acts on the first decision.** A report can be corrected afterwards. A model's decision cannot.

## Gating: fixed in this package

| Surface | Assumption | Fix | Commit |
|---|---|---|---|
| `init_portfolio` (R4) | every book incepts on `experiment_start_date` | NYSE calendar: current session if open, otherwise the next | `679d3c7cd` |
| `_month_tag` via `pipeline.py`, `boundary_force_cover.py` | a book's inception date is the experiment's | passes the lab constant, so the cohort stays M8 rather than resetting to M1 | `679d3c7cd` |
| `run_pipeline` EOD path | a re-run is harmless | `_eod_skip_reason` runs ahead of the first book load | `679d3c7cd` |
| `log_daily_snapshot` | the only inception is the lab's | refuses a row that predates the book's own `inception_date` | `679d3c7cd` |
| `compute_metrics`, `canonical_perf_frame` (R1, and the R2 seam in those functions) | one denominator, one series start | `book_epoch()`: each book's own start and `inception_value` | `9ad8a2d79` |
| `alpha_vs_spy` | SPY's window is every book's window | `None` for a re-incepted book until R3 | `9ad8a2d79` |
| `detect_negative_crossings`, `_eod_history` (R6) | the previous row is this book's prior session | sliced to the epoch; a first close is not a crossing | `9ad8a2d79` |
| `digest._daily_pnl_dollars`, `daily_report._daily_pnl` | today minus the previous row | epoch-sliced; a first close is measured from `inception_value` | `9ad8a2d79` |
| `read_recent_decisions`, `read_last_action_per_ticker` | a book's history is the whole trade log | `since=portfolio.inception_date` | `5e1a89636` |
| `tests/conftest.py` | committed mode is the simulator | suite defaults to simulator; book state isolated | `5d73fdd45` |

**R6 disposition: fixed, not suppressed.** One behaviour to know: a book that closes its very first session down never fires `negative_return` for that episode, because the alert needs a crossing between two rows of the epoch. It fires the first time the book crosses from non-negative to negative after that. Registered in the ledger entry.

## Following: left alone, tracked

| Surface | Why it follows | Before |
|---|---|---|
| `build_data._equity_curve`, `_compute_correlation_matrix` and the other `load_eod_history` readers in `build_data.py` (R2) | dashboard only, behind the pause | pause lifts |
| `compute_spy_benchmark_metrics(starting_capital=...)`, `spy_curve` (R3) | dollar scale only: SPY's percentage is unaffected, its `current_value` reads on a $2,000 scale | pause lifts |
| `_build_cost_tracker` `gross_pnl_usd` (R3) | dashboard only, behind the pause | pause lifts |
| SPY return per epoch, restoring cohort alpha (R3) | alpha shows `—` meanwhile, which is honest | pause lifts |
| Leaderboard mixes epochs: DeepSeek since April beside the cohort since 11-06, and ranks across them; the SPY row runs from April | a presentation decision for Research, not a computation error | pause lifts |
| `daily_report` "Days" column reads `1/147`; `settings.phase` still reads "Phase A - Paper Trading" next to `Mode: BROKER_PAPER` | cosmetic | pause lifts |
| `monthly_report.py` (`starting_capital` at lines 472, 490, 532), `build_monthly_data_layer.py` `INCEPTION_CAPITAL` (R5) | not on the pipeline's path; the November layer is not built until December | November layer |
| `research_metrics.py:1203`, `behavioral_summary.py:122` raw history | window-scoped research readers; pre-deposit inventory from Monday 11-09 | inventory |
| Milestones | `mark_milestones` is once-ever per key, so a cohort book never re-fires a level its paper book crossed. A false negative, never a false alert | November layer |

## Correct as they stand

`experiment_start_date` remains right wherever it means the experiment: `digest._day_counter`, `build_data` day counter, `daily_report` day counter, `regime_classifier`, `canonical_spy_series`, and the lab-wide pre-inception check in `log_daily_snapshot`. `risk.py:370` already reads each book's own `inception_value`. `audit.py` and `barrier_probe.py` seed in-memory books. `defab_clean_window.py` `SEED` is a frozen Phase A tool.

## Named, not fixed

`run_one_model` runs `check_position_stops` before its `is_eod` return, so a stop that triggers on the primary EOD pass places a venue order after the close. In paper mode that was a book entry. In a venue mode, `_force_close_via_venue` waits on a terminal state that an after-hours order may not reach, which would read as `stop_unprotected`. It takes a 15% single-name loss at the close to reach this, and it predates this package. Outside the dispatch; flagged for the hub.
