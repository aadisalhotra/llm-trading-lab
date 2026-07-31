# Monthly Report Data-Layer Schema (FIXED)

**Status:** canonical target for the monthly `data_layer.json`. `schema_version: 1.0`.
First record: `reports/monthly/2026-05/data_layer.json` (back-filled by
`scripts/backfill_may_data_layer.py`). **Uncommitted** pending sign-off.

## Why this exists

The quarterly report aggregates three monthly `data_layer.json` files by a
**mechanical diff** that assumes every month carries the **same fields**,
populated or explicitly `null` (populate-or-null) — never added/dropped per
month. This file is the fixed target every month must match.

**Field names are the layer's existing names** (canonical). The one rename
adopted at schema close: `leaderboard[].cumulative_return → cumulative_return_inception`.

Four-tier model **data-confidence taxonomy** (`leaderboard[].data_confidence`,
`profiles[].status`, RQ2/RQ3 per-model `trust_flag`):

| Tier (leaderboard/RQ) | profiles.status | Meaning | May models |
|---|---|---|---|
| `estimable` | `estimable_primary` | Clean book; primary figures. | GPT, Grok |
| `exploratory_completeness` | `performance_only` | Estimable but missingness-limited. | Gemini |
| `exploratory_model_splice` | `provisional_model_splice` | Two snapshots blended. | DeepSeek |
| `non_estimable_corrupt_book` | `non_estimable` | Book corrupt; decision metrics suppressed. | Sonnet, Opus |

> Grok/Gemini are `estimable` for the **point return** but `raw_basis_sensitive`
> for **daily-risk** (Sharpe/vol/DD) — see `performance[].risk_basis` and
> `gate_scope_refinement`.

---

## 1. `report_meta`

| Field | Rule | Notes |
|---|---|---|
| `schema_version` | populate | `"1.0"`. |
| `period`, `period_label`, `regime`, `phase`, `prompt_version`(+`_note`) | populate | |
| `pilot_exploratory` | populate | |
| `data_window` | populate | `{calendar_month{...}, inception_anchor{inception_date, inception_capital_usd, note}}`. |
| `risk_free_rate` | populate | **Object** `{value: 0.0368, as_of: "2026-04-09"}` (inception-pinned; not a bare scalar). |
| `risk_free_rate_note`, `methodology_notes.risk_free_rate` | populate | As-of 2026-04-09. (Exact FRED 4/9 confirm = separate erratum check.) |
| `pinned_snapshots[model]` | populate | `{display_name, provider, configured_model, cohort, may_snapshots[], snapshot_stable}`. `may_snapshots[]` = list of `{snapshot_id, first_date, last_date, n_success}` — multi-snapshot models carry **distinct entries** (DeepSeek: `v4-flash` 2026-04-24→2026-05-21, `v4-pro` 2026-05-21→2026-05-29). |
| `spy_benchmark` | populate | `{ticker, inception_anchor_date, inception_value, month_end_value, cumulative_return_since_inception, monthly_return, descriptive_sharpe: 6.11, note}`. |
| `regime_summary`, `source_commit`, `generated_at_utc`, `generator`, `bootstrap_config`, `methodology_notes` | populate | |
| `provenance` | populate | `{generated_at_commit, integrity_refs, note}`. `generated_at_commit` = HEAD at build (build-time). |
| `source_commit` (release gate) | populate | **MANDATORY RELEASE GATE.** Emitted `null` by the builder (it cannot know, pre-commit, the commit that reproduces the report). After the data layer commits, reconcile: `HASH=$(git log -1 --format=%H -- reports/monthly/<MONTH>/data_layer.json)` → write `HASH` into `report_meta.source_commit`, then commit. A published monthly layer with a `null` `source_commit` is a release-gate failure. |

## 2. `leaderboard[]` (6 models + `spy_benchmark` row)

| Field | Rule | Notes |
|---|---|---|
| `model`, `rank` | populate | SPY `rank: null` (benchmark, not ranked). |
| `monthly_return` | populate | (existing name; not renamed). |
| `cumulative_return_inception` | populate | Inception-anchored continuity (← renamed from `cumulative_return`). |
| `cumulative_return_clean` | populate | `{value, source: raw\|defab\|not_estimable\|benchmark, confidence, model_spliced}`. GPT raw 0.0766 · DeepSeek raw 0.0586 `model_spliced:true` · Gemini defab 0.0494 · Grok defab 0.0459 · Sonnet/Opus `value:null` not_estimable · SPY 0.0637 benchmark. |
| `spy_relative_alpha` | populate | (existing name). SPY `0.0`. |
| `data_confidence` | populate | Four-tier value; SPY `"benchmark"`. |

## 3. `performance[]` (6 models + `spy_benchmark` row)

| Field | Rule | Notes |
|---|---|---|
| `monthly_return`, `volatility`, `max_drawdown`, `sharpe`, `trade_count`, `win_rate`, `turnover`, `avg_hold_days` | populate or null | **Unchanged at back-fill** (additive only). `max_drawdown` is EOD, May-confined. |
| `risk_basis` | populate | `raw` (GPT) · `raw_basis_sensitive` (Grok, Gemini) · `spliced` (DeepSeek) · `non_estimable` (Sonnet, Opus) · `benchmark` (SPY). |
| `spy_benchmark` row | populate | `sharpe: 6.11` = the descriptive Sharpe (also in `report_meta.spy_benchmark.descriptive_sharpe`); trade/turnover/hold fields `null` (passive). |

## 4. `charts`

| Field | Rule | Notes |
|---|---|---|
| `equity_curve` `{anchor, series, caption}` | populate | Series incl. `spy_benchmark`. **Re-anchored (Phase-A convention):** `anchor` is `{clean_window_start, clean_window_base_date, index_base, excluded_window, note}`; the displayed curve starts at `clean_window_start` (read from the ledger) and indexes off the `clean_window_base_date` close — the 4/9–4/22 launch/shakedown window is excluded, consistent with `cumulative_return_clean`. The inception-anchored cumulative return is unchanged (`leaderboard.cumulative_return_inception`). `caption` is the verbatim chart disclosure. |
| `equity_curve_carries_shakedown_fabrication` | populate | `false` — re-anchored curves never span the shakedown window. Retained as a standing invariant: a `true` value is a regression and halts the builder (`_self_validate`). |
| `underwater` `{anchor, series}` | populate | |
| `correlation_matrix` | populate | `{models, action_concordance, weight_correlation, n_shared_ticks, methodology_ref}`. `n_shared_ticks` (existing name) = per-pair shared-tick count. |

## 5. `cross_model_behavioral`

| Field | Rule | Notes |
|---|---|---|
| `rq1` | populate | `{primary: {status:"not_estimable", reason:"single_pair_cohort", deferred_to:"phase_b"}, exploratory_pairwise: {<numeric concordance block>, reasons: {gemini:"completeness", deepseek:"model_splice", claude:"corrupt_book", claude_opus:"corrupt_book"}}}`. The numeric concordance scalar lives at `rq1.exploratory_pairwise.observed_action_concordance`. |
| `cross_model_episode_register` | populate | Cohort-wide behavioral episodes; **`[]`** for May (none formally on record; the 2026-05-26 systemic outage lives under `data_integrity.incidents`). |
| `per_model_trade_activity`, `per_model_reversal_churn_rate`, `definition_refs`, `window`, `pilot_exploratory` | populate | |

## 6. `profiles[]` (per model)

**Interpretive-shape normalization (ruled 2026-07-31).** Two interpretive
shapes exist in committed layers: May's `strengths[]`/`weaknesses[]` bullet
arrays, and June's `evidence`/`read`/`note` strings. **June's shape is
canonical from 2026-06 onward; May is grandfathered.** Rationale: the June
shape separates mechanically checkable facts (`evidence` — a dense
metric-grounded string) from interpretation (`read`) and status
meta-explanation (`note`), which is the epistemic structure the reports
actually use; May's committed layer is frozen and pinned byte-for-byte by the
reproduce-May regression, so it cannot be migrated.

- **From July 2026** every profile carries **all seven** interpretive keys
  populate-or-null: `style_tag`, `risk_posture_tag`, `strengths` (legacy,
  `null`), `weaknesses` (legacy, `null`), `evidence`, `read`, `note`. The
  builder emits them `null`; `evidence`/`read` (and the tags) are authored in
  the Reports chat for estimable / performance-shown models and are **forced
  `null` for `non_estimable`** (book-derived claims, uncertifiable on a
  phantom book); `note` survives suppression — it explains it.
- **May 2026 (grandfathered):** `strengths[]`/`weaknesses[]` populated for
  estimable models; `evidence`/`read`/`note` **absent** (predate the shape).
- **June 2026 (grandfathered as committed):** the new keys appear only where
  authored (`evidence`+`read` on gpt; `evidence` on gemini/grok/deepseek;
  `note` on claude/claude_opus) — absent ≠ null on the rest. Normalization is
  enforced (builder + `_schema_validate`) from the July build.
- **Quarterly aggregator rule:** treat a key absent in a grandfathered month
  as `null` — the union of keys across months is the canonical field set.

| Field | Rule | Notes |
|---|---|---|
| `model`, `display_name`, `cohort` | populate | |
| `status` | populate | Four-tier (`estimable_primary` / `performance_only` / `provisional_model_splice` / `non_estimable`). |
| `decision_completeness` | populate | `api_success / records`; Gemini 0.5681. |
| `style_tag`, `risk_posture_tag` | nullable | Authored in Reports chat. `risk_posture_tag` is a **retained schema field** but populate-or-null: non-null for estimable / performance-shown models, `null` for `non_estimable_corrupt_book` models (a book-derived deployment/cash claim is uncertifiable on a phantom book). The field surviving ≠ populated for all. |
| `strengths`, `weaknesses` | nullable (legacy) | May's interpretive shape — populated in May only; `null` from June onward (retained schema fields). |
| `evidence`, `read`, `note` | nullable (canonical from 2026-06) | `evidence` = metric-grounded factual string; `read` = interpretation; `note` = status meta-explanation (survives non-estimable suppression). Absent in May (grandfathered); partial in June (as committed); required populate-or-null from July. |
| `evidence_metrics` | populate or null | **Sonnet/Opus:** `rq2_disposition_difference`/`rq3_confidence_outcome_corr` = `null` (not published). DeepSeek RQ values retained but flagged exploratory via `status`. |
| `notable_events` | populate | Objective extraction; empty arrays where none. |
| ~~`data_caveat`~~ | **dropped** | Subsumed by `status`. |

## 7. `methodology_data_integrity_rq`

### `data_integrity`
`incidents[]`, `per_model_failure_rate`, `missing_tick_count`,
`missing_ticks_by_date`, `healthy_ticks_per_day_modal`, `notes` — all populate.

### `known_caveats`
- `state_integrity`: `bugs[]` (`launch_day_clobber` 2026-04-09 fix d2e862ca all six; `cross_model_commingling` 2026-04-10/21 fix cacd8058 Sonnet+Opus); `per_model_fabrication{count,gross_usd}`; `audit{runs,clean_after,clean_runs,anomalies}`; `persistence{stock_persists_for, carried_forward, scope, consequence}`; `clean_window_figures_source{model: raw\|defab\|not_estimable}`.
- `model_identity.deepseek_model_splice`: `{alias, transitions[{date,to,note}], off_spec_window, detection, performance_status, decision_based_status}`.

### `inclusion_gates`
`{completeness_min: 0.80, uncorrupted_book: true, model_identity_stable: true, passing: ["gpt","grok"]}`. The passing cohort is under the **stable** key `passing` (fixed schema — never month-prefixed; month identity lives in `report_meta.period`).

### `gate_scope_refinement`
Note: gates certify **point-return** estimability, not **daily-risk-path**
estimability when residual phantom sits in a volatile name (September pass).

### `rq_update`
- `point_estimates.RQ1..RQ3` + `accumulating_inputs.RQ4..RQ6`, each carrying
  `status`, headline value, `canonical_definition_ref`, `window`, `pilot_tag`.
  RQ1 `status:"not_estimable"`; RQ2/RQ3 `status:"estimable_partial_cohort"`.
- `RQ2`/`RQ3` `per_model[*].trust_flag` ∈ the four-tier taxonomy.
- `fdr_note` — populate.
