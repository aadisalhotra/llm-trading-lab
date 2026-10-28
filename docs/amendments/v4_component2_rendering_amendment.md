# v4 Component-2 Rendering Amendment — settled cash as the deployable figure

- **Artifact:** `docs/amendments/v4_component2_rendering_amendment.md` — the governing text for this amendment. The T2.4 amendment note in `docs/prereg/tier2_novel_sections.md` and the ledger entry `operational_events.2026-10[v4_component2_rendering_amendment_2026_10_27]` cite this file.
- **Date:** October 27, 2026 (landed after the close; first effective session Wednesday 2026-10-28)
- **Ruling:** Research, relayed by the hub 2026-10-27 on Operations' 2026-10-26 rendering finding.
- **Provenance note:** the hub relay named this path as where the memo lands but did not carry a memo body. This file is the ruling as relayed — classification, the three elements, the dates — plus the exact text Operations implemented to it. The wording in §3 is Operations' implementation of the ruling, not Research-authored text. If Research holds a memo with different wording, it governs and this file is corrected to it by append.

---

## 1. Finding (2026-10-26, first v4 session)

Component 2 of the v4 declaration (T2.4) registers a settlement representation: the state schema splits cash into settled and unsettled balances, and the prompt carries one neutral sentence stating the settled-funds purchase rule. As landed on 2026-10-23, both halves were present, and the instrument was still internally inconsistent:

- The portfolio block led with `Cash: $X (y%)`, the **total** cash balance, carrying the percent-of-book figure. Settled and unsettled followed as bare amounts.
- The rule sentence says purchases may use **only settled cash**.
- Every decision-guidance reference to cash (the "What you receive" bullet, the "Cash as a position" section, the `cash_rationale` requirement in the template and in the JSON output appendix) said "cash" with no qualifier. In context that points at the salient figure: the total.

So the guidance asked a model to reason about, and justify, a cash level its own rule made non-deployable whenever proceeds were unsettled. On 2026-10-26 Gemini's post-cycle snapshot, which the next cycle renders, showed total cash up to $41,894.19 (30.7% of book) with settled cash $0.02. That is the case the inconsistency bites.

## 2. Ruling and classification

The presence of the settled/unsettled figures is not sufficient. "Splits" is read against the component's registered purpose, which is to represent the settlement constraint the model trades under. A representation whose decision guidance points at a figure its own rule makes non-deployable does not serve that purpose.

**Classification:** implementation corrected to registration intent. **Not** a treatment change. The settlement rule, the enforcement, the neutrality constraint (no strategy guidance) and the three-component verification rule are all unchanged. This amends component 2's content and spec. It is not a fourth component.

## 3. The three elements, as implemented

### ① Salience inversion

Settled cash becomes the first cash line, carries the percent-of-book formatting, and is labeled deployable. Total and unsettled follow as bare informational amounts. No strategy guidance is added.

```
YOUR CURRENT PORTFOLIO STATE:
  Total value:   $2,000.00
  Settled cash (deployable): $1,100.00 (55.0%)
  Total cash:     $1,500.00
  Unsettled cash: $400.00
  Open positions: ...
```

The total line is labeled `Total cash` rather than the old bare `Cash`, so that no line in the block is an unqualified "cash" that guidance could be read against. The percent is `Portfolio.snapshot()["settled_cash_pct"]` (settled cash / total value). v1–v3 render the single `Cash: $X (y%)` line exactly as before.

### ② Consistency sweep

Every decision-guidance reference to cash denotes settled (deployable) cash. Rule, state block and guidance point at one figure.

| Site | Before (v2 text, carried into v4 at landing) | After |
|---|---|---|
| `prompts/v4.txt` — What you receive | "Your portfolio: current cash, and every open position …" | "Your portfolio: your settled (deployable) cash, total cash and unsettled cash, and every open position …" |
| `prompts/v4.txt` — Cash as a position | "deploying all of it forgoes the flexibility …"; "Treat your cash level as a deliberate choice."; "When your portfolio is near fully invested (cash near zero) …"; "if you hold a large cash balance …" | "deploying all of your settled cash forgoes the flexibility …"; "Treat your settled (deployable) cash level as a deliberate choice."; "When your settled cash is near zero …"; "if you hold a large settled cash balance …" |
| `prompts/v4.txt` — Output format | "required when cash is near zero or unusually high." | "required when settled cash is near zero or unusually high." |
| `src/prompt_builder.py` — JSON appendix `cash_rationale` | "why your current cash level is a deliberate choice; required when cash is near zero or unusually high" | "why your current settled (deployable) cash level is a deliberate choice; required when settled cash is near zero or unusually high" |

Left unchanged on purpose:

- "Cash is a position. Holding it forgoes expected return": true of any cash, so it is distinction-neutral.
- "There is no required cash level": neutral.
- `(no open positions — 100% cash)`: a state annotation, not guidance, and true of total cash.

The swept passages keep v2's sentence count and add no guidance vocabulary. Both properties are tested.

The appendix change gets its own constant, `V4_OUTPUT_SCHEMA`: `V2_OUTPUT_SCHEMA` with that one line replaced, derived rather than copied. v2's appendix is untouched.

### ③ Pinned rendering

No test asserted the literal rendered cash block (established 2026-10-26). `tests/test_v4_ablation.py` now pins it twice: `test_v4_cash_block_renders_exactly` checks the helper output byte for byte, and `test_v4_cash_block_renders_exactly_in_the_full_user_prompt` checks it through `build_prompts` with the real template.

## 4. Verification under the three-component rule

The rule stands: v4 is v2 plus three separately verified components, and any fourth fails. Component 2's verified extent is now the rule sentence, the three swept template passages, the appendix's `cash_rationale` line and the inverted cash block. The tests verify it this way:

- **Component 1:** reverting exactly those registered component-2 edits from `v4.txt` must give the v2 blob at `57cb14f4`, byte for byte. Before the amendment, removing the rule line alone did that.
- **Component 2:** the rule is still one neutral sentence and the last hard limit. Each sweep passage names settled cash with no added guidance. The appendix differs from v2's in exactly one line.
- **Rendered prompt:** the v2-vs-v4 render may differ only by these registered lines and component 3's removed header lines. The existing injection test still proves a fourth component fails.

`prompts/v4.txt` sha256 (LF, as stored): landed `5a275e2b0eb306cccb48c136f39784817de9f727e32b3154673403643b4c6d07` → amended `fbba04e1a558bab43cb35a52a22f5935dc16e5ddbf9e514889fdcc56f331e703`.

## 5. Segment and dates

- **Internal revision boundary.** `prompt_version` stays `v4`. The v4 segment gets an internal revision boundary: 2026-10-26 and 2026-10-27 ran the landed form, and 2026-10-28 onward runs the amended form. Decision records carry no prompt hash, so the boundary is identified by date (ledger entry, append-only). That date holds only if the landing reaches `origin/main` before the first 2026-10-28 cycle.
- **Smoke-segment verification record.** The 2026-10-26 finding, and the 10-26/10-27 sessions on the landed form, are the verification record of the smoke segment. They are **explicitly not behavioral data**: no metric, RQ or comparison treats them as observations of model behavior under the registered instrument.
- **Freeze.** The prompt freeze executes on the amended form. 2026-10-31 is a Saturday, so the effective freeze is Friday 2026-10-30. Verification sessions on the amended form before the freeze: 2026-10-28, 10-29 and 10-30.
