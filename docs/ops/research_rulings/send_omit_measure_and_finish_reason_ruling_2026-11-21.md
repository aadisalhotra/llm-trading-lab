# Two Rulings Before Calibration — the Send/Omit Measure, and Finish Reasons

- **Status:** Research's rulings of 2026-11-21, on the hub's 15:26 ET dispatch, issued before any calibration run. They are folded into `docs/registrations/cohort_refresh_ruling.md` §5 and §6 at the December freeze. Until then this file, at its hash, is the record that the rules preceded the numbers. Zero markers.
- **Amends:** `streaming_and_send_omit_ruling_2026-11-21.md` §2.3 item 4, and the allowance rule of `cohort_refresh_ruling.md` §5 by one floor.
- **Seen before ruling, and disclosed:** that `gemini-4-argon` returned zero thinking tokens on ten calls to one synthetic prompt; the Claude pilot of five pairs; and that `grok-4.7` took 198 to 304 seconds on a hard synthetic prompt. No threshold below was set from them.

## 1. The measure for the send/omit test

**For every arm, the measure is the total output of the call: reasoning tokens plus visible tokens.**

1. **It replaces** "the reasoning-token count, or the output-token count where the provider reports no separate reasoning count". Where a provider's output count already includes reasoning, the measure is that count. Where reasoning is reported separately, it is the sum. Operations states the fields for each arm before its runs.
2. **One measure for every arm,** not a special case for Argon. Any arm whose reasoning is adaptive can return none on an easy input.
3. **Why total output.** It is positive on every completed call, so every valid pair has a ratio. It is what the cap and the timeout act on. And a change of level changes it: reasoning is the larger part of the output on trading inputs, so a level that reasoned half as much, or not at all, would move the total well outside the margins.
4. **What it gives up.** It is somewhat less sensitive than reasoning alone, because the visible part dilutes a difference in reasoning. Research accepts that for a measure that is defined on every call.
5. **A pair in which neither call reasoned stays in the test.** The two settings behaved alike on that input, which is what the test asks.
6. **Reasoning counts are still reported,** beside the test and not in it: for each setting, the median reasoning count and the number of calls with none.
7. **Everything else in §2.3 stands:** the 39 inputs, pairing, the 90% interval within 0.80 to 1.25, at least 30 valid pairs, once per arm.

## 2. A floor on the allowance

**The reasoning allowance is never below 4,096 tokens for an arm whose reasoning shares the output cap.** The allowance is twice the largest reasoning count seen in calibration. A provider that does not guarantee reasoning on every request could show little or none in calibration and then reason on a harder input in production. Zero observed is not zero possible, and unused headroom costs nothing.

## 3. Finish reasons

**One rule for every arm. Operations' mapping for Argon is confirmed under it.**

1. **The raw finish reason is recorded on every response,** whatever class it maps to.
2. **A normal completion is parsed.** Nothing else is.
3. **A response that ended on its output limit** is flagged `decision_truncated`, as now.
4. **`REFUSAL`** is any response in which the provider states that it withheld or stopped the output under a content rule, and any prompt the provider blocked. For `gemini-4-argon` that is a blocked prompt and the finish reasons SAFETY, PROHIBITED_CONTENT, BLOCKLIST, SPII and **RECITATION**.
5. **`ABNORMAL_FINISH`** is every other finish reason, including **OTHER** and any value the adapter does not list. An unknown value is never treated as success.
6. **Both are failed decisions.** They count in completeness denominators, the text received is never parsed or repaired, and they are not retried. Only a parse failure retries (`api_call_timeout.md` §1), and these are classified from the finish reason before the parser runs.

**Why RECITATION is a refusal.** The provider stopped the output under its own content rule and the call returned normally. That is the defining feature of the class. Across arms the question the class answers is how often a provider's rules withheld a decision, and the raw reason kept under item 1 lets recitation be separated when needed.

**Why OTHER is not.** The provider gives no ground. Counting it as a refusal would attribute to content policy something that is not known to be policy. Folding it into a generic error would hide it.

**One thing to watch.** The prompt carries news text. A model that reproduces it could trip a recitation stop, and it would do so on news-heavy ticks. Any `REFUSAL` or `ABNORMAL_FINISH` in a validated session goes to the hub before the Go/No-Go.
