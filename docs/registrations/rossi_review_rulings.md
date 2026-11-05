# External-Review Rulings (2026-09-17) — Staleness, Latency, Cadence Apparatus, Memory Continuity, Reasoning-Text Companion

- **Artifact:** `docs/registrations/rossi_review_rulings.md` — governing text. Committed per the standing convention; relay citations retire upon landing.
- **Ruled:** 2026-09-17 (Research), from the external methodological review of the same date (A. Rossi, Georgetown — general feedback, no formal ratification role; no authority, real weight). Latency items corrected 2026-09-2x per Operations' measurement (the ≥590s calls were provider-side timeouts, not slow reasoning). **Dates translated** per `docs/amendments/phase_b_boundary_amendment_2026-12-01.md`: the validation month is **November 2026**; live instrumentation begins at the venue cutover within November.

## 1. Decision-to-fill staleness (registered, phase-distinguished)

*Phase A (pilot):* staleness is **structurally zero** — simulated fills price from the same per-tick price dictionary that marks held positions (verified across 7,383 legs to float precision); there is no venue in the loop and no decision→fill gap exists to measure. The Phase A figure is a property of the simulator, not a market observation, and cannot be cited as a clean bill of health.

*Phase B (live):* staleness is a real, measured property of the execution environment. Registered measurement, beginning with **November broker-paper data**: per-model, per-stage latency distributions (fetch→decision, decision→submission, submission→fill) with tails; realized staleness cost per order (fetch→fill price drift, absolute and relative to the inter-tick move); the staleness-to-grid ratio. Two instrumentation fields exist only from the cutover (fetch-start timestamp; screening-call latency) and are named so the registration commits to the **live measurement**, not to a pilot quantity that is zero by construction.

*Why disclosure, not design change:* behavioral estimands condition on the decision-time information set — the price snapshot and news-payload hash pin what each model saw — and all six models share the same fetch snapshot per tick, so common staleness cancels from every cross-model comparison. The non-cancelling component is per-model reasoning latency (§2), a model-intrinsic variable documented, not equalized.

*Comparability consequence:* paper-phase fills are staleness-free; live fills are not — a registered component of the paper/live execution-environment difference, beside settled funds and the fill deadline.

## 2. Per-model decision latency (disclosed experimental variable — normal-call basis)

Latency statistics are computed on **attempt-1, non-timeout calls only**; failed calls (no usage metadata) are a separate population registered under provider availability, never mixed into latency characterization (**outcome-class conditioning rule** — distributional claims condition on outcome class before characterization). Per-model normal-call distributions: `[CITE-FILL: Operations' attempt-1 non-timeout p50/p90/p99 table, healthy Aug–Sep baseline]`. The asymmetry is model-intrinsic, documented and not equalized; each model's normal-call reasoning time is a floor on its own live fills' staleness relative to the shared snapshot. November's chain-time measurement reports per-arm worst-case grid occupancy on this basis.

**Provider-availability event (Gemini line, filed with the completeness history):** April–May 2026 transport-level failure epidemic — 504 deadline-exceeded (108), 503 high-demand (31), 499 cancelled (11); 151 of 152 ≥590s calls carry no usage metadata — against the `google-genai` transport's 600s RPC deadline, up to 31% of a month's Gemini ticks at peak, improving from June, zero events Aug–Sep. No repository change correlates; most plausible cause is provider-side serving capacity on a preview-tier model — outside our control, recurrable without warning. Distinct from the July truncation signature (generation-level, with usage metadata) and from the budget mechanism (T1.4): three failure modes, three mechanisms, one arm, kept separate. `[VERIFY: the ≥590s failures are already counted in the April–May completeness denominators — one count, this registration adds causal decomposition]`

**Thinking-share characterization:** on normal calls, ~86% of Gemini's response is reasoning (median 3,923 thinking / 614 answer tokens, 16384-budget era) — consistent with the shared-budget architecture; registered in the per-model configuration disclosures. `[VERIFY (dispatched): pre-fix successful-call thinking distributions — whether 4096 − median(thinking) ≈ 173, which would close T1.4's mechanism to the token]`

## 3. Cadence apparatus (the registered objects from the cadence defense)

The 30-minute cadence stands (defense in the paper's methods; the nesting argument — the 30-minute grid contains the daily grid — plus the estimands that need the grid, RQ5's path dependence and RQ2's realization-timing resolution). Two registered objects:

1. **ESS reporting requirement:** the block-bootstrap-implied effective sample size is published alongside every RQ1 estimate — nominal tick counts never masquerade as independent information.
2. **Downsampled RQ1 companion (registered robustness analysis):** RQ1 recomputed on end-of-day decisions only, pre-registered as a companion — answering "would daily cadence change the findings" with a number.

## 4. Memory context and entry attribution

**Memory-context enumeration (registered, verbatim-from-code):** the memory/state context is a treatment variable we author. Registration stub, cleared only by quotation: *"Each decision prompt includes the model's current portfolio state and accumulated context: `[VERIFY AGAINST CODE: enumerate the memory/state fields from the committed context builder — positions with entries, cash, recent-decision window, P&L representation, and the short-position visibility status per the registered limitation]`."* No paraphrase lands.

**Entry-attribution rule (registered):** position-level, outcome-dependent behavioral measures (disposition; calibration's realized leg) attribute a position to the model regime that opened it. Primary estimands include only positions opened **and** closed within one regime; cross-regime positions — entered by one reasoner, resolved by another — are excluded from primary estimands and reported as a disclosed secondary (behavior toward inherited positions). Same family as the censoring principle (T2.3): an inherited entry was not the current reasoner's choice, so realization timing relative to it is not that reasoner's disposition. Structural note: the Phase B design never routes memory to new models — the cohort refreshes at the boundary with fresh re-inception (no positions or history carries) and freezes for the window; this rule is the registered handling for any forced mid-window change the contingency must absorb.

## 5. Reasoning-text exploratory companion (registered exploratory; not RQ7)

Named in the deposit, scoped, **outside the confirmatory FDR family**, pipeline built post-deposit: per-decision reasoning text analyzed for citation behavior — what models cite (checkable against the news-payload hash), whether citation predicts action, and whether models citing shared content converge more, decomposing RQ1's excess concordance by shared-information citation (mechanism analysis: convergence via common salience vs despite divergent stated bases). Registered fence: reasoning text is **self-report, not mechanism ground truth** (cf. CLQT's stating-vs-doing coherence gap); the companion measures stated-information behavior and its correlation with action, never asserting citations caused decisions. Not confirmatory because the extraction pipeline has never run (the RQ4 pilot-first principle) and the family does not grow weeks before deposit.

## 6. Landing instructions

1. Commit at `docs/registrations/rossi_review_rulings.md`.
2. §1–§2 land in the execution-environment and per-model disclosure sections; §3's two objects amend the RQ1 entry; §4's rule and stub land beside T2.3 and the RQ2/RQ3 entries; §5 lands as the exploratory-companion registration.
3. Clear the three `[VERIFY]`/`[CITE-FILL]` markers by quotation before deposit assembly (the citation-resolution gate).

## 7. Provenance

- 2026-09-17: external review (four threads); same-day rulings — staleness as registered environment property; cadence defended with the PI's more-observations argument explicitly conceded as wrong and replaced; reasoning-text assessed exploratory; memory/continuity three-parter ruled.
- 2026-09-2x: latency thread corrected twice on Operations' measurements — the ≥590s population reclassified from slow reasoning to provider-side timeouts (the "consumes a third of the grid" characterization withdrawn before landing; outcome-class conditioning rule adopted), and the `[VERIFY]` on budget-induced latency resolved to neither branch (provider availability).
- 2026-09-30: validation-month dates translated by the boundary amendment.
- 2026-10-04: produced as a committed artifact in the relay-only batch (citation-resolution gate).
