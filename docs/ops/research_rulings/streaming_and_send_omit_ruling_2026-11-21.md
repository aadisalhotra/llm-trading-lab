# Two Rulings Before Calibration — Streaming on the Claude Arms, and the Send/Omit Test

- **Status:** Research's rulings of 2026-11-21, on the hub's 14:51 ET dispatch, issued before any calibration run. They are folded into `docs/registrations/cohort_refresh_ruling.md` §5 at the December freeze. Until then this file, at its hash, is the record that the rules preceded the numbers. Zero markers.
- **Seen before ruling, and disclosed:** Operations' pilot of five send/omit pairs per Claude arm on one synthetic prompt, as the hub relayed it. §2.4 says what that pilot does and does not count for.

## 1. The Claude arms stream

**Confirmed.** `claude-sonnet-5-5` and `claude-opus-5-5` are streamed in calibration and in production (hub decision, 2026-11-21), because the provider's SDK refuses a non-streaming request at the calibration cap.

1. **This is the case §6 of `reasoning_allowance_limit_ruling_2026-11-21.md` covers:** a non-streaming path limited by the size of the request. For these two arms the allowance ladder is run from its first rung against the streaming limit.
2. **The six conditions of that ruling's §3 apply:** only the transport changes; it is built and run before the caps freeze; calibration is on the shape used; the timeout is total wall-clock time; a stream without its terminal event is a failed call of its own class; RQ6's repeats use the same shape.
3. **Two additions for a stream.** A stream cancelled at the tick deadline is a `TICK_DEADLINE` failure. A stream that ends in a refusal is a `REFUSAL`. In both cases the text already received is discarded and is never parsed.
4. **The configuration table records it:** the two Claude arms' requests are streamed and the other arms' are not. The asymmetry is transport only and is disclosed as API-specific formatting.

## 2. Evidence that sending the default level equals omitting it

### 2.1 What is being decided, and how much rests on it

Each arm runs at its provider's default level. Sending that level explicitly pins it; omitting it is the default itself. If the two are shown to be the same, the arm sends. **If they are not shown to be the same, the arm omits.** No arm falls back on this test, and no level changes. Only the request field does.

### 2.2 Two routes; either is sufficient

Both require the provider's page for the default, saved with its date.

- **Route A, the API's own report.** The API reports the level in use on each response, and with the level omitted it reports the level that would be sent. This is direct evidence. It is how `gpt-6-astra` is settled.
- **Route B, the paired test** of §2.3, for an arm whose API reports no level.

### 2.3 The paired test

1. **Calibration omits the level.** All calibration runs on the common set are made with the level omitted, which is the provider default itself. Caps and the timeout therefore do not depend on this test's outcome.
2. **The subset is fixed by rule: 39 inputs, one per tick.** Number the ticks of the three calibration sessions 0 to 38 in time order. For tick k take the input of the snapshot at position k mod 5 in the order `claude`, `claude_opus`, `gemini`, `gpt`, `grok`. If that input is absent, take the next snapshot in the order.
3. **Each arm is run once more on each of the 39 inputs with the level sent.** Each such run is paired with the same arm's omit run on the same input.
4. **The measure** is the reasoning-token count of the call, or the output-token count where the provider reports no separate reasoning count. Which is used is stated for each arm before its runs.
5. **The test.** Take the logarithm of sent over omitted for each pair. Equivalence is shown if the 90% confidence interval for the mean, on the t distribution, lies wholly between log 0.80 and log 1.25. That is the conventional two one-sided tests at 5% with the conventional equivalence margins.
6. **Validity.** A pair in which either call failed is dropped. At least 30 pairs are needed. Every response in a counted pair must name the configured model and end normally.
7. **Outcome.** Shown: the arm sends the level. Not shown, or fewer than 30 pairs: the arm omits it, and its cell reads that the provider default applies and that the equivalence of sending was not shown.
8. **Per arm, once.** Each arm is tested separately, with no correction across arms, and the test is not repeated to change an outcome.

### 2.4 The pilot

Five pairs on one synthetic prompt cannot show equivalence and cannot refute it. The pilot is recorded as a pilot and enters no test. Research saw its figures before writing §2.3. The margins are the standard ones and were not set from them.

### 2.5 Timing

The sent runs are made with the calibration runs. The outcome for each arm is recorded before the open of 2026-12-07, when the request shape freezes with the caps.
