# O-3: latency table (#56), completeness check (#57), Gemini thinking (#58)

- **Produced:** 2026-11-16, Operations. Read-only: nothing in the repo was changed.
- **Read at:** origin/main `3d1c04ab7` (the 11-16 end-of-day tick).
- **Inputs:** decision logs `data/trades/{book}_2026-0{4..9}.jsonl` and the committed monthly layers `reports/monthly/2026-0{5,6,7}/data_layer.json`.
- **Reproduce:** `python o3_compute.py > o3_results.json` from the repo root. Every number below comes from `o3_results.json`. The script and the JSON sit next to this file, and their hashes are in the manifest at the end.
- **Estimator (CLAUDE.md rule 9):** each decision record contributes one value. Percentiles use numpy's default `linear` method (Hyndman–Fan type 7). No trimming, no per-day aggregation.

---

## #56: attempt-1, non-timeout latency, Aug–Sep baseline

**Population.** Records with `api_attempt == 1` and `api_success == true`, timestamped 2026-08-01 through 2026-09-30. `api_latency_seconds` is the adapter's wall-clock from first send to parsed response. `BaseAdapter.decide` starts the clock before the retry loop, so a second attempt's latency includes the first attempt and the retry delay. Restricting to attempt 1 is therefore what keeps retries out of the table. Failed calls go to the provider-availability population and are excluded here, as the outcome-class conditioning rule requires. Each book had one configured model ID across the whole window.

| Model (book) | Model ID | n | p50 (s) | p90 (s) | p99 (s) | max (s) |
|---|---|--:|--:|--:|--:|--:|
| GPT-5.4 (`gpt`) | gpt-5.4-2026-03-05 | 529 | 14.45 | 21.49 | 38.74 | 48.09 |
| Claude Opus 4.6 (`claude_opus`) | claude-opus-4-6 | 529 | 41.88 | 67.18 | 82.55 | 88.50 |
| Gemini 3.1 Pro (`gemini`) | gemini-3.1-pro-preview | 511 | 41.53 | 65.43 | 93.72 | 214.06 |
| Claude Sonnet 4.6 (`claude`) | claude-sonnet-4-6 | 529 | 47.52 | 78.12 | 84.04 | 176.58 |
| DeepSeek V4 Pro (`deepseek`) | deepseek-v4-pro | 521 | 49.41 | 93.78 | 129.27 | 160.42 |
| Grok 4.20 (`grok`) | grok-4.20-0309-reasoning | 512 | 61.07 | 94.35 | 127.52 | 207.16 |

**Per-month stability (p50 / p90 / p99, seconds):**

| Book | Aug | Sep |
|---|---|---|
| gpt | 14.69 / 20.82 / 35.08 | 14.03 / 21.44 / 39.22 |
| claude_opus | 41.70 / 63.69 / 82.92 | 42.36 / 68.18 / 81.23 |
| gemini | 38.70 / 62.88 / 88.08 | 42.87 / 67.42 / **159.35** |
| claude | 46.96 / 79.44 / 84.28 | 47.73 / 75.50 / 83.87 |
| deepseek | 53.01 / 97.14 / 125.25 | 47.12 / 87.45 / 135.84 |
| grok | 55.56 / 84.20 / 104.43 | 65.37 / 106.74 / 144.89 |

The bodies are stable month to month. Two tails are not: Gemini's p99 almost doubles in September (88 → 159 s), and Grok's whole distribution shifts up (p50 +9.8 s, p99 +40 s). A pooled Aug–Sep p99 for those two models averages two different months. Research should decide whether the citation uses the pooled figure or reports the months separately.

**Excluded records** (classified from the stored error text; `api_error_class` exists only from 2026-11-15):

| Book | Excluded | Breakdown |
|---|--:|---|
| claude, claude_opus, gpt | 0 | none |
| deepseek | 8 | 6 × HTTP 402 *Insufficient Balance*, 1 × HTTP 504, 1 × attempt 2 |
| gemini | 18 | 13 × attempt 2 (parse retry), 3 × 503 high demand, 1 × 429 *prepayment credits depleted*, 1 × transport disconnect (09-03) |
| grok | 17 | 13 × HTTP 403 *permission-denied* (team access), 3 × client read timeout at 120 s, 1 × 429 *at capacity* |

**Two findings for the provider-availability population:**

- **Account-state failures are a class the registration doesn't name.** 20 of the 29 attempt-1 failures excluded above (6 DeepSeek 402, 13 Grok 403, 1 Gemini 429) came from billing or credit state on our side, not from provider availability. "Provider availability" should not absorb them silently. They need their own label.
- **Grok's three 120 s client timeouts in this window are the registered 08-20, 08-25 and 09-17 events.** The fourth registered one (04-24) falls outside the window.

---

## #57: are the ≥590 s failures already counted in the completeness denominators?

**Verdict: VERIFIED for every month that has a committed denominator (May, June, July). April has no committed denominator, so for April the claim cannot be checked against one.**

Completeness is a per-record ratio: `api_success / records` over the month's decision log. A ≥590 s call is one decision record, so it appears once in the denominator and, if it failed, once in the failure count. This registration adds a causal decomposition of failures already counted. It adds no new count.

| Month | Decision-log records | api_success | Layer `records` / `api_success` | Layer = log? | ≥590 s records | of which counted as failures | ≥590 s share of the month's failures |
|---|--:|--:|---|---|--:|--:|--:|
| 2026-04 | 206 | 112 | **no committed layer** | n/a | 68 | 68 | 68/94 |
| 2026-05 | 257 | 146 | 257 / 146 | yes, both | 80 | 79 | 80/111 |
| 2026-06 | 270 | 212 | 270 / 212 | yes, both | 3 | 3 | 3/58 |
| 2026-07 | 283 | 166 | 283 / 166 | yes, both | 1 | 1 | 1/117 |

(Layer path: `methodology_data_integrity_rq.data_integrity.per_model_failure_rate.gemini`. April's only monthly artifact is `reports/monthly/2026-04.md`, a one-day render generated 04-09 that has no completeness figure.)

**Rule-10 check of the registration's own numbers against the record** (rossi_review_rulings.md §2):

| Registered statement | Record | Agrees? |
|---|---|---|
| "151 of 152 ≥590s calls carry no usage metadata" | 152 records ≥590 s (Apr 68, May 80, Jun 3, Jul 1). 151 have no token counts | **Yes** |
| (implied) the 152 are failures | **The one record with usage metadata is a success**: 2026-05-14 17:20Z, 600.1 s, 8,305 in / 337 out. So there are **151 ≥590 s failures plus 1 ≥590 s success**. May's 80 ≥590 s records are 79 failures and 1 success | **Wording**: "the ≥590s failures" means 151, not 152 |
| "504 deadline-exceeded (108), 503 high-demand (31), 499 cancelled (11)" | 108 / 31 / 11. The 151st failure is `Timeout of 600.0s exceeded, last exception: 503 recvmsg: Connection reset`, a 503 that is not high-demand | **Yes**, with one failure unlisted (108+31+11 = 150 of 151) |
| "zero events Aug–Sep" | 0 records ≥590 s in Aug or Sep | **Yes** |
| "improving from June" | Jun 3, Jul 1 | **Yes** |
| "up to 31% of a month's Gemini ticks at peak" | May 80/257 = **31.1%**. But **April is 68/206 = 33.0%** | **Only if April is excluded.** If the April pilot month counts, the peak is 33% |

---

## #58: pre-fix successful-call thinking: is 4096 − median(thinking) ≈ 173?

**Verdict: NOT COMPUTABLE from the record as dispatched. The census premise ("computable from Gemini pre-2026-07-28 `thoughts_tokens`") is false.**

**1. Pre-fix thinking was never recorded.**

| Month | Gemini records | `thoughts_tokens` key present | non-null |
|---|--:|--:|--:|
| 2026-04 | 206 | 0 | 0 |
| 2026-05 | 257 | 0 | 0 |
| 2026-06 | 270 | 0 | 0 |
| 2026-07 | 283 | 52 | **0** |
| 2026-08 | 257 | 257 | 254 |
| 2026-09 | 272 | 272 | 269 |

The key first appears with the telemetry commit `3d97fc187` (2026-07-27), but on all 52 late-July records it is null. The legacy `google-generativeai` SDK reported `thoughts_token_count` as 0 or absent. The finish-reason probe README says the same: thinking was spent "invisibly". The first non-null value is **2026-08-03T13:41:57Z**, after the `google-genai` migration. Pre-fix successes (2026-04-08..07-27) number 588, and none of them carries a thinking count. The probe artifacts (`side_experiments/gemini_finish_reason_probe/results.jsonl`) record no thinking field either.

**2. Where 173 and 3,923 come from.** The hypothesis pairs numbers from two different populations:

- **173** is the **July pre-fix mean visible output of failed calls**: 173.4 tokens, n = 112. The "~540" it is compared with in T1 is the July pre-fix mean output of successful calls: 539.7, n = 118. Over all pre-fix months the failure mean is 182.1 (median 152) and the success mean is 495.9.
- **3,923** is the registered *post-fix* (16384-cap) median thinking, and 4096 − 3,923 = 173 exactly. The equation is built from a post-fix success median and a pre-fix failure mean, so it is a coincidence check, not a measurement of the pre-fix state.

**3. The registered 3,923 / 614 medians do not reproduce.** I tried every plausible window. None gives them:

| Population (successful calls with thinking recorded) | n | median thinking | median answer | 4096 − median thinking |
|---|--:|--:|--:|--:|
| Aug–Sep, attempt 1 (the #56 population) | 511 | 3,881 | 580 | 215 |
| Aug–Sep, all successes | 519 | 3,880 | 575 | 216 |
| Aug only | 250 | 3,606 | 511 | 490 |
| Sep only | 269 | 4,122 | 590 | −26 |
| Aug 3–10 (Phase-B week one) | 64 | 4,191 | 684 | −95 |
| Aug 1 – Sep 17 (up to the ruling date) | 405 | 3,581 | 497 | 515 |

Across defensible windows, "4096 − median" ranges from **−95 to 515**. A to-the-token closure that depends this heavily on the window closes nothing. The registered thinking *share* does reproduce: ~86% registered, against **86.7%** pooled (Σthinking / Σ(thinking+answer)) and 87.1% as the median of per-call shares on the #56 population.

**4. Why no record could close it to the token.** For a call truncated with `MAX_TOKENS`, thinking + visible output = 4096 is the shared-budget mechanism itself, so it holds by construction. Testing it per call needs each failure's thinking count, which was never recorded. A median taken over successes measures the wrong population, because successes are exactly the calls whose thinking left room for the answer.

**What the record does support:**
- **The mechanism.** It already stands on the probe's conversion test, which `tier1_novel_sections.md` quotes: at the 4096 cap, 22 of 23 failures in arm A ended with `MAX_TOKENS`; the same prompts verbatim at a 16384 cap (arm D) ended 15 of 15 with `STOP` and zero `MAX_TOKENS`.
- **The magnitude, by inference only.** 291 of 511 post-fix attempt-1 successes (**57.0%**) used thinking + answer > 4096, so they would have overrun the old cap. The July pre-fix failure share was **48.9%** (1 − 0.5108). The two agree in direction and rough size. This is not a measurement, because thinking under a 4096 cap need not match thinking under a 16384 cap.

**For Research:**
- Resolve the `[VERIFY]` as *not verifiable from the record*: pre-fix thinking was never captured by the production SDK. The mechanism rests on the conversion test, not on a to-the-token identity.
- Re-cite the §2 thinking-share sentence to a population that reproduces. On the #56 population that is median 3,881 thinking / 580 answer, n = 511, window 2026-08-01..09-30, share 86.7% pooled. Alternatively, Research names the population behind 3,923 / 614. Operations could not find it.

---

## Manifest

| File | sha256 |
|---|---|
| `o3_compute.py` | see the manifest in the delivery message |
| `o3_results.json` | see the manifest in the delivery message |

(This file's own hash cannot appear inside it. All three hashes are in the delivery message.)
