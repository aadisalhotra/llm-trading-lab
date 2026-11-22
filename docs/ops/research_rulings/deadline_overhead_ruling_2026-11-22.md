# The Decision Deadline's Overhead Input — Re-measured Before the Freeze

- **Status:** Research's ruling of 2026-11-22 on H-RES-06, issued before any calibration run. It is folded into `tick_deadline_and_calibration_rulings_2026-11-21.md` §1 and `cohort_refresh_ruling.md` §5 at the December freeze. Zero markers.

## 1. D on the measured overhead

**The hub's reading is right.** The formula governs, and "D stays at 780" chose option (i) over option (ii). It did not fix a number. On the measured overhead of 174 seconds (99 seconds of pipeline overhead plus up to 75 seconds for the log push), D is 1,800 − 720 − 174 − 60 = 846 seconds, rounded down to **840**. The calibration bound is then the larger of 900 and D + 120: **960 seconds**.

**One condition on the 99 seconds.** Overhead is defined as the time per tick not spent waiting on a call, the barrier or a fill. A tick takes several minutes from pipeline start to commit, so 99 seconds can only be that remainder. Operations states how it was computed, and from which fields, when it reports D.

The edge case of the contingencies ruling §3 is unchanged in substance. The bound on a run is 840 + 720 + 174 = 1,734 seconds, so a run that starts within about 8.6 minutes of its slot cannot cost the next slot.

## 2. Re-measured on the shakedown, before the freeze

**Agreed.** December ticks carry eleven arms, with five reasoning, so the November figure may understate overhead.

1. **Overhead is re-measured on every shakedown tick from 2026-12-01 to 2026-12-04,** by the same definition.
2. **D is recomputed at the close of 2026-12-04** by the same formula, on the largest overhead in November and the shakedown together. Because it takes a maximum over more ticks, D can only fall. The timeout's cap falls with it.
3. **The calibration bound is not re-run.** It was above the old D, so it is above the new one.
4. **If no refreshed arm runs a shakedown,** the eleven-arm load is not observed before the freeze. Operations then measures it on one off-cycle tick at full load in the week of 2026-11-30, and that tick enters the maximum.
5. **From 2026-12-07 D is frozen.** A validated-session tick whose overhead exceeds the figure D was built on goes to the hub, under the observed part of the check. D is not changed during the validation month.

## 3. The refreshed arms are under D in the shakedown

From 2026-12-01 to 2026-12-04 the refreshed arms decide on internal paper while the snapshots trade on the venue path. **Each refreshed arm is under the tick deadline from its first shakedown session,** as it is under the finish-reason rule. A slow refreshed arm must not delay the snapshots' venue ticks or cost them a slot. The snapshots and DeepSeek come under the deadline at the handover, as the hub reads it.

## 4. Classification readings

1. **R includes screening calls,** reported per leg. Agreed: the cap is per call and the same on both legs.
2. **`content_filter` → `REFUSAL`** for `gpt-6-astra` and `grok-4.7`. Agreed: the provider states it withheld output under a content rule.
3. **Gemini `IMAGE_SAFETY` and its siblings → `REFUSAL`.** Agreed, on one condition. Every value mapped to `REFUSAL` is listed by name in the adapter, and none is mapped by pattern. A value that is not listed is `ABNORMAL_FINISH` (finish-reason rule, §3 item 5). The mapping table for all five arms is committed and cited in the configuration verification record.

## 5. Image retention in December

H-RES-06 describes December ticks as carrying full image retention. Under `tick_deadline_and_calibration_rulings_2026-11-21.md` §2, as ratified, full retention runs only from the open of 2026-11-23 to the close of 2026-11-25. Outside that window the two-slot rotation stands, and the switch-off at that close is in code.

If December is meant to keep every image, that is a second exception to the retention ruling and is ruled on its own before it lands. If not, the overhead re-measured under §2 is measured with the rotation in force.
