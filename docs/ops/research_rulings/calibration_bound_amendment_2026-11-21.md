# The Calibration Call Bound — Amended to Exceed the Decision Deadline

- **Status:** Research's confirmation of the hub's amendment in H-RES-01, 2026-11-21, issued before any calibration run. It amends `cohort_refresh_ruling.md` rev. 2 §5 item 1 and `calibration_contingencies_ruling_2026-11-21.md` §4.1 item 1, and is folded into both at the December freeze. Zero markers.

## 1. The error

`calibration_contingencies_ruling_2026-11-21.md` §4.1 item 1 said that a call which timed out at the calibration bound of 600 seconds "would be cut in production by the timeout or the deadline", so its unobserved reasoning could be left out of R. That is wrong. The production timeout is capped at the decision deadline D, about 780 seconds, so a call of between 600 and 780 seconds can complete in production. Its reasoning would never have been observed in calibration, and the allowance would not cover it.

## 2. The amendment

**The calibration bound is 900 seconds, or D plus 120 seconds if that is larger.**

1. **The bound exceeds D,** so every call that production could complete is observed in calibration. No single call can complete in production after D.
2. **A call that completes inside the bound is a valid call,** whatever its latency. Its reasoning count enters R and its latency enters the timeout derivation. Where the latency plus 40% exceeds D, the timeout is D.
3. **A call that reaches the bound** is the case §4.1 item 1 describes, and now correctly: it is data, it is not retried, the derived timeout is set to D, and its reasoning count is left out of R, because production could not complete it.
4. **Nothing else in either ruling changes.**
