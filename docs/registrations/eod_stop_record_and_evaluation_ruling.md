# End-of-Day Stops — Record Form, the Administrative Marker, and Where the Stop Rule Is Evaluated

- **Artifact:** `docs/registrations/eod_stop_record_and_evaluation_ruling.md` — governing text. Routes via the hub; nothing here pushes without sign-off. Zero markers.
- **Ruled:** 2026-11-17 (Research), on the hub's dispatch of Operations' finding that the end-of-day pass runs the position-stop sweep and, in a venue mode, can submit a real order with no decision row.
- **Basis:** code read at origin `75d643a`. Operations cites its working tree `b7ba8573d`, which is not on origin; line numbers below are origin's. Every quotation was checked against that tree.
- **Hub rulings this text rests on (2026-11-17):** removing the EOD sweep is executor-class and is rejected; Gate A lands unchanged apart from correcting the statements that EOD places no trades; the guards and the pre-trade venue check extend to EOD with an EOD boundary key, and EOD stop executions are logged.

## 0. What the code does, and what the record shows

1. **The sweep precedes the EOD branch.** `run_one_model` calls `_position_stop_sweep` at `src/pipeline.py:265–266`, before `if is_eod:` at `:277`. The only EOD case that skips it is an aborted valuation (`:265`).
2. **In a venue mode a triggered stop is a real order.** `force_liquidate` routes to `_force_close_via_venue` and `_place` whenever the book is on the venue.
3. **EOD then saves the book and writes no decision row.** The EOD branch records `trades_executed=0`, calls `save_portfolio`, and returns; `log_decision_run` is never reached.
4. **Neither duplicate guard applies at EOD** (`:707`, `:752`, both under `if not is_eod`). Post-cycle reconciliation does run at EOD in broker modes (`:970–974`).
5. **It was named twelve days ago and not ruled.** `docs/registrations/cutover_surface_sweep_2026-11-05.md`, under "Named, not fixed": "a stop that triggers on the primary EOD pass places a venue order after the close … Outside the dispatch; flagged for the hub."

**Base rate: zero, established two ways.** Operations' set difference found every venue order since 2026-11-06 accounted for, so no EOD order has been submitted in the validation window. Research checked the whole record independently: across 11,078 consecutive decision-row pairs since 2026-04-23, all six books, every holding's share count equals the previous row's plus the logged executions (the documented 2026-11-05/06 re-inception excluded). An EOD stop in paper mode would have moved a book with no logged execution. None did. **The EOD stop has never fired in the record, paper or venue.**

## 1. Record form — an administrative row in the book's decision log

**Ruled: the hub's form, with the model-call fields null.** An EOD pass whose stop sweep produces any forced result — executed, rejected, or left unexecuted — writes one row to that book's decision log (`data/trades/{book}_{month}.jsonl`), in the same run and the same commit as the saved book.

**Why the decision log and not a separate file.** Every consumer that replays executions to rebuild a book reads this log; a fill recorded elsewhere would leave a position open in every replay, silently. And the model's memory feed reads this log too: an intraday stop appears in the model's recent decisions, so an EOD stop must appear the same way, or the model's information set would depend on the time of day its stop fired. The prompt template is untouched.

**Fields.**

| Field | Value |
|---|---|
| `record_class` | `"ADMINISTRATIVE"` — present only on administrative rows; its absence means a model-call row |
| `admin_class` | `"EOD_POSITION_STOP"` — a closed set, extended only by registration. `BOUNDARY_ADMIN` (the 2026-10-23 force-cover rows) is its one existing member |
| `provider`, `model_id_configured`, `model_id_returned` | `"administrative"`, `"n/a"`, `"n/a"` — the existing rows' convention, kept so one discriminator covers both eras |
| `cycle_id` | the EOD run's cycle id. This is what accounts for the order under the guard ruling's id test |
| `timestamp`, `date`, `model_key`, `execution_mode`, `prompt_version`, `month_tag` | as on any row |
| **Model-call fields** — `api_success`, `api_attempt`, `api_latency_seconds`, token and cost fields | **`null`.** No model was called |
| `raw_decisions`, `accepted_decisions`, `violations` | empty lists |
| `executions` | the forced results verbatim: order id, `broker_order`, `decision_id` (`{book}-{cycle}-stop-{ticker}`), `stop_unprotected`, `constraint` |
| `stop_evaluation` | per triggered holding: ticker, shares, average cost, the closing mark, the loss; plus `stop_dust_exempt`, as on intraday rows |
| `portfolio_after` | the saved book |
| `overall_reasoning` | a fixed sentence naming the class and stating that no model was called |

**How each estimand treats it.** One rule covers all of them: an EOD stop execution is treated exactly as an intraday forced stop execution, except that its row holds no model call.

- **Model-call populations exclude administrative rows before any statistic is computed:** completeness and G-COMPLETE, latency, cost per decision, RQ1's decision sets and tick alignment, RQ3's confidence legs, RQ6, the spacing guard's "last decided" reference, and the identifier-echo counts. An EOD stop is no decision. It is not a leg, and it is neither a success nor a failure.
- **Execution-replay populations include it:** book conservation, RQ2's average-cost replay, RQ3's closed-trade reconstruction, RQ5's pre-trade weights. The close is administrative and is censored under T2.3: it moves the replayed book and is never a realization, a calibration outcome, or a model trade in RQ5's trade-driven series.
- **The placeholder confidence** on a forced result (`"confidence": 10` in the synthetic decision) is not a stated confidence and never enters a confidence series.
- **`DECISION_RECORD_LOST` does not apply.** That class is a fill with no committed row of either kind. An EOD stop with its administrative row is fully recorded.

## 2. Found while ruling this — the administrative marker fails on the venue

T2.3's censoring is implemented by one predicate, `_is_administrative` (`src/analytics/research_metrics.py:217`): `str(ex.get("order_id") or "").startswith("FORCED_")`. Its docstring says the executor "stamps ``order_id`` as ``FORCED_<reason>``". That is true only in paper mode. On the venue, `_force_close_via_venue` returns `order_id=placement.order_id` — the venue's client order id, `{book}-{cycle}-{seq}-a{n}`.

**So in Phase B the predicate is false for every forced close that executes on the venue**: the EOD stop, every intraday stop, and every drawdown-halt liquidation (which routes through `force_liquidate`). Each would enter RQ2 as the model's own realization and RQ3 as a chosen close. The bias has a direction: stops fire on losses, so realized losses are overstated and the disposition estimate is pushed toward zero or reversed. `src/analytics/intended_vs_expressed.py:64` carries the same test.

**Nothing has been affected yet.** No forced close has executed on the venue; the one forced result in the November logs is the unexecuted dust stop of 2026-11-12. This is a live-phase defect found before the live phase.

**Ruled.**

1. **The administrative marker is a property of the execution, independent of execution mode.** An execution is administrative when the executor created it by forced liquidation. The record already says so in two mode-invariant places: `decision_id` of the form `{book}-{cycle}-stop-{ticker}`, and `decision.reasoning` beginning "Forced liquidation:" (present in every era). The predicate reads those, and keeps the `FORCED_` prefix for paper-era rows.
2. **Going forward the executor stamps an explicit `administrative_reason`** on every forced result. It is a record field; it alters no order (book-record class, no S1 cost).
3. **A test fixture with a venue-mode forced execution** is required, so the predicate is checked against the system that will run and not against the paper form it was written for.
4. **Deadline:** before the November layer builds, and in any case before the first Phase B behavioural computation. It is analysis code and the records carry the markers, so it is not a go-live gate; it is a gate on the first figure that could contain a venue forced close.

## 3. The existing administrative rows report a model call that never happened

The two `BOUNDARY_ADMIN` rows (`gemini`, 2026-10-23T19:11 and 19:36) carry `api_success: true`, `api_attempt: 1`, `api_latency_seconds: 0.0`. Read naively they are two successful zero-second calls: two extra records and two extra successes in October's Gemini completeness, and two zeros in any latency population that covers that day. No published figure contains them — the latest committed monthly layer is July's — so nothing is restated. **Ruled:** they are excluded from model-call populations by the §1 discriminator (`provider == "administrative"`), the logs are not rewritten, and the exclusion lands before the October layer builds. New administrative rows carry nulls (§1) so the question cannot recur.

## 4. The stop rule — where it is evaluated, and what an EOD trigger does

**Registered as run.**

> **Position stop — evaluation points.** The position stop (15% on a long; 10% on a short in the v3 regime) is evaluated by the pipeline at marks. It is not a resting order at the venue. It is evaluated at two points: (1) every admitted intraday tick, per book, before the model is prompted, at that tick's prices; (2) the end-of-day pass, at the session's closing prices, unless the pass is skipped or its valuation is aborted. It is not evaluated between those points, and not on a withheld tick. Its domain is holdings of at least $1 notional at the evaluation mark (`ghost_position_ruling.md` §⑧). A triggered stop submits a market day order for the whole position.

**What an EOD trigger does depends on what the venue does with a market order after the close.** Operations is establishing which case holds. The registration is fixed for each before the answer:

- **(A) The venue rejects it, or (B) accepts it for the next open and our 300 s fill deadline cancels it.** Nothing executes at EOD. The administrative row records the unexecuted result under its own tag, `EOD_STOP_NOT_EXECUTABLE`. The position is evaluated again at the next session's first admitted tick, at that tick's mark, and executes then if the stop is still breached. In these cases the EOD evaluation is a detection, and execution belongs to the open.
- **(C) The order is left open at the venue past the run** — a cancel that fails or is never confirmed. It would fill at the open against a book that has not moved. This must not occur. **Invariant, registered:** no order outlives the run that placed it; the EOD run confirms a terminal state before it commits, and an order still open is a CRITICAL recorded on the row.
- **(D) The venue fills it after hours.** It executes at EOD and is recorded as an executed forced close.

**Three consequences.**

1. **`EOD_STOP_NOT_EXECUTABLE` is not `stop_unprotected`.** In cases A and B the stop fails by design every time it triggers. Raising the lab's most serious alarm for an expected outcome would teach people to ignore it. The in-session `stop_unprotected` keeps its meaning.
2. **A paper/venue difference, disclosed.** A paper book executes an EOD stop as a book entry at the closing price. The DeepSeek arm stays on paper in Phase B, so under A or B its EOD stops execute at the close and the venue books' do not. It joins the registered execution-environment differences for that arm.
3. **Any change is for a registered boundary.** If A or B holds, the EOD evaluation cannot execute, and whether to keep it is a fair question. Removing or deferring it changes what orders a state produces, so it waits.

## 5. Statements that must change in the same landing (CLAUDE.md rule 10)

Four sites say EOD places no trades: `src/logging/boundary_ledger.py:9`; `src/pipeline.py:274–276` ("It must NOT execute any trades."); `src/pipeline.py:697`; `docs/MONITORING.md:212`. The `_is_administrative` docstring (`research_metrics.py:203`) says the executor stamps `FORCED_<reason>`, which holds for paper mode only.

## 6. To confirm (conditional until computed)

1. Which of A–D the venue does with a market day order submitted after the close, for whole and fractional quantities.
2. Whether `_place` confirms the cancel at the fill deadline, or can return with the order still open (case C).
3. What the G-EXEC scorer does today with an intraday forced result. The EOD row takes the same treatment; this ruling does not set a new one.

## Provenance

- 2026-11-05: the cutover surface sweep named the EOD stop as "Named, not fixed … flagged for the hub". No ruling followed.
- 2026-11-17: Operations re-found it while staging Gate A; the hub ruled the three items in the header and dispatched the record-form and evaluation-point questions. Research read the EOD path, the forced-close path and the censoring predicate at `75d643a`; computed the base rate over the whole record; ruled the row form, the estimand treatment and the evaluation points; and found that the administrative marker does not survive venue execution.
