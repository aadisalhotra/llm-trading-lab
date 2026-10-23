# Prompt Changelog

All changes to the universal trading prompt are logged here. Each version is saved as `vN.txt`.

## v4 — landed 2026-10-23 (first v4 session 2026-10-26)
**Author:** Aadi
**Status:** Active (built 2026-08-11; landed 2026-10-23 after the close; first v4 session 2026-10-26, Phase B)
**Supersedes:** v3 (active 2026-07-01 – 2026-10-23)

Phase B is long-only because the 2026-10-15 branch decision selected the cash branch
(ledger `operational_events.2026-10[branch_decision_cash_long_only_v4]`). A cash account
cannot short — shorting requires margin (`docs/broker/broker_confirmations_2026-08.md`
D-4) — so the branch is long-only by construction, and T2.4 registers its prompt as
"v4 (cash/long-only branch)" (`docs/prereg/tier2_novel_sections.md`). v4 is the Phase B
prompt: v3 with its shorting content removed. v4 occupies the last pre-freeze change
slot; the 2026-10-31 prompt freeze executes on v4. (An earlier draft of this entry cited
a "2026-08-08 execution-architecture ruling"; no such session occurred. Corrected at
landing per the 2026-08-14 audit.)

**Ablation basis — v4 was not written, it was computed.** v4 is the *literal reverse*
of the committed v2→v3 shorting diff. The v3 entry below records that v3 is "v2
verbatim plus shorting-enabling content only"; that discipline is what makes the
inversion sound. Procedure: take the v2→v3 diff from `57cb14f4` (the commit that
introduced v3 — 3 hunks, +39/−13 lines), retarget its path headers at `v4.txt`,
copy `v3.txt` → `v4.txt` byte-for-byte, and `git apply -R`. Reverse-applying a patch
whose "+" side is v3 yields exactly its "−" side. No line of `v4.txt` was hand-edited,
so the **ablation** introduces zero novel language — every byte of it has already run
in production for a full month (June 2026, under v2). v4 as a whole is v2 plus two
further components added 2026-10-05 (see the verification gate below).

**Verification gate — v4 is v2 plus three separately-verified components (T2.4, amended).**
The earlier gate, "v4 ≡ v2, byte-identical", held only while component 1 stood alone;
it is **superseded** (2026-10-05) by the committed two-component declaration in
`docs/prereg/tier2_novel_sections.md` T2.4, now three. Each component is verified on
its own, and any fourth fails verification:

1. **Shorting ablation — mechanical.** `v4.txt` with its one settlement line removed is
   byte-identical to the v2 blob committed at `57cb14f4`
   (sha256 as stored, LF: `963e726207feec3d07934b4df80b28a4388c8b86145226d031a03e5887534f11`).
   This keeps the June certification: had any hunk of the v2→v3 diff been non-shorting,
   inverting the shorting hunks would have left that change stranded in v4 and it would
   appear here as a delta. Nothing did — all three hunks are shorting content (the
   constraint block, the "Short selling" subsection, and the action enumeration).
2. **Settlement representation — a minimal reviewed addition.** The portfolio block
   splits cash into settled and unsettled balances (`prompt_builder._format_portfolio_block`,
   `settlement_split`), and `v4.txt` carries **one** neutral sentence, the last hard limit:
   "Purchases may use only your settled cash; the proceeds of a sale become settled cash
   one trading day after the sale." Rule statement only, no strategy guidance.
3. **Environment-mode metadata removed** — the `EXECUTION MODE` and `PHASE` header lines
   (hub relay 2026-10-05 of a Research ruling delivered inline 2026-09-11; **relay-only,
   no committed artifact**). Gated: v4 omits them; on the migration branch the same
   removal lands alone on v3 via the signed-off `prompt_environment_metadata: "removed"`
   setting at the boundary — never before. (Moot since 2026-10-15: the long-only branch was
   selected, and there the v4 flip itself is the removal.) Until then v1–v3 render the lines
   exactly as before.

`v4.txt` sha256 (as stored, LF): `5a275e2b0eb306cccb48c136f39784817de9f727e32b3154673403643b4c6d07`. Re-checkable at any time by
`tests/test_v4_ablation.py`, which renders v2 and v4 with the real templates and fails on
**any** difference beyond these three components, rather than trusting this note.
A v4 prompt also refuses to build while settled-funds enforcement is off
(`SettlementPromptMismatchError`): v4's activation and enforcement are one event.

What ablated (the exact inverse of the v3 delta itemized below): the long-and-short
permission line and the gross-short/long/net exposure rule book; the shorts-count-too
parentheticals on the position, per-name, trade-count and drawdown limits; the 10%
short stop; the whole "Short selling" subsection; and SHORT/COVER from the action
enumeration. Everything else is untouched — the 50-position and 20% per-name limits,
the 50-trade cap, the 15% long stop, the 30% drawdown halt, the confidence anchors and
gates, the anti-reversal rule, cash-as-a-position, the loss review, the decision format
and confidence field, and the universe description all carry over unchanged.

**Output-contract appendix.** `prompt_builder._output_schema_for` selected the
shorting-aware appendix on `major >= 3`, which would have handed v4 `V3_OUTPUT_SCHEMA`
— SHORT/COVER in the `action` enum, contradicting v4's own prose. The shorting regime
is not monotonic in version number, so selection is now an explicit registry
(`_SHORTING_PROMPT_MAJORS = {3}`) and v4 resolves to `V2_OUTPUT_SCHEMA` — the same
already-validated long-only constant June ran on, reused verbatim. v3's resolution is
unchanged, so this is inert until activation.

Config flipped in the landing commit (2026-10-23, after the close), as one event:
`prompt_version` v3 → v4, `shorting_enabled` true → false, and settled-funds enforcement on
(`settlement.enforce_settled_funds` true, `activation_date` 2026-10-23) — a v4 prompt refuses
to build without enforcement. The landing date (2026-10-23) and the first v4 session
(2026-10-26) differ because the flip landed after the close. Regime boundary registered at
`scripts/phase_a_integrity_ledger.json` → `regime_boundaries.phase_b_long_only`.

**Open shorts at the boundary** — a model cannot issue COVER under v4, so any short
still open when the flip lands is unmanageable by its holder. See the activation
procedure note in that ledger entry; the flip must be preceded by a force-cover of
every open short (`Executor.force_liquidate` already covers rather than sells a short
position). **Executed 2026-10-23:** two sweeps, four covers on Gemini's book at live prices
under v3 (`scripts/boundary_force_cover.py`, reason `BOUNDARY_ADMIN`); see ledger
`operational_events.2026-10[v4_boundary_force_cover_2026_10_23]`.

## v3 — effective 2026-07-01
**Author:** Aadi
**Status:** Active (effective 2026-07-01, Phase A; built + tested 2026-06-20)
**Supersedes:** v2 (active 2026-06-01 – 2026-06-30)

Prompt text in `prompts/v3.txt` is v2 verbatim plus **shorting-enabling content only** —
no other edits, so the v2→v3 contrast is unconfounded (anything non-shorting would
muddy the before/after read on RQ1). Shorting design built and staged in `57cb14f4`
(2026-06-20), activated in `fff1b165` (2026-07-01), boundary registered at ledger
`regime_boundaries.shorting_activation`; the Research + PI ratification itself has no
committed artifact (flagged at the v4 landing, 2026-10-23). Not re-litigated here. The shorting-only delta:

1. **Operating constraints** — "Long positions only. No short selling…" becomes "Long
   and short positions are both permitted." Adds the exposure rule book: gross short
   ≤ 20% of equity, long ≤ 100%, total gross ≤ 120%, net ∈ [−20%, +100%]. Each existing
   limit (50 positions, 20% per-name, 50 trades/day, drawdown halt) gains a parenthetical
   that shorts count toward it; the limit *values* are unchanged. The per-position
   stop line adds the 10% short stop alongside the existing 15% long stop.
2. **New "Short selling" subsection** — neutral mechanics only (a short profits when
   price falls; proceeds credit cash; negative quantity; cover to close; 20% gross cap;
   10% stop; no borrow cost in this phase). Explicitly states there is no expectation
   to short — a no-short book is valid. No contrarian/differentiation framing (would
   bias RQ1).
3. **Output format** — the action enumeration "buy or sell" becomes "buy, sell, short,
   or cover". The JSON appendix (`prompt_builder.V3_OUTPUT_SCHEMA`) adds SHORT/COVER to
   the `action` enum and clarifies that `target_weight` is always a positive magnitude
   (the desired gross weight; for SHORT/COVER it is the gross short weight).

Communicated identically to all six models; API formatting is the only per-model
difference. Risk engine gates execution behind `portfolio_rules.shorting_enabled`
(FALSE in production until activation). Confidence self-calibration feedback remains
deferred (Aug 2026).

## v2 — 2026-06-01
**Author:** Aadi
**Status:** Active (effective 2026-06-01, Phase A)
**Supersedes:** v1 (in effect 2026-04-09 – 2026-05-31)

Prompt text in `prompts/v2.txt` is the locked source verbatim (the marked section of
`LLM-Trading-Lab-Decision-Prompt-v2.0.md`). v2 adds context and calibration only — it
does not prescribe strategy, sizing, or holding period (required to protect RQ1
cross-model herding). Six changes from v1:

1. **Confidence scale rebuilt** — every 1–10 band gets behavior-tied anchors, plus two
   hard gates: confidence ≥ 8 must name a specific driver; speculative/hedge/"worth a
   try" trades cap at ≤ 4. Anti-clustering self-check added.
2. **Anti-reversal / churn friction** — the model's own most-recent action per ticker
   is surfaced inline (`YOUR_LAST_ACTION`); reversing it requires a concrete nameable
   change; re-entry of a recently exited name counts as a reversal; per-trade "why now".
3. **Cash as two-sided opportunity cost** — acknowledgment required at near-zero or
   unusually high cash. No prescribed floor.
4. **Loss review** — open positions down ≥ 5% require an explicit thesis review
   (intact / weakened / invalidated). All outcomes legitimate.
5. **Neutral objective** — maximize risk-adjusted return over the 18-month horizon; no
   single-period evaluation.
6. **Pre-market briefing** integrated as carried-forward daily context.

Permanent exclusion: no contrarian / differentiation / consensus-orientation framing
(would bias RQ1). Deferred: shorting (Jul 2026), confidence self-calibration feedback
(Aug 2026).

Schema reconciliation (additive — no v1 field renamed): the parser, `DecisionResult`,
and decision logs gain period-level `cash_rationale` (nullable), `position_reviews`
(`{ticker, thesis_status, implication}`), `no_trade_reason` (nullable), and per-trade
`confidence_justification`, `why_now`, `reversal_justification` (nullable). Trade size
stays `target_weight` (0.0–0.20). Period reasoning reuses the existing
`overall_reasoning`. Because v2.txt states the output format in prose, the pipeline
appends the authoritative JSON field-name contract to the system prompt for v2+
(`prompt_builder.V2_OUTPUT_SCHEMA`), mirroring the existing v2 last-action append.

## v1 — 2026-04-08
**Author:** Aadi
**Status:** Superseded by v2 on 2026-06-01

Initial universal trading prompt for Phase 1 build.

- JSON-only output enforced via schema in prompt + provider-side `response_format` where supported.
- Neutral framing — no bullish/bearish language, no leading data presentation.
- Hard rules listed explicitly so models filter their own outputs against constraints.
- Required fields: action, ticker, target_weight, confidence, reasoning.
- Confidence is a 1-10 integer to allow calibration analysis later.
- HOLD is allowed and not penalized.
- Cash is explicitly stated as a valid choice to avoid forcing trades.
