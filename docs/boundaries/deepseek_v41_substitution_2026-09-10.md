# DeepSeek V4-Pro → V4.1-Flash forced substitution — pre-boundary capture

**Boundary:** 2026-09-10T04:00:00Z (claimed; see §1 on attestation)
**Capture frozen at:** 2026-09-10T01:07Z–01:20Z (≈3 hours before the boundary)
**Data source:** `origin/main` @ `bbb1d886`, decision logs `data/trades/deepseek_2026-*.jsonl`
**Last pre-boundary production call:** 2026-09-09T19:34:22Z
**First post-boundary production call (expected):** ≈2026-09-10T13:33Z

DeepSeek announced on 2026-09-09 that until V4.1 Pro ships, every
`deepseek-v4-pro` request routes to V4.1 Flash and bills at Flash rates. **The
model string on the wire does not change.**

This is the third DeepSeek identity event in the lab's life and the first with
advance notice. The 2026-07-20 GA swap became a *disclosed gap* in the
version-boundary record because no pre-launch state was captured and the record
concluded that the swap was "structurally invisible to lab telemetry." This
document exists so that does not happen twice — and §3 shows that the
invisibility conclusion was **too pessimistic**.

---

## 1. Announcement and attestation status

### What is claimed

| Item | Claimed value |
|---|---|
| Effective | 2026-09-10T04:00:00Z |
| Scope | all `deepseek-v4-pro` requests → V4.1 Flash, until V4.1 Pro ships |
| Model string | unchanged (`deepseek-v4-pro`) |
| Off-peak rates | $0.003 cache-hit / $0.15 cache-miss / $0.60 output per 1M tok |
| Peak rates | exactly 2× off-peak |
| Peak windows | 01:00–04:00 and 06:00–10:00 UTC, **Monday–Friday** |

Against our current registered v4-pro rates ($0.66 cache-miss / $1.98 output)
that is **−77.27% input, −69.70% output**.

### What is attested

**Nothing, on any DeepSeek-controlled public channel.** Checked
2026-09-10T01:07Z, ~3 hours before the stated boundary:

| Channel | State |
|---|---|
| `api-docs.deepseek.com/quick_start/pricing` | still the 2026-08-16 rates; three models listed, none a 4.1 |
| `api-docs.deepseek.com/updates` (change log) | ends at the 2026-08-21 Vision-Exp entry |
| news index | no 4.1 entry |

The figures reach us only through press, and the press disagrees with itself:

- One outlet gives USD $0.003 / $0.15 / $0.60 and the 04:00Z hour, sourced to
  "a September 9 customer email" which it states it **could not independently
  retrieve** from the sign-in-only platform.
- Another gives CNY ¥0.02 / ¥1 / ¥4 and **no hour at all**.

USD == CNY ÷ 6.667 exactly. That is the shape of a press-side FX conversion,
not a published USD list price — DeepSeek's own USD list has always been quoted
directly in round USD ($0.22, $0.66), never as a converted CNY figure.

### Ruling applied

**The rate period is NOT registered.** This is the situation hub ruling
2026-08-05 governs — old rate until a new rate is attested — established on the
grok-4.20 cut-date precedent, where a supplied date was contradicted by the
archive. Registering an unattested *cut* would understate our own cost; holding
overstates it, which is the correct direction for a cost claim.

**Known consequence, recorded so it is not later discovered as a surprise:**
from ≈2026-09-10T13:33Z until the rate is attested and landed, `deepseek-v4-pro`
traffic prices at $0.66/$1.98 while the provider bills ≈$0.15/$0.60 — roughly a
**3.6× overstatement**, about **+$0.18/trading day** against the window-B
baseline below. Costs are repriced at *read* time from stored tokens
(`performance.reprice_record_usd`), so landing the period later corrects the
whole interval retroactively. **Nothing is lost by waiting.**

### One thing that *was* attested, and was wrong in our table

The published page reads: *"Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC,
Monday through Friday (all other hours are off-peak)."*

Our 2026-08-16 transcription captured the hours and **dropped the Monday–Friday
qualifier**, leaving `DEEPSEEK_PEAK_WINDOWS_UTC` — declared in-module as "the
single source of truth for the windows" — *wider* than the published schedule.
A future per-call classifier reading hours alone would price a Saturday 02:00Z
call at 2×. Inert today (nothing classifies by timestamp); corrected in this
package by adding `DEEPSEEK_PEAK_WEEKDAYS`.

This is an omission on our side, not a provider change: the qualifier is on the
page today, describing the August schedule.

---

## 2. Pre-boundary baseline

### 2.1 Model-version log — `data/model_versions/deepseek.jsonl`

1354 rows: 1351 observations (1342 successful, 9 failed) + 3 TRANSITION events.

| observed_version | n (success) | first → last |
|---|---:|---|
| `deepseek-chat` | 9 | 2026-04-08 → 2026-04-09 |
| `deepseek-reasoner` | 135 | 2026-04-09 → 2026-04-23 |
| `deepseek-v4-flash` | 257 | 2026-04-24 → 2026-05-21 |
| `deepseek-v4-pro` | **941** | **2026-05-22 → 2026-09-09** |

All 9 failed observations carry the synthetic `deepseek-v4-pro` echo (BaseAdapter
sets the configured id on failure) and are excluded from comparison by design.

The three TRANSITION rows are the 2026-04-24 backfilled provider-side repoint,
the 2026-05-21 deliberate single-cell repoint, and its 2026-05-22 detector
confirmation. **There is no fourth.** The 2026-07-20 GA swap produced none —
that is the disclosed gap — and on current code the 2026-09-10 substitution will
produce none either.

### 2.2 Decision-record statistics

Windows are inclusive date ranges over successful calls. `prompt_version` is
**v3 throughout windows B–D** (v4 is staged, not live), so the prompt is not a
confound inside them.

| | **A** v4-pro leg | **B** since 08-16 rates | **C** last 30 cal. days | **D** Sept to date |
|---|---|---|---|---|
| range | 05-22 → 09-09 | 08-16 → 09-09 | 08-11 → 09-09 | 09-01 → 09-09 |
| calls / success / fail | 950 / 941 / 9 | 214 / 207 / 7 | 265 / 258 / 7 | 78 / 77 / 1 |
| failure rate | 0.95% | 3.27% | 2.64% | 1.28% |
| retries (attempt>1) | 2 | 0 | 0 | 0 |
| trading days | 75 | 17 | 21 | 6 |
| calls/day | 12.67 | 12.59 | 12.62 | 13.00 |

**Window B is the reference comparator** — homogeneous prompt version and
homogeneous rate period.

| metric (window B, n=207) | mean | sd | p10 | p50 | p90 |
|---|---:|---:|---:|---:|---:|
| `input_tokens` | 8438.6 | 91.8 | 8342.2 | 8436.0 | 8561.4 |
| `output_tokens` | 3202.3 | 1385.0 | 1748.6 | 2959.0 | 5272.8 |
| out/in ratio | 0.3794 | 0.1640 | 0.2077 | 0.3456 | 0.6221 |
| `api_latency_seconds` | 58.00 | 26.69 | 30.51 | 52.65 | 98.26 |
| **output tok/sec** | **56.26** | **7.67** | **48.70** | **55.83** | **62.32** |
| `screening_input_tokens` | 3406.8 | 44.2 | 3356.2 | 3401.0 | 3466.0 |
| `screening_tokens` (out) | 3416.2 | 1050.8 | 2221.2 | 3239.0 | 4779.6 |

Full-leg (window A, n=941) for reference: input 8074.8 ± 610.1, output
3849.9 ± 1749.2, latency 69.85 ± 34.71, output tok/sec 57.31 ± 12.17.

**`api_finish_reason` is `None` on 941/941. `thoughts_tokens` is `None` on
941/941.** See §3.

### 2.3 Cost baseline

Repriced at `RATE_HISTORY` via `performance.reprice_record_usd` (2026-08-05
rider 1), **not** summed from the stored `cost_usd` field.

| window | cycles | decision $ | screening $ | total $ | per cycle | per trading day |
|---|---:|---:|---:|---:|---:|---:|
| A (v4-pro leg) | 941 | 7.585899 | 5.479704 | **13.065603** | 0.013885 | 0.174208 |
| B (since 08-16) | 207 | 2.465373 | 1.865586 | **4.330959** | 0.020923 | 0.254762 |
| C (last 30d) | 258 | 2.790894 | 2.090894 | **4.881789** | 0.018922 | 0.232466 |
| D (Sept to date) | 77 | 0.953622 | 0.692764 | **1.646386** | 0.021382 | 0.274398 |

Token totals, window B: decision 1,746,785 in / 662,876 out; screening
705,214 in / 707,144 out.

> **Do not use the stored `cost_usd` field for this.** Summed raw it gives
> $35.867605 for the leg against $13.065603 repriced — a **2.737×**
> overstatement, and exactly 4.000× in June and July, because those calls were
> priced at call time under the legacy flat table's $1.74/$3.48, a rate that was
> never published on any date. This is a known, handled condition: the reporting
> layer reprices at read time and never reads the stored field. It is recorded
> here only so a future reader does not reconstruct the baseline the wrong way.
> 13 screening records in window A are unpriceable (null stored cost defeats the
> back-solve) and are excluded.

**Projection at the claimed (unattested) rates**, from window B tokens:
$4.330959 → $1.189812, i.e. **−72.53%**, $0.254762 → $0.069989 per trading day
(≈$64.20 → ≈$17.64 annualised at 252 trading days).

### 2.4 Per-trading-day series (control chart baseline)

Successful calls only. Established so a post-boundary reader can see the
*pre-existing* drift and not attribute it to the substitution: `input_tokens`
rises ≈8250 → ≈8530 over the window (portfolio and prompt growth) and output
tok/sec declines ≈63 → ≈51.

```
date          n  ok  in_mean  out_mean  out_p50  lat_mean  tok/s_mean  tok/s_sd
2026-08-11   12  12   8247.4    3302.7   3206.5     62.63       53.07     12.13
2026-08-12   13  13   8324.8    3883.2   3938.0     72.79       53.34      6.99
2026-08-13   13  13   8315.2    3148.3   2451.0     50.10       62.87      7.74
2026-08-14   13  13   8380.1    2385.2   2042.0     36.35       63.77     12.17
2026-08-17   12  12   8373.8    3015.2   2463.0     48.68       64.43     11.24
2026-08-18   13  13   8391.2    3739.6   3444.0     65.60       57.64      9.99
2026-08-19   13  13   8380.2    3745.2   3186.0     66.99       56.92     10.38
2026-08-20   13  13   8376.5    3161.5   3052.0     54.07       59.33      7.08
2026-08-21   13  13   8440.1    2731.9   2608.0     45.54       60.99     10.74
2026-08-24   13  13   8391.5    3230.1   2718.0     60.64       53.57      5.58
2026-08-25   13  13   8392.0    2748.4   2314.0     48.19       57.12      3.89
2026-08-26   11  11   8361.9    2826.0   3010.0     50.63       55.47      4.58
2026-08-27   13  13   8452.3    2745.0   2654.0     48.90       57.05      4.66
2026-08-28   13   8   8469.6    2953.0   2520.0     56.02       53.40      4.42
2026-08-31    9   8   8347.1    2673.9   2097.5     49.12       54.23      2.32
2026-09-01   13  13   8417.2    4178.7   3560.0     76.22       55.19     10.02
2026-09-02   13  13   8577.0    2897.3   2737.0     55.35       53.27      5.65
2026-09-03   13  13   8541.4    2641.0   2469.0     48.56       54.30      3.77
2026-09-04   13  13   8472.4    3245.8   3273.0     57.19       57.41      3.93
2026-09-08   13  13   8505.7    3574.9   3329.0     66.71       53.21      2.44
2026-09-09   13  12   8533.2    4023.2   3589.5     83.57       51.12      9.25
```

### 2.5 Live API-surface probe — the fields the logs never held

Run 2026-09-10T01:13Z–01:20Z against `deepseek-v4-pro`, ~2h45m before the
boundary, because the next production call falls *after* it. Out-of-repo, wrote
nothing to `data/`, made no decision-log or version-log entry.
3 samples, 2 successes + 1 connect timeout, then 1 replacement sample.
Raw responses and the probe script are held off-repo on the operator machine
(deliberately — the probe is not lab telemetry and must not be mistaken for it);
the values below are the whole of what it established.

| | sample 1 | sample 3 | replacement |
|---|---|---|---|
| `model` echo | `deepseek-v4-pro` | `deepseek-v4-pro` | `deepseek-v4-pro` |
| **`system_fingerprint`** | `a307abda487cd1b463329ccb945ce396` | *(identical)* | *(identical)* |
| `finish_reason` | `stop` | `stop` | `stop` |
| `completion_tokens` | 8373 | 5932 | — |
| `reasoning_tokens` | 7163 | 4973 | 4601 |
| reasoning ÷ completion | 0.8555 | 0.8383 | — |
| `prompt_cache_hit_tokens` | 0 | 512 | 512 |
| `prompt_cache_miss_tokens` | 559 | 47 | — |
| latency | 115.01s | 71.35s | 81.14s |

**`usage` keys present:** `completion_tokens`, `completion_tokens_details`,
`prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`, `prompt_tokens`,
`prompt_tokens_details`, `total_tokens`.
**Top-level keys:** `choices`, `created`, `id`, `model`, `object`,
`system_fingerprint`, `usage`.

> Probe token counts are **not** comparable to production token counts — the
> probe prompt is 559 tokens against production's ≈8440. What transfers is the
> *field surface*, the fingerprint value, and the reasoning **share**.

---

## 3. Detector limitation, and what replaces it

### The limitation

`detect_version_transition` (`src/model_versions.py:48`) compares consecutive
successful `observed_version` strings and fires only when they differ.
`observed_version` is `data.get("model")` from the DeepSeek response — the alias
echo. The provider has stated the alias will not change. **The detector cannot
fire on this boundary.** No fix to the detector changes that: it is comparing
the one field the provider has told us will hold still.

This is the same mechanism that made 2026-07-20 invisible.

### The correction to the 2026-07-27 finding

The V4-GA record concluded "no fingerprint/metadata captured anywhere, so the
GA build swap underneath the alias is structurally invisible to lab telemetry."

**The first clause is true; the second does not follow.** DeepSeek returns
`system_fingerprint` on every response and has been doing so all along — §2.5
observed it on a live call. It was invisible because
`deepseek_adapter._call_api` read `choices[0].message.content`, `model` and
three `usage` counters, and dropped everything else. The blindness was ours, not
structural.

### Signals available post-boundary

Ranked by strength. "Baselined" means a pre-boundary comparator exists.

| # | Signal | Baselined? | Strength |
|---|---|---|---|
| 1 | **`system_fingerprint`** = `a307abda…ce396` | **yes** (§2.5) | **decisive if it moves** — set by the provider, independent of our rate table, invariant to trade composition |
| 2 | `input_tokens` on a stereotyped prompt | yes: 8438.6 ± 91.8 (CV **1.09%**) | **strong** — a tokenizer change shows immediately; a >0.5% shift is far outside noise |
| 3 | output **tok/sec** | yes: 56.26 ± 7.67 (CV 13.6%) | **strong** — a *rate*, so it largely divides out trade composition, which drives length not speed |
| 4 | reasoning ÷ completion share | probe only: 0.84–0.86 | strong once logged; a Pro→Flash reasoning-tier change should move it hard |
| 5 | `finish_reason` | probe only: `stop` | moderate — catches truncation regressions under the 16384 cap |
| 6 | `output_tokens` / out-in ratio | yes: 3202.3 ± 1385.0 | **weak alone** — CV 43%, dominated by trade composition |
| 7 | failure / retry rate | yes: 3.27% / 0 | weak, high variance |
| 8 | `observed_version` | yes | **will not fire** — this is the limitation |

**On cost as a signal.** The ≈−77%/−70% drop is real and is the sharpest
*economic* consequence, but it is only *evidence* when read from **DeepSeek's
own billing dashboard**. Our `cost_usd` is modelled from `cost_rates.py`, not
read from the provider. So:

- while the rate is unregistered (§1), our logs will show **no** cost change —
  divergence from the provider invoice is then the detection signal, and it
  requires a human to read the invoice;
- if the rate were registered, our logs would show the drop **by construction**,
  which proves nothing about whether the substitution actually happened.

Registering the rate makes the cost signal **circular**. Signals 1–5 do not have
this property.

**Methodological warning carried forward from the Gemini Phase-B week-one
finding: stratify before concluding.** Token divergence there turned out to be
trade composition, not the SDK migration. Signals 2 and 3 are the
composition-robust ones; signal 6 is exactly the trap.

---

## 4. Changes landed with this capture

| Path | Change |
|---|---|
| `src/adapters/deepseek_adapter.py` | capture `system_fingerprint`, `finish_reason`, `reasoning_tokens` → `thoughts_tokens`, and the cache hit/miss split. Defensive reads; a missing field degrades to `None` and never costs a decision. The `choices[0].message.content` read stays indexed so a malformed response still raises into BaseAdapter's parse-retry. |
| `src/logging/decision_log.py` | new record fields `api_system_fingerprint`, `cache_hit_tokens`, `cache_miss_tokens`. `api_finish_reason` and `thoughts_tokens` already existed (Gemini populates them) and now populate for DeepSeek with no schema change. Additive, `.get()`-read, same shape as the 2026-08-05 `screening_input_tokens` addition. |
| `src/analytics/cost_rates.py` | `DEEPSEEK_PEAK_WEEKDAYS` added (the dropped Monday–Friday qualifier); a NOT-REGISTERED block recording the V4.1 claim, the verification result, the direction of the resulting cost error, and the circularity corollary. **No rate value changed.** |
| `tests/test_deepseek_adapter.py` | new, 12 tests: capture of all four fields; fingerprint moving while the alias echo holds still (the boundary's exact shape); reasoning-share semantics; request params unchanged; per-field degradation to `None`; malformed `choices` still raises. |
| `tests/test_cost_rates.py` | weekday-qualifier test. |

Suite: **409 passed** before these, **422 passed** after — +13 (12 new adapter
tests, 1 new cost-rates test; `test_cost_rates.py` 28 → 29). Nothing pre-existing
changed status. The v4 lane's staged `tests/test_v4_ablation.py` — 17 tests —
re-runs green and untouched.

**Timing note.** The adapter change only yields data on calls made after it is
deployed. The first post-boundary call is ≈2026-09-10T13:33Z. Every cycle it
misses is a cycle whose fingerprint is unrecoverable.

**Cohort-wide follow-up.** The same question was then put to the other four
adapters — see `docs/adapter_field_retention_audit_2026-09.md`. OpenAI and xAI
were dropping the identical four fields; Gemini was dropping the cache split
and the prompt-level block reason; Anthropic was dropping its finish reason and
cache counters. The load-bearing finding there: **Anthropic exposes no build
fingerprint and no reasoning-token counter at all**, so for the two Claude
cells `model` remains the only identity signal and a string-stable substitution
would be undetectable in the way this one nearly was. That is a limitation to
disclose, not one this lab can close.

---

## 5. Open items

1. **Attestation watch.** Re-check the pricing page, change log and news index
   after the boundary. On attestation, append the period to *both*
   `deepseek-v4-pro` and `deepseek-v4-flash` with `effective_from: "2026-09-10"`
   and remove the NOT-REGISTERED block. Read-time repricing corrects the whole
   interval retroactively.
   - Date-granularity note, for the period note when it lands: the boundary is
     04:00Z, the table is date-granular, so a 09-10 call before 04:00Z would be
     under-priced. Inert — the lab's model-calling crons run 13:00–21:00 UTC.
2. **Provider invoice.** Read DeepSeek's billing dashboard after 2026-09-10 and
   compare against §2.3. This is the only *independent* cost evidence.
3. **Ledger.** Entry queued, not written — `scripts/phase_a_integrity_ledger.json`
   is contended by the staged v4 prompt lane (CLAUDE.md rule 7). Text prepared;
   it lands after the v4 lane, by surgical text insertion (rule 8).
4. **Research disclosure.** The `deepseek-v4-pro` leg now spans 2026-05-22 →
   present with **two** presumed-but-unobserved internal transitions: 2026-07-20
   (GA, no capture) and 2026-09-10 (this one, captured). RQ comparisons treating
   the leg as one model must say so. Professor Rossi's methodology review is
   2026-09-17.
5. **Cohort parity.** The lab's DeepSeek cell was moved to v4-pro on 2026-05-21
   specifically to sit at the frontier reasoning tier for cohort parity. A forced
   route to a Flash tier is that decision being reversed by the provider. Whether
   the cell stays, moves, or is disclosed as tier-changed is a Research question,
   not an Operations one.
