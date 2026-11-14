# Identity-Monitoring Capability — Per-Provider Table (v3, Final Corrected State)

- **Artifact:** `docs/registrations/identity_monitoring_table_v3.md` — governing text. Committed per the standing convention; relay citations retire upon landing.
- **Ruled:** 2026-09-11 (per-provider rewrite), amended 2026-09-11 (OpenAI/Anthropic de-collapse; decision-leg scope) and 2026-09-13 (the no-substitution reclassification — see provenance). This artifact is the **final corrected state**; superseded intermediate states are recorded in §5, not restated as current.

## 1. Scope statement (cohort-wide, conditions every row)

Identity telemetry is captured on the **decision leg only**; no screening-call fingerprint exists for any cell, so build identity on the screening leg is unrecorded cohort-wide — every field claim below covers roughly half of each cycle's provider calls. A provider could serve different builds to the two legs undetected. Closing the screening leg is an Operations cost option, not a registered capability.

## 2. Primary detection instrument (all six cells)

**Behavioral-profile monitoring plus provider-announcement monitoring** — the stack available identically to every cell. **No provider identity field in any cell has ever been tested against an actual substitution; none has demonstrated detection power because no detection opportunity has occurred** (the 2026-09-10 DeepSeek event was reclassified: the routing covered `deepseek-v4-flash`, not our pinned `deepseek-v4-pro` — no substitution occurred on our string). Provider fields register as logged corroborants of varying documented strength.

**Tripwire governing rules (registered with the instrument):**
1. **Cross-model control arm (required):** no single-arm behavioral-profile movement registers as event evidence without the cohort cross-section from the same window — a candidate signal must be distinguishable from the cohort's contemporaneous distribution, not merely from its own history. Motivating failure, documented: 2026-09-10, DeepSeek output tok/s +3.37 sd read as substitution corroboration while Grok sat at +4.22 sd the same day with no provider event; the cross-section was in the logs, unexamined. A reading that skips the control arm is not evidence and enters no ledger entry, pre-read, or registration.
2. **Named estimator (required):** profile z-scores compute on the **daily-mean estimator** against a trailing 17-trading-day baseline, stated wherever a z is reported; the per-call estimator differs by ~2.3× in z on the same data. An unlabeled z carries no weight.

## 3. Per-provider table

| Cell | Provider identity fields | Status | Corroborant weight |
|---|---|---|---|
| xAI | `system_fingerprint` populated (`fp_d44b4eeb21`); four-field surface confirmed | Observed; untested against any substitution | Candidate — undemonstrated |
| DeepSeek | `system_fingerprint` populated | Constant across all observations; **never tested against a substitution** (none has occurred on this string in the capture era) | Candidate — undemonstrated; logged descriptor |
| Google | Build-level `model_version` (e.g., `gemini-3.1-pro-002`); no fingerprint | Provider-documented as build-scoped; untested | Strongest *documented* signal; undemonstrated |
| OpenAI | `system_fingerprint` key present, **empty provider-side** — dated, narrow claim: on 2026-09-10, `gpt-5.4-2026-03-05` returned no fingerprint on 13/13 production decision calls while `service_tier` and `finish_reason` populated from the same response objects; no pre-2026-09-10 observations exist (field uncaptured before then) | **Behavioral absence** — field exists in schema, adapter verified correct, re-checkable daily at no cost; possibly model- or tier-specific (re-check, **firms the row**: `data/trades/gpt_2026-09.jsonl` records dated 2026-09-11, `"api_system_fingerprint": null` on 13/13 production decision calls; 2026-09-12 was a Saturday, no session) | None while empty; converts to candidate if it populates |
| Anthropic (×2: Sonnet, Opus) | Model string only | **Structural absence** — no fingerprint, no reasoning counter; the fields do not exist in the Messages schema; not closable by logging or re-checking | None, permanently |

Field retention is cohort-wide as of 2026-09-10 (commits `1a5fee77` DeepSeek, `d6f3d6e9` cohort-wide — both cited; one alone understates scope). Historical scope: fingerprints and `finish_reason` are unrecoverable pre-2026-09-10; the capture era begins 2026-09-10T13:33Z. The sole pre-era observation is the 2026-09-09 three-sample probe (`docs/boundaries/deepseek_v41_substitution_2026-09-10.md` §2.5, "Live API-surface probe — the fields the logs never held", quoted: "Run 2026-09-10T01:13Z–01:20Z against `deepseek-v4-pro`"; "3 samples, 2 successes + 1 connect timeout, then 1 replacement sample."; 2026-09-10T01:13Z is 2026-09-09 21:13 ET), tagged induced; its token counts (559-token prompt vs ~8,440 production) are not comparable to production magnitudes — only the field surface, fingerprint value, and reasoning share transfer.

## 4. Registered residual (uniform) and the conduct principle

Residual, all six cells: an unannounced substitution with an unremarkable behavioral profile, on the decision leg; the screening leg carries the same residual with no corroborants at all. Because no instrument-based gradient has demonstrated power, identity assurance for every cell is **provider-conduct-based**: the determination doctrine is symmetric — a demonstrated silent substitution on any line triggers the same freezability review the DeepSeek line received — even though the corroborants are not. This registration is load-bearing for the Phase B cohort determination (the exploratory-only DeepSeek ruling rests on observed provider conduct, not on observability).

## 5. Provenance — the week's claim-states, each tracked to its evidence date

1. **"Structurally undetectable" (pre-2026-09-09):** string-stable substitution held undetectable by the apparatus. Falsified by a $0.06 probe — the blindness was a logging omission (the adapter discarded provider-set fields on all 941 pre-fix DeepSeek records), not an apparatus limit.
2. **Fingerprint-primary (2026-09-10, withdrawn):** the fingerprint promoted to primary control. Withdrawn when measurement showed one populated-and-tested-looking cell was actually never-moving (one value across a probe point and 13 calls — "didn't move across the substitution" and "never moves" are not separable), one null provider-side, two structurally absent.
3. **Fingerprint-demoted / profile-primary (2026-09-11):** behavioral-profile + announcement promoted as the stack that fired; fingerprints demoted to corroborants.
4. **No-substitution reclassification (2026-09-13):** provider attestation + own telemetry established the 2026-09-10 routing covered `deepseek-v4-flash`, not `deepseek-v4-pro`; the event reclassified to an announced retirement, reversed within days, with no serving change on our string; the "demonstrated non-responsive" DeepSeek row corrected to "never tested"; the tok/s corroboration withdrawn (control-arm failure, rule 1 above). The ledger's original entries stand append-only with `corrects_prior_finding` fragments.
- 2026-10-04: produced as a committed artifact in the relay-only batch (citation-resolution gate).

## 6. Landing instructions

1. Commit at `docs/registrations/identity_monitoring_table_v3.md`.
2. The Forced-Change/Deprecation Exposure table's rows gain one cross-reference line each to their detection cells here (exposure = conduct × observability; the two tables must read consistently).
3. The DeepSeek cohort determination (pre-read 3 / the internal ruling) cites this artifact for its apparatus claims.
4. Clear the two `[CITE-FILL]`s by quotation before deposit assembly.
