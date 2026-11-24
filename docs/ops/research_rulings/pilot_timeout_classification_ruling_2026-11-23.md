# Pilot Timeouts That May Be Connection Failures — the Test, Fixed Before the Count

- **Status:** Research's ruling of 2026-11-23 on H-RES-15, issued before Operations' count. It is folded into `api_call_timeout.md` §3, the pilot's data-integrity section and, if the count requires it, `docs/PRE_REGISTRATION.md` §3.10 at the December freeze. Zero markers.

## 1. Yes, the pilot's timeout figures need a note

The HTTP library reports a failed connection as a timeout. A failure counted as a timeout in the pilot may therefore be a connection that never opened. The note covers three figures.

1. **Rows of class `TIMEOUT`,** which exists from 2026-11-15 (`57c8fd8`).
2. **Rows classified as timeouts from their error text before that date,** including the four xAI calls of `api_call_timeout.md` §2.
3. **The 148 Gemini failures** that `docs/PRE_REGISTRATION.md` §3.10 describes as "server-side deadline/timeout", with the tick-position gradient it reports and its attribution to provider capacity. This one is in the deposit text.

## 2. The test

1. **A provider-returned deadline error is not affected.** If the provider answered with an error saying its own deadline was exceeded, the failure is provider-side by construction, whatever the client reported.
2. **A client-side timeout stands as a timeout only if the call's elapsed time reached at least 90% of the client timeout in force for that call.** That is 120 seconds before the 300-second constant took effect, and 300 seconds after.
3. **A client-side timeout that ended earlier is reclassified as a connection failure.** In the causal decomposition it is a provider or network failure, not a timeout.
4. **A row with no elapsed time recorded is "unresolved"** and is reported as such. It is not assigned to either class.
5. **One count per model and per month,** with the three groups of §1 kept apart.

## 3. What the note changes and what it does not

1. **Completeness figures do not change.** Every failure is counted once in the denominators, whatever its class (the undifferentiated-failure principle).
2. **Latency statistics do not change.** They are computed on successful calls only, and so is the derivation of the timeout constant.
3. **What changes is attribution.** The classes are relabelled in the causal decomposition. The trade log is not rewritten; an annotation joins to it, as for the four xAI rows.
4. **The Gemini paragraph of §3.10** is restated at the freeze only if the count finds connection failures among the 148. It then gives the split and re-reads the gradient and the attribution on the timeouts that remain. If the count finds none, the paragraph stands and the note records that it was checked.

## 4. One more documented instance

`api_call_timeout.md` §3 lists failures absorbed inside an existing error category. This is a fourth instance, the reverse of the first: a connection failure surfacing as a timeout, where the first was a timeout surfacing as a generic error. It is added to that list. The registered consequence is unchanged: failure modes emit classes that can be told apart, which the calibration fix and the production fix of 2026-11-30 implement.

## 5. The market-event detector

Recorded in the execution-environment registration as alert-only. It does not change the news text or anything on the prompt, decision or order path. A reader of the code will otherwise ask whether it alters inputs.
