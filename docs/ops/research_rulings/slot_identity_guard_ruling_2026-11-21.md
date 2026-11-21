# The Spacing Guard Keyed on Slot Identity — What It Must Also Guarantee

- **Status:** Research's ruling of 2026-11-21, on the hub's 14:31 ET decision that the spacing guard keys on scheduled-slot identity. It supplements `tick_deadline_and_calibration_rulings_2026-11-21.md` §1.2 and is folded into the execution-environment registration at the December freeze. Zero markers.
- **Correction recorded.** §1.2 of that file recommended measuring from the previous tick's start. The hub refused it, correctly: runs are serialized, so a backup run queued behind a long run can start more than 20 minutes after the first run's start and decide a second time for the same slot. Research had not considered the queue.

## 1. Does slot identity meet §1.2's requirement

**Yes, on two conditions.** The requirement is that a tick starting on schedule is never withheld because its predecessor ran long. A guard that asks "has this slot been decided" and not "how long ago did the last run end" meets it. The hub's R1 to R4 are confirmed. The conditions:

1. **A run's slot is the slot in which the run starts, not the slot that triggered it.** A run triggered for 10:00 that starts at 10:31 is a 10:30 run. If the trigger decided the slot, that run would decide "for 10:00" on 10:31's information, and the 10:30 run would follow it minutes later.
2. **A slot is taken once any model-call record exists for it, for any arm.** "Completed tick" is too narrow. If a run stops after some arms have decided, a second run in the same slot would give those arms a second decision, and would give the rest a different information set from the first. A withheld-tick record does not take the slot.

## 2. Is a minimum gap between consecutive slots' starts also needed

**Yes. It is obtained by a start window, not by a second gap test.**

**The rule.** A run decides for a slot only if it starts within the first 10 minutes of that slot. Ten minutes is the cadence minus the registered spacing minimum, 30 − 20. A run that starts later in the slot decides nothing; the slot is recorded once as missed, with its cause.

**What it guarantees, by construction.**

1. **Consecutive ticks start at least 20 minutes apart.** The latest start in one slot is 10 minutes in, and the earliest start in the next is 30. The registered spacing minimum keeps its value and its meaning, start to start.
2. **The hub's example cannot occur.** A 10:00 run that starts after 10:10 is not a 10:00 tick.
3. **R2 still holds.** The decision deadline bounds a run at under 29 minutes. A run that starts at the edge of its window, 10 minutes in, ends by 39; the next run, queued behind it, starts by 9 minutes into its own slot and is admitted. Delay does not accumulate.

**No test against the previous run's end is added.** That test is what withheld on-schedule ticks.

**What changes for a late backup.** Today a backup that starts 15 minutes late fills its slot and the guard then withholds the next one. Under this rule the late slot is missed and the next runs on time. The count of ticks is the same and the grid is kept.

**Why the grid matters.** A tick is the unit of RQ1's pair-tick observations, of the tick-position strata and of RQ6's slot rotation. Two ticks minutes apart are two observations of nearly the same information set, and a tick decided late carries one slot's label on another slot's information.

## 3. Three consequences

1. **Every decision row carries its slot identifier.** From the guard's landing, tick position in every analysis is the slot identifier and is not inferred from timestamps.
2. **A missed slot counts as a missed decision for every arm,** as a withheld tick does now.
3. **The end-of-day pass keeps its own key** and is outside this rule.

## 4. Cases to add to the replay (R4)

1. A long run followed by an on-schedule run.
2. A backup queued behind a long run in the same slot.
3. Runs starting 9 and 11 minutes into a slot.
4. A primary run that starts more than 10 minutes late, followed by the next slot on time.
5. A run that stops after some arms have decided, followed by a backup inside the window.
6. The first tick of a session, the last, and the last tick of an early close.
7. A run at the edge of its window that takes the full bounded duration, followed by the next two slots.
