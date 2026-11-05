# API_CALL_TIMEOUT_SECONDS = 300 — Registered Constant, and the Undifferentiated-Failure Principle

- **Artifact:** `docs/registrations/api_call_timeout.md` — governing text. Committed per the standing convention; relay citations retire upon landing.
- **Ruled:** 2026-09-17 (registration, on Operations' inputs); the companion principle (§3) registered the same exchange. **Dates translated:** the grid-occupancy check runs in the **November 2026** validation month per `docs/amendments/phase_b_boundary_amendment_2026-12-01.md`.

## 1. The registered constant

> **Client-side API call timeout (registered constant).** `API_CALL_TIMEOUT_SECONDS = 300`, cohort-wide, enforced in `src/adapters/base.py` inside each adapter's `_call_api`, covering both the screening and decision legs. **Derivation, registered:** 300s clears the highest observed successful-call latency in the stable Aug–Sep baseline (214.1s across all six models) with ~40% headroom; halves the undocumented 600s provider transport default that previously bounded these calls; and equals `DEFAULT_FILL_DEADLINE_SECONDS = 300`, giving the system **one** registered "too long to wait" constant across the LLM-call and broker-fill boundaries rather than two that could drift independently. **Semantics:** a timed-out call routes through the fail-fast branch and returns immediately — one 300s stall per call, never compounded across attempts; only parse failures retry. A timed-out call is a failed decision leg, counted in completeness denominators, excluded from latency statistics per the outcome-class conditioning rule. **Grid bound:** worst case per model per tick is 300s × 2 legs = 600s — 50% of the 20-minute spacing budget from a single arm — registered as the binding input to November's per-arm grid-occupancy check.
>
> **Derivation caveat (owned tradeoff):** the 214.1s baseline maximum is drawn from a window in which the Gemini provider-availability event class (registered, recurrable without warning) was dormant. Under recurred provider degradation, the timeout trades possibly-completable slow calls for bounded grid cost and clean failure accounting — **by design**: a bounded failure beats an unbounded stall, and the completeness gate and occupancy check surface any recurrence immediately, in the right category.

## 2. The xAI clipping record (the empirical case for cohort-wide)

Four legitimate xAI calls were truncated at the prior 120s ceiling — 2026-04-24, 2026-08-20, 2026-08-25, 2026-09-17 — invisible as a class until queried directly against `api_error` text, because a client-side timeout surfaced as an ordinary adapter error (`Read timed out. (read timeout=120)`) with no distinct flag. Retro-correction: the four reclassify from generic adapter error to client-timeout truncation in the causal decomposition; counts in completeness denominators do not change (one count, corrected attribution) `[VERIFY: Operations confirms the four sat under the corrected cause label in the error/availability registrations]`.

## 3. The undifferentiated-failure limitation (registered apparatus principle)

> An instrument that fails *inside an existing error category* is undetectable by the apparatus watching that category. Documented instances, one month: (i) client-side timeouts surfacing as generic adapter errors (the §2 record); (ii) a behavioral tripwire reporting success while measuring nothing; (iii) a scheduled-task registration echoing success while no task existed. The common shape: the failure mode's signature is absorbed by a category the monitoring already tolerates, so no counter moves and no alarm exists to fire. **Registered consequence:** instruments and infrastructure emit **distinguishable event classes for their own failure modes** — a timeout is a `TIMEOUT` event, not a generic error; a tripwire that cannot compute emits `MEASUREMENT_FAILED`, not silence; a registration that cannot verify emits a verification failure, not an echo. Monitoring that watches categories can only see what the categories separate. `[CITE-FILL: the timeout-event differentiation commit, per the Operations dispatch]`

Cross-reference (one-directional, this artifact → the integrity framework): this principle is the monitoring-layer sibling of the estimability taxonomy — the taxonomy classifies data known to be damaged; this principle governs how damage becomes knowable at all.

## 4. Landing instructions

1. Commit at `docs/registrations/api_call_timeout.md`.
2. §1 lands in the execution-environment registration beside the fill deadline and spacing minimum; §3 lands in the data-integrity framework (T1.2-adjacent) with the cross-reference sentence.
3. Clear the `[VERIFY]` and `[CITE-FILL]` by quotation before deposit assembly.

## 5. Provenance

- 2026-09-17: Operations supplied the value, derivation, and bound; Research registered them with the recurrence caveat added; the xAI class finding generalized to the §3 principle on the hub's class-level framing, with the event-differentiation follow-up dispatched to Operations.
- 2026-09-30: grid-occupancy check translated to November by the boundary amendment.
- 2026-10-04: produced as a committed artifact in the relay-only batch (citation-resolution gate).
