# The Reasoning Allowance When a Provider's Output Limit Is Finite

- **Status:** Research's ruling of 2026-11-21, 12:44 ET dispatch, issued before any calibration run. It is folded into `docs/registrations/cohort_refresh_ruling.md` §5 at the December freeze. Until then this file, at its hash, is the record that the rule preceded the numbers. Zero markers.
- **Applies to:** any confirmatory arm whose reasoning shares the output cap and whose provider limits the output of a request. Raised for `gemini-4-argon` (non-streaming limit 32,768 tokens, per Operations) and for `grok-4.7` if its limit proves finite.

## 1. Terms

- **L** is the provider's output limit for the request shape in use.
- **H** is L minus 16,384: the room left for reasoning once the registered answer budget is reserved.
- **R** is the largest reasoning-token count on the calibration set.

## 2. The ladder

The first rung that holds is the one that applies. Nobody chooses among them.

1. **The rule as written.** If twice R, rounded up to the next 4,096, is no more than H, the allowance is that figure.
2. **Bounded at the limit, with a floor on the margin.** If rung 1 fails and H is at least 1.4 times R, the allowance is H and the cap is L. No request field changes. The configuration table records the arm as reduced-margin and states H divided by R.
3. **A request shape with a higher limit.** If rung 2 fails and the provider documents a higher limit for another request shape, such as streaming, that shape is used, on the conditions of §3. The ladder is then run again from rung 1 against the new L.
4. **The arm fails validation and falls back.** If no rung above holds by the open of 2026-12-07, the arm enters Phase B on its Phase A snapshot. Its effort is not lowered.

## 3. Conditions on a changed request shape

1. **Only the transport changes.** Every field that reaches the model is identical to the non-streaming request. The change is API-specific formatting under the design's own rule, and the configuration table records which arms use it.
2. **It is built and run before the caps freeze:** in the shakedown sessions, and frozen at the open of 2026-12-07 with the caps.
3. **The calibration is re-run on the shape that will be used,** on the common input set.
4. **The call timeout is total wall-clock time,** enforced by the adapter, not time to the first chunk.
5. **A stream that ends without its terminal chunk is a failed call of its own class.** It is never parsed or repaired as a success. The finish reason, the usage counts and the model identifier are read from the stream on every call.
6. **RQ6's repeats use the same shape** as production.

## 4. Why these thresholds

- **The answer budget is the registered quantity; the margin is not.** The 16,384 tokens reserved for the answer are equal across arms by registration. The factor of two is a safety margin of Research's choosing, so a provider limit may reduce it without breaking a registered equality.
- **The floor of 1.4 is the project's own registered headroom.** The call timeout is derived as the highest observed value plus 40% (`api_call_timeout.md` §1). The same headroom over the largest observed reasoning count is the least that is accepted here.
- **Bounding comes before a change of request shape** because it changes nothing that is sent or received. A new transport path in one adapter is a larger risk than a margin between 1.4 and 2.
- **Falling back comes last** because it gives up the refreshed arm for the sake of a margin.

## 5. Two guards

1. **A calibration call that itself ends on the limit** leaves R unknown. Rungs 1 and 2 then fail by construction and rung 3 applies.
2. **The registered test is unchanged.** A response that ends on its budget in a validated session is flagged, is not an estimable decision record, and goes to the hub before the Go/No-Go. On a reduced-margin arm one such response reopens that arm's rung.

## 6. Scope

The ladder applies to every arm, by the same thresholds. It also applies if a provider's non-streaming path limits a request by duration or size in some other way.
