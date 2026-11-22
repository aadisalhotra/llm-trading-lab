# Fingerprints That Rotate Under a Fixed Model — How a Transition Is Read

- **Status:** Research's ruling of 2026-11-22 on H-RES-10, issued before the adapters land. It supersedes §1 (from "The production record says more" on) and §3 of `xai_probe_followup_ruling_2026-11-22.md` (`30845cb4…`). §2, §4 and §5 of that ruling stand. It is folded into the identity-monitoring registration at the December freeze. Zero markers.
- **Basis:** every decision row from 2026-09-10 to 2026-11-20 on origin `3824cb75`, field `api_system_fingerprint`, recomputed by Research; `deepseek_fingerprint_event_ruling.md`; the xAI probe records (`926beccf…`).

## 1. Research's error

§1 and §3 of the follow-up ruling were computed from the November file only. "It changed once for one model … and stayed at the new value" is wrong. The `fp_00c95f2c6b` run began on 2026-10-29, and 75 was its November count only. The 2026-11-10 change was the eleventh transition, not the first. The hub's re-derivation is right.

## 2. The record, in full

| Identifier | Rows with a fingerprint | Runs | Transitions | Distinct values | Sessions |
|---|--:|--:|--:|--:|--:|
| `grok-4.20-0309-reasoning` | 663 | 12 | 11 | 9 | 52 |
| `deepseek-v4-pro` | 648 | 2 | 1 | 2 | 52 |

**xAI.** The fingerprint changes about once every four to five sessions. Every change fell between sessions. Three values recurred: `fp_b965085801`, `fp_55c25385eb` and `fp_00c95f2c6b`. The format varied, with one 16-digit value among 10-digit ones. The model name was constant throughout. The probe found `grok-4.3` at a tenth value.

**DeepSeek.** One value for 584 rows over 47 sessions, from 2026-09-10 to 2026-11-13. A second value from 2026-11-16.

The other four providers return no fingerprint.

## 3. How a transition is read

**One doctrine for every provider, applied to each field's own base rate.**

1. **Each identifier's fingerprint field is classified by its own record.** It is **rotating** if it made at least two transitions in a record of at least 15 sessions, and **stable** otherwise. A row without a fingerprint is not a transition.
2. **On a stable field, a transition is a candidate identity event.** It gets a ledger entry and a profile reading against the cross-model control arm, as DeepSeek's change of 2026-11-16 did.
3. **On a rotating field, a transition is logged and is not by itself a candidate identity event.** Identity for that arm rests on the fields that do identify the model: the model name in every response, under `IDENTITY_MISMATCH`, and on profile monitoring and the lifecycle monitor. Transitions are counted in the monthly data-integrity section.
4. **A stable field that makes a second transition inside 15 sessions is reclassified as rotating,** by the same rule. A ledger entry records it, and the hub is told.
5. **When classification happens.** The snapshots and DeepSeek are classified now, from the record in §2. The refreshed arms are classified at the Go/No-Go, from their December rows, shakedown included. Until then their transitions are logged, and at classification any transition on a field found stable is read as a candidate event.

**Why the threshold sits where it does.** At xAI's rate a field would show about four transitions in 18 sessions. At DeepSeek's it would show none in most such records. Two transitions in fifteen sessions separates them with room on both sides. Research wrote the rule after seeing these two histories, and before any refreshed arm has a row. The two present classifications follow from the record for any threshold from two to eleven transitions.

## 4. The present classifications

1. **`grok-4.20-0309-reasoning`: rotating.** The 2026-11-10 change loses the status the follow-up ruling's §3 gave it. It is one instance of a documented pattern. No ledger entry is needed for it as an event; the pattern is recorded once.
2. **`deepseek-v4-pro`: stable.** Its change of 2026-11-16 was correctly treated as a candidate identity event. The two treatments are consistent: same doctrine, different base rates.

## 5. The Grok reading of 2026-11-30

The reading returns to its original purpose: the control-arm context for DeepSeek's 2026-11-16 event. Grok moved most in that window, and it is read on a post-cutover baseline.

1. **Segments:** 2026-11-06 to 2026-11-13 (6 sessions) against 2026-11-16 to 2026-11-20 (5 sessions). Grok's own fingerprint transitions are not boundaries.
2. **It is descriptive.** Neither segment has the registered 17-session baseline, and the reading says so.
3. Research's message of 2026-11-22 proposing segments around 2026-11-10 is withdrawn.

## 6. The identity-monitoring table, xAI row

> `system_fingerprint` populated. For `grok-4.20-0309-reasoning` it took nine distinct values in twelve runs between 2026-09-10 and 2026-11-20: eleven transitions, all between sessions, three values recurring, with the model name constant throughout. It rotates under a fixed model and is not the identity field. A transition is logged and is not by itself a candidate identity event. The identity field is the model name in the response. An induced probe on 2026-11-22 showed that xAI's redirect of retired identifiers names the served model (`docs/ops/xai_redirect_probe_2026-11-22/`). That response is a failed decision under `IDENTITY_MISMATCH`.

The DeepSeek row gains one sentence:

> Stable: one value from 2026-09-10 to 2026-11-13, a second from 2026-11-16 (candidate identity event, `deepseek_fingerprint_event_ruling.md`).
