# Four Rulings Before Calibration — Budget Stops, Scope, the Deadline's Edge, and Calibration Contingencies

- **Status:** Research's rulings of 2026-11-21 on the four open items of H-RES-00, issued before any calibration run. They are folded into the registrations named in each section at the December freeze. Until then this file, at its hash, is the record that the rules preceded the numbers. Zero markers.
- **Refers to:** `send_omit_measure_and_finish_reason_ruling_2026-11-21.md` §3 ("the finish-reason rule"); `tick_deadline_and_calibration_rulings_2026-11-21.md` §1; `slot_identity_guard_ruling_2026-11-21.md` §2; `reasoning_allowance_limit_ruling_2026-11-21.md`; `cohort_refresh_ruling.md` rev. 2; `output_budget_equalization_ruling.md` rev. 3 §4.

## 1. A response that ends on its budget

**The hub's reading is confirmed.** Under the finish-reason rule a response that ended on its output limit is a failed decision. It is not parsed, not repaired and not executed. It is flagged `decision_truncated` and counts as a failure in the completeness gate.

1. **Item 2 of the finish-reason rule governs; item 3 only names the class.** "A normal completion is parsed. Nothing else is."
2. **This supersedes `output_budget_equalization_ruling.md` §4 items 2 and 3 for an arm under the rule.** Those items describe a pipeline that parsed the repaired text and executed it, and they remain the description of the record before the rule took effect. The same ruling's §1 already says why they could not stand: a cut response is a corrupted decision, and executing the first part of a rebalance acts on it.
3. **It is not an executor-class change.** The executor is untouched. What changes is whether a response is a decision at all. The refreshed arms' venue-path sessions are counted from 2026-12-07 with the rule in force.

## 2. Scope of the finish-reason rule

**The hub's reading is corrected in one respect.** The rule applies to:

1. **The five refreshed arms,** from each arm's first session.
2. **The five Phase A snapshots and DeepSeek, from the handover of 2026-12-05/06 at the latest.** Until then they are unchanged.

**Why the snapshots are not left out.** A snapshot that is invoked as a fallback trades real capital beside four refreshed arms. It cannot do so under different failure rules, executing repaired fragments where the others execute nothing. And it should be validated in December under the rules it would trade under. `cohort_refresh_ruling.md` §4 item 6 (item 5 before rev. 2) freezes what a snapshot is sent: its reasoning setting and its budget. This rule changes nothing that is sent, and nothing at all on a response that ends normally. The same item already gives the snapshots the cohort-wide timeout.

**DeepSeek follows** so that every arm in a tick is under one set of failure classes. Its series carries a note at the boundary.

**One ledger entry** records the date from which each group is under the rule.

## 3. The deadline's edge case

**Option (i): D stays at 780 seconds, measured from the pipeline's own start.** The edge case is reported through the observed check.

1. **"Start" means one instant throughout:** the moment the pipeline process begins the tick, after the runner's setup. The decision deadline is measured from it, the slot is assigned by it, and the ten-minute start window is tested on it.
2. **The guarantee, restated exactly.** A run whose pipeline starts within about 8.5 minutes of its slot time cannot cost the next slot, on a run bound of 29 minutes and a setup of 2.5. A run that starts between 8.5 and 10 minutes late and then uses its whole bound can. Part 1 of the check in the tick-deadline ruling §1.4 is read with that limit.
3. **Why not 630 seconds.** It would take 150 seconds of decision time from every arm on every tick to insure against a run that both starts more than 8.5 minutes late and exhausts every bound at once. In November the 90th percentile of start delay was 2.83 minutes.
4. **A flag makes the first half of the edge visible.** A run whose pipeline starts more than 8.5 minutes into its slot is flagged on its rows.

## 4. Calibration contingencies

### 4.1 A calibration call that times out or fails

1. **A timeout at the calibration bound of 600 seconds is data.** It is not retried. Its latency is known only to exceed 600 seconds, so the derived timeout is set to its cap, D. Its reasoning count is unknown and is left out of R: a call that long would be cut in production by the timeout or the deadline, and the allowance does not have to cover it.
2. **A failure of provider availability or of the lab's own account is not data about the arm.** That input is run again once, after the batch. If it fails again it is dropped.
3. **Counts of each are reported per arm.**

### 4.2 A calibration call that returns `REFUSAL` or `ABNORMAL_FINISH`

It is not a valid call. It is not retried, it enters neither R nor the latency maximum, and a send/omit pair containing one is dropped. Each occurrence goes to the hub with its raw finish reason. Calibration is not the gate: the arm proceeds, and the completeness gate decides in the validated sessions.

### 4.3 The minimum number of valid calls

1. **An arm needs 100 valid trading calls,** each ending normally, with every one of the thirteen tick positions represented.
2. **Short because of provider or account failures:** the dropped inputs are run again on a later day, up to two further passes, until the arm has 100.
3. **Short because of the arm's own timeouts, refusals or abnormal finishes:** the arm is not calibrated, does not begin validated sessions, and falls back. An arm that cannot complete 100 of 195 real inputs could not pass the completeness gate.
4. **The send/omit test keeps its own minimum** of 30 valid pairs.

### 4.4 What a correction is, during the shakedown

**A correction is the same rule applied to more records. It is never a number set by hand.**

1. **At the close of 2026-12-04 the allowance rule, the ladder and the timeout derivation are run once more** on the calibration calls together with the shakedown's production calls. What they give is what freezes at the open of 2026-12-07.
2. **Because both rules take a maximum, a correction can only raise a cap or the timeout,** never lower one. The timeout stays capped at D.
3. **A shakedown response that ends on its budget leaves R unknown for that arm,** and the ladder's guard applies: the arm moves to the next rung.
4. **An error in the computation,** such as a wrong token field, is corrected by recomputing from the same records, and is documented.
5. **Not open to correction:** an arm's effort level, the formula for D, the start window, the send/omit measure and margins, and the calibration set.
6. **Each correction has a ledger entry** with the value before, the value after, and the record that caused it.
7. **An arm that ran no shakedown** keeps its calibration values.

### 4.5 A shakedown `TICK_DEADLINE`, `REFUSAL` or `ABNORMAL_FINISH`

1. **It is reported to the hub the same day,** with the record.
2. **It is diagnosed as one of two things.** A defect in the pipeline, such as a wrong mapping or a mishandled stream, is fixed before 2026-12-07 and its demonstration is repeated. Behaviour of the model or the provider changes nothing.
3. **Nothing is tuned to avoid a recurrence.** The prompt is frozen at v4. Effort, the deadline and the classes stand.
4. **It counts toward no gate.** Shakedown sessions are not validated sessions. There is no early fallback: the snapshot is running beside the arm in any case, and the gate decides at the Go/No-Go.

## 5. What Research needs with the calibration results

The per-call table for every arm, with its hash: input identifier, leg, setting (sent or omitted), finish reason and class, latency, reasoning tokens, visible tokens, and the model named in the response. Research recomputes R, the ladder rung, the timeout and the send/omit interval from it and records each arm's outcome before the open of 2026-12-07.
