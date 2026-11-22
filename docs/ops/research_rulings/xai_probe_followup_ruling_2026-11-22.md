# After the xAI Probe — the Fingerprint, a Response That Names Another Model, and the Monitor's Cadence

- **Status:** Research's rulings of 2026-11-22 on H-RES-08. §2 is needed before the adapters land and before 2026-12-07. The rest folds into the identity-monitoring registration, `cohort_refresh_ruling.md` §6 and the lifecycle text of §3.10 at the December freeze. Zero markers.
- **Basis:** `docs/ops/xai_redirect_probe_2026-11-22/` on `review/adapters-claude-5-5-2026-11-21` at `d1761e73` (`records.json` sha256 `926beccf…`); `data/trades/grok_2026-11.jsonl` on main, field `api_system_fingerprint`; `lifecycle_and_identity_ruling_2026-11-22.md` §2; the NYSE calendar.

## 1. Case A, and what the probe does not show about the fingerprint

**Case A governs `grok-4.7`.** All six redirected calls named the served model, `grok-4.3`. The name in the response is the primary identity field for this arm.

**The probe cannot show that the fingerprint carries no identity information.** Every one of the twelve calls was served by the same model, `grok-4.3`: six by redirect and six by direct request. Identical fingerprints across them are what a fingerprint that identifies the served build would also show.

**The production record says more.**

| Served model | `system_fingerprint` | Where |
|---|---|---|
| `grok-4.3` | `fp_eb3c003fc66c14ed` | probe, 12 of 12 calls, 2026-11-22 |
| `grok-4.20-0309-reasoning` | `fp_00c95f2c6b` | production, 75 decision rows, 2026-11-02 to the close of 2026-11-09 |
| `grok-4.20-0309-reasoning` | `fp_81c6f9bb90` | production, 115 decision rows, from the open of 2026-11-10 to 2026-11-20 |

One further row, on 2026-11-05, carries none.

**What goes in the identity-monitoring table.** xAI's fingerprint differed between two different models when one was requested directly and one was reached by redirect. It changed once for one model, with no announcement, and stayed at the new value. It is a corroborant that can move without a change of model, and it is not the identity field. That is a narrower statement than "no identity information", and the table states this one.

## 2. A response that names another model is not a decision

**Case A means the identity check fires on the response itself.** What happens to that response has to be fixed before it can trade.

1. **For each arm, the names it accepts are listed in the adapter.** The list is the configured identifier and any build string the provider returns for it, recorded in calibration and the configuration verification. Nothing is matched by pattern.
2. **A response whose model name is not on its arm's list is `IDENTITY_MISMATCH`.** It is a failed decision: not parsed, not executed and not retried. It is reported to the hub the same day as a candidate identity event.
3. **A name is added to the list only by the hub's ruling,** on evidence that it is a build of the configured model. Execution resumes from the next tick. A name that identifies a different model is never added. The arm truncates at the last session before the change, as registered.
4. **Scope** is that of the finish-reason rule: the refreshed arms from their first session, and the snapshots and DeepSeek from the handover at the latest.

**Why not execute and flag.** In Case A the first redirected tick would trade real capital on a model outside the cohort, under the cohort arm's name, and truncation would not reach back to undo it. A failed decision costs one tick of completeness. Its cost when a benign build string appears is the same, until the hub adds it.

## 3. The 2026-11-10 change on `grok-4.20-0309-reasoning`

The fingerprint change between the close of 2026-11-09 and the open of 2026-11-10 falls under the same doctrine as DeepSeek's change of 2026-11-16 (`deepseek_fingerprint_event_ruling.md`): a candidate identity event, read against the cross-model control arm. `grok-4.20` is a registered fallback, and an unresolved identity question on a fallback is a gate question.

1. **A ledger entry**, if none exists, with the two values, the last and first rows, and the fact that no xAI announcement is known for it.
2. **The Grok reading due by 2026-11-28** is made on a baseline that begins after 2026-11-10 as well as after the venue cutover. It states whether the profile moved at 2026-11-10 relative to the control arm.

## 4. The monitor's cadence

**Once per session does not meet §2.4 as written. §2.4 is amended to a cadence in sessions.**

1. **What the self-chain gives.** In the window there are ten gaps of four calendar days between consecutive sessions, at the Monday and Friday holidays, and 48 of three days.
2. **What it guarantees.** The check runs before the first decision of every session. With xAI's nine days' notice, an announcement made just after one check is seen with at least five calendar days and four sessions before it takes effect. A strict three-day cadence would give six days. The difference is one calendar day, at four points in the year.
3. **Why that is enough.**
   - Under Case A, a redirect is caught by the name check on the first response after it takes effect, and §2 stops that response from trading. The monitor provides lead time; it is not the detector.
   - No arm trades between sessions. A check between sessions could only start the pre-retirement RQ6 run a day sooner.
   - A check between sessions would depend on cron, which has failed. An unreliable check adds no guarantee.
4. **The amended rule.** Each confirmatory provider's lifecycle page is checked before the first decision of every session. The deposit states the worst-case lead for xAI, five calendar days and four sessions. If any provider gives notice of less than eight calendar days, the cadence is ruled on again for that provider.
5. **Unchanged:** the monthly audit by hand, and an immediate check on any announcement that reaches the project.

## 5. xAI's documentation and its served behaviour

`grok-3` is documented to redirect at no reasoning effort. Redirected, it reasoned 147 to 177 tokens, as the low-effort requests did. For xAI, a documented default is not evidence of served behaviour.

The configuration cells for `grok-4.7` that rest on xAI's documentation are stated as documented. The December verification states the observed reasoning counts beside them. Its send/omit test already goes by Route B.
