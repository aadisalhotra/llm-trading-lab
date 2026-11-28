# Three Rulings Before Calibration — the Grid Check, Calibration Images, Temperature

- **Status:** Research's rulings of 2026-11-21, on the hub's 14:14 ET dispatch, issued before any calibration run. They are folded into the registrations named in each section at the December freeze. Until then this file, at its hash, is the record that the rules preceded the numbers. Zero markers.
- **Basis:** `api_call_timeout.md` §1; `cohort_refresh_ruling.md` rev. 2 §5; `rq6_harness_and_call_input_retention_ruling.md` rev. 3 §2; at origin `7bcf1cc3`, `config/settings.json` (`tick_spacing.min_minutes: 20`, `fill_deadline_seconds: 300`, barrier timeouts 300 and 120), `src/logging/tick_spacing.py:287–327` and `src/logging/decision_log.py:97`; decision rows of 2026-11-20; Operations' figures as the hub relayed them.

## 1. The grid-occupancy check

### 1.1 A finding that comes first

**With the spacing guard as implemented, the strict check cannot pass at any timeout, and it did not pass at the registered 300 seconds.**

The guard withholds a tick that starts less than 20 minutes after the last decision row's timestamp (`tick_spacing.py:317–326`). That timestamp is the time the row is written (`decision_log.py:97`), and a tick's rows are written together at the end of its run: on 2026-11-20 the six arms' rows of each tick fall within 15 seconds of each other, four to five minutes after the tick's start. So the guard measures from the end of the previous run, not from its start and not from when a decision returned.

On a 30-minute cadence that leaves a run ten minutes. A run that takes longer costs every arm the next tick. The barrier and fill bounds alone are 720 seconds. What has protected the grid is that runs are usually short. With reasoning on for every arm, runs over ten minutes become ordinary.

This is Research's reading of the code. Operations confirms or corrects it before the first calibration call. Nothing below chooses a timeout to work around it.

### 1.2 The requirement, and the hub's decision it needs

**A tick that starts on schedule is not withheld because the previous tick ran long.** The check below presupposes this. The guard as implemented does not meet it.

How it is met is the hub's and Operations' decision, and it is made before the first calibration call. Research's recommendation is that the guard measure from the previous tick's start. A second run started within 20 minutes of the first is still stopped, which is what the guard is for, and a backup run still fills a tick that never completed. A change to an admission guard lands and is replayed like any other, before 2026-12-07.

### 1.3 Terms

- **Cadence** is 30 minutes: 1,800 seconds.
- **Execution bounds** are the barrier's registration and release timeouts and the fill deadline: 300 + 120 + 300 = 720 seconds for a venue book.
- **Overhead** is the largest time per tick not spent waiting on a call, the barrier or a fill, taken from the November venue record. Operations states it before calibration; its relayed figures imply about 240 seconds.
- **The decision phase** runs from the tick's start to the last arm's final returned call.
- **D, the decision deadline,** is the cadence minus the execution bounds, minus overhead, minus a 60-second margin, rounded down to 30 seconds. On Operations' figures: **780 seconds** from the tick's start.

### 1.4 (a) The check

It has two parts, and both must hold.

1. **By construction.** D plus the execution bounds plus overhead is less than the cadence. D is defined so that it is.
2. **Observed.** In every validated session, no tick is queued behind the previous tick's run and none is withheld because of it. One such tick goes to the hub before the Go/No-Go.

**It is a strict worst-case check, and the deadline is what makes it passable.** Three call timeouts together with the execution bounds exceed the cadence at 300 seconds, so the conjunction of timeouts cannot be the test. A percentile bound is refused: it accepts that some ticks overrun, and an overrun costs every arm a decision and leaves stops unevaluated for a cycle.

### 1.5 (b) The tick rule

**Admissible, in this form.**

1. **One deadline, D, for the whole cohort,** measured from the tick's start, the same for venue and paper-only books.
2. **A call that has not returned at D is cancelled, and that arm's decision for the tick has failed.** The event class is `TICK_DEADLINE`, distinct from `TIMEOUT` (`api_call_timeout.md` §3).
3. **A parse retry is started only if the deadline has not passed,** and is subject to it like any call. Nothing is predicted. The record keeps the parse failure and what became of the retry.
4. **A deadline failure is a failed decision.** It counts in completeness denominators, is excluded from latency statistics under the outcome-class rule, and is not attempted again later.
5. **Nothing a model is sent changes, and a returned decision executes as before.**
6. **The formula for D is fixed now.** Its inputs are registered parameters and one November measurement. The rule lands with the caps and the timeout and is frozen with them at the open of 2026-12-07.

**Its cost, stated.** The deadline falls on an arm's slowest calls, which are probably its hardest ticks. That is the selection the call timeout already carries, by design: a bounded failure beats an unbounded stall. Deadline and timeout failures are reported per arm and per tick position.

### 1.6 (c) The timeout, and an arm that is too slow

1. **The timeout is the registered derivation, capped at D.** It is the highest successful calibration latency plus 40%, rounded up to 30 seconds, not below 300 and not above D, cohort-wide.
2. **No arm falls back because of the timeout's value.** `cohort_refresh_ruling.md` §5 item 4 is amended to that extent. The deadline makes an overrun impossible, so a slow arm cannot fail the grid for the others. It can only fail its own decisions.
3. **A slow arm falls back by the registered gate.** With `TIMEOUT` and `TICK_DEADLINE` failures counted, an arm whose decision completeness over its validated sessions is below 0.80 fails validation.
4. **Effort is still never lowered.**
5. **The fallbacks are subject to the same deadline.** The slowest decision on the day of this ruling was 296 seconds, on a Phase A snapshot.

**Two levers this ruling does not pull.** A longer deadline could be bought by lengthening the cadence or by shortening the barrier or fill bounds. All are registered parameters. Changing one after calibration would be choosing after the numbers. If the hub wants one changed, that is decided before the first calibration call.

## 2. Chart images for calibration

**Agreed, as a dated exception to `rq6_harness_and_call_input_retention_ruling.md` §2 item 4.**

1. **Scope.** Every chart image sent with a trading call is kept from the open of Monday 2026-11-23 to the close of Wednesday 2026-11-25. The switch-off at that close is in code or configuration, not a manual step, and one ledger entry records the window and the bytes kept.
2. **Purpose.** The calibration of `cohort_refresh_ruling.md` §5 replays retained inputs byte for byte, and an input without its image cannot be replayed.
3. **The calibration set is fixed by this rule.** It is every retained trading-call input of the five Phase A snapshots at every tick of those three sessions, each with its screening call: 195 if every call succeeded. Nothing is selected from it. DeepSeek's inputs carry no image and are not in the set.
4. **RQ6 is unaffected.** On those three days only the two rotation slots are eligible for RQ6, as on every other day. The extra images are calibration material and are tagged as such.
5. **Size.** About 25 MB in all, against the hub's limit of 0.5 GB a year for full retention. The two-slot rotation is unchanged outside the window.

## 3. Temperature and the Astra level

1. **Production sends no temperature to any of the five refreshed arms.** That is the registered design: every model runs at its provider's default sampling temperature (`docs/PRE_REGISTRATION.md`, RQ6). Three arms accept nothing else, and sending a value to the two that do would be an asymmetry and a choice.
2. **The configuration cells** state what the probes found. For `claude-sonnet-5-5`, `claude-opus-5-5` and `gpt-6-astra`: rejected — any value other than 1 returns an error with reasoning on. For `grok-4.7` and `gemini-4-argon`: accepted, with the effect to be established by the December verification as honoured or ignored. If that probe is inconclusive the cell says so.
3. **`gpt-6-astra` is sent `medium` explicitly.** The API reports `reasoning.effort: "medium"` when the level is omitted. That is a default reported by the API, which `cohort_refresh_ruling.md` §5 admits. The dated response is saved as the evidence, and the equivalence of sending and omitting is shown by reasoning-token counts, as for the other arms. The cell says the level is the one the API reports, not one the provider's pages state.
