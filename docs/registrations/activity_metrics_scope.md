# Activity-Metric Scope — Inclusive Semantics from the v3 Regime

- **Artifact:** `docs/registrations/activity_metrics_scope.md` — governing text. Committed per the standing convention; relay citations retire upon landing.
- **Ruled:** 2026-09-05 (Research), hub-ratified; the registration line below was delivered inline twice and lost in relay once — this artifact ends that.

## 1. Ruling

The descriptive activity and behavioral metrics — `trade_count`, `buy_count`, `sell_count`, `executed_notional_total`, `reversal_flip_rate`, `trades_per_active_day`, `n_closed_trades`, `avg_hold_days`, `median_hold_days`, `same_day_exits` — **count SHORT/COVER activity from the v3 regime (2026-07-01) forward**. The pre-existing long-only computation was an inherited default predating shorting activation, never a decision; Operations made it explicit at all five call sites before this ruling.

**Principle (the RQ6 principle applied to descriptive metrics):** descriptive metrics describe the deployed configuration's full action vocabulary. Two of the ten were actively misdescriptive long-only, not merely incomplete: `reversal_flip_rate` excluding direction reversals omits the strongest reversal class a book exhibits (long→short), and `same_day_exits` excluding covers omits precisely the stop-forced same-day covers the 10% short stop generates — systematic undercounting, worst for the models that used the capability most.

## 2. Implementation (as ruled and executed)

- **Classification:** output-version event (no registered estimand depends on these metrics); landed before the August 2026 computation cycle, so August's monthlies were the first computed under inclusive semantics.
- **July recompute, dual publication:** the pre-fix long-only July `evidence_metrics` values remain visible, labeled pre-fix-code output, beside the inclusive recomputation. Rides the **output-version ledger, not the consolidated July erratum** — a semantics upgrade to descriptive metrics with the old semantics disclosed, not a correction of a defective computation; the distinction protects the erratum record's meaning.
- **Built-in check:** the four books that never shorted must reproduce exactly under the recomputation (`scripts/phase_a_integrity_ledger.json`, `operational_events["2026-09"]` → `activity_metrics_short_inclusive`, fix commit `ff029fbc`, quoted (JSON value; the ledger spreads the list over several lines): "august_byte_exact_books": ["gpt", "grok", "claude", "claude_opus", "deepseek"]). Dated scope, noted at the 2026-11-14 fill: the four (gpt, grok, claude, claude_opus) had never shorted as of that recomputation (2026-09-11); GPT's first executed SHORT is 2026-10-20 (CRM, `data/trades/gpt_2026-10.jsonl`), so the statement holds for the recomputation, not as a standing property.
- **Definitional mapping** (same-tick sell-and-short as one flip; short hold-days open→cover, symmetric with longs) landed by Operations against the committed metric definitions under the principle; genuinely ambiguous cases escalate rather than resolve silently at a call site.

## 3. Registration line (Tier 2, verbatim)

> **Activity-metric scope.** Descriptive activity and behavioral metrics (`trade_count`, `buy_count`, `sell_count`, `executed_notional_total`, `reversal_flip_rate`, `trades_per_active_day`, `n_closed_trades`, `avg_hold_days`, `median_hold_days`, `same_day_exits`) are computed over the deployed configuration's full action vocabulary — SHORT/COVER activity counts from the v3 regime (2026-07-01) forward; direction breakdowns are carried by the registered short-utilization descriptors. Pre-fix outputs (long-only, an inherited default made explicit 2026-08) are superseded by inclusive recomputation with dual publication (`scripts/phase_a_integrity_ledger.json`, `operational_events["2026-09"]` → `activity_metrics_short_inclusive`). This scope is deliberate and contrasts with RQ1's registered exclusion of SHORT/COVER from its estimand — descriptive metrics follow the configuration; registered estimands follow their registration.

## 4. Landing instructions

1. Commit at `docs/registrations/activity_metrics_scope.md`.
2. The §3 line lands in `docs/prereg/tier2_novel_sections.md` beside the RQ entries, citing this artifact and the output-version ledger entry.
3. Clear the two `[CITE-FILL]`s by quotation before deposit assembly.

## 5. Provenance

- 2026-09-04/05: the builder sweep surfaced the long-only sites as an explicit-but-unruled default; hub dispatched the decision; Research ruled inclusive-from-v3 with the output-version classification and the four-book reproduction check (conditional-until-computed applied prospectively — no delta asserted pre-recomputation).
- 2026-09-0x: hub receipt confirmed; code change dispatched; registration line resent once after relay loss.
- 2026-10-04: produced as a committed artifact in the relay-only batch (citation-resolution gate).
