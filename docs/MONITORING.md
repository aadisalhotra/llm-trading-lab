# Monitoring — liveness dead-man's-switch

## What this is

The intraday pipeline pings an **external watchdog** every time a decision tick
lands. If the watchdog stops hearing pings during market hours, *it* alerts —
over its own infrastructure, independent of this pipeline, GitHub Actions, and
the Gmail alerter.

This exists because of the 2026-05-26 outage: the self-chain silently dropped a
dispatch at 15:00 UTC, the workflow reported `success`, and the lab made **zero
decisions for ~2 hours** with no alert. The in-process email alerter can't catch
that — it only runs when a tick runs. A monitor that watches for the *absence*
of decisions, from outside, is the only thing that does.

The ping fires from `src/alerts/heartbeat.py`, called in `src/pipeline.py` at
exactly one place: the successful completion of an **intraday** tick, after the
market-hours guard has passed and all models have run. A market-closed no-op
does not ping. EOD does not ping (it runs outside the watchdog's window). A
fatal intraday abort (no prices) pings `/fail` to trip the watchdog instantly.

**Until `HEARTBEAT_URL` is set, the code is a clean no-op** — it logs
"HEARTBEAT_URL not set" and returns. Deploying it changes nothing about trading.

## Provisioning (one-time, ~10 minutes)

### 1. Create the check (Healthchecks.io — free tier is enough)

1. Sign up at <https://healthchecks.io> and create a new check named
   `llm-trading-lab — intraday liveness`.
2. Set the schedule to a cron expression scoped to the NYSE regular session.
   **Use the `America/New_York` timezone, not UTC** — Healthchecks evaluates the
   cron in that zone and so handles the EDT/EST shift automatically. A fixed-UTC
   schedule would be an hour off all winter and false-alarm every day from
   November to March.

   - **Schedule (cron):** `0,30 9-15 * * 1-5`
   - **Timezone:** `America/New_York`
   - **Grace period:** `45 minutes`

   This expects a ping on each :00/:30 boundary from 9:00–15:30 ET Mon–Fri. The
   last decision tick of the day is the 15:30 ET tick (the 16:00 ET boundary is
   the close, so `is_market_open_now()` returns false and it makes no decision);
   after it, the next expected slot is 9:00 ET the following weekday, so evenings
   and overnight raise no alarm.

   **Why 45 minutes, not tight:** the 9:00 ET slot is a phantom — the pre-open
   tick at 9:00 ET runs but the market isn't open until 9:30, so it makes no
   decision and sends no ping. The first real ping of the day lands ~9:38 ET
   (the 9:30 tick plus ~6–8 min of model runtime). A 45-min grace bridges that
   9:00→9:38 morning gap with margin, and absorbs slow ticks the rest of the
   day, so there are **no routine false alarms**. The cost is detection latency:
   a genuine silent outage is caught in roughly **45–75 minutes** (one to two
   missed ticks plus grace). That's a night-and-day improvement over the ~2-hour
   *silent* gap that triggered this — and the arm/disarm enhancement below is
   what lets you drop the grace to ~12 min (≈30–40 min detection) without
   reintroducing the morning false alarm.

3. Point the check's **Integrations** at a *loud* channel — not just email.
   Email + SMS (or Telegram / a phone-push app like Pushover) means a 2-hour
   outage reaches you in ~12–15 minutes even if you're away from the inbox.

4. Copy the **ping URL** (looks like `https://hc-ping.com/<uuid>`).

### 2. Wire the secret

- **GitHub Actions:** repo → Settings → Secrets and variables → Actions → New
  repository secret. Name: `HEARTBEAT_URL`. Value: the ping URL.
- **Local `.env`:** add `HEARTBEAT_URL=https://hc-ping.com/<uuid>`.
  ⚠️ On Windows, confirm the file is actually `.env` and not `.env.txt` —
  Explorer hides the real extension. If it's `.env.txt`, `load_env()` won't find
  it and the local test below will report the URL unset.

### 3. Test the wiring

```
python -m src.alerts.heartbeat
```

Should print `Test heartbeat SENT` and the check on healthchecks.io should flip
to a green "up" state with a fresh ping timestamp.

## Holiday handling (a known limitation of the v1 schedule)

The cron schedule above does not know the NYSE holiday calendar, so on the ~10
market holidays per year (and the afternoon of early-close half-days) the
pipeline correctly makes no decisions, the watchdog still expects pings, and you
get a single false "down" alert on a day you already know the market is shut.

This is the deliberate v1 trade-off: zero extra secrets, zero extra code, ~10
predictable false positives a year. The clean fix — **arm/disarm**, where the
pipeline tells the watchdog to start expecting pings at the first tick of a
trading day and to pause after EOD — needs the Healthchecks management API key
(a second secret) and pause calls in the pipeline. It's tracked as a future
enhancement; v1 ships without it.

## Settings

`config/settings.json` → `alerts.heartbeat`:

```json
"heartbeat": { "enabled": true, "timeout_seconds": 10 }
```

`enabled` gates the ping independently of `alerts.enabled` — muting email must
not blind the liveness monitor. `timeout_seconds` caps how long a ping can stall
the tail of a tick (it never blocks trading; the tick is already complete).

## Relationship to the other monitoring layers

- **`notify-failure` job (intraday.yml):** catches workflow *failure*. Blind to
  a successful-but-decisionless run — the exact 2026-05-26 case. The heartbeat
  closes that gap.
- **B-secondary, in-repo log verifier (planned):** reads the committed decision
  logs and emails a diagnostic-rich alert; holiday-accurate via
  `is_market_open_now()`. Complements this watchdog; does not replace it (it runs
  on GitHub, so it can't survive a GitHub-wide outage the way this can).

## Known blind spot — data completeness is not deploy freshness

**Logged 2026-08-10 for the mid-month batch. Not fixed; scoped here so the next
fix targets the right symptom.**

`scripts/check_dashboard_staleness.py` answers one question: *is the published
artifact keeping up with what we built?* It fetches the live dashboard's
`generated_at` and compares it to the **locally committed** dashboard build. It
does not, and cannot, answer *did today produce a full trading day of data?*

Those come apart when the chain stops committing. On **2026-08-06** the intraday
chain halted after 5 of 13 decision cycles (~11:35 ET) and never ran the EOD
wrap — no performance row, no daily report, no digest, for all six models. The
staleness monitor stayed quiet and was *correct* to: the pipeline committed its
5 ticks and then stopped, so the public artifact and the local build were
equally fresh. Nothing failed to publish. The day published **current-but-stale**
and the monitor has no vocabulary for that.

The uncomfortable part is that this is the *same property* that makes the
monitor holiday-safe. Measuring public-vs-local rather than public-vs-wall-clock
is exactly why a market holiday doesn't page us — the pipeline commits no new
tick, so both sides age together. A mid-session chain death is indistinguishable
from a holiday by that test. The holiday-safety and the blind spot are one
mechanism, so this is not a threshold to retune; a completeness check is a
different instrument.

This is the same wrong-symptom class as the pre-fix Gemini blind spot: an alert
that fires reliably on the symptom it watches, while the failure that actually
costs data expresses itself as a different symptom entirely. Getting the
threshold right on the wrong quantity buys nothing.

What would actually close it: a check on **expected vs logged cycles for the
trading day** — count the day's decision records against the session's expected
boundary count and page when the session ends short. That is squarely the
planned **B-secondary in-repo log verifier**'s job (above); it reads the
committed decision logs, which is the surface where this failure is visible.
Fold this requirement into that build rather than bolting a second check onto
the staleness monitor, whose contract is deliberately narrow.

Incident record: `scripts/phase_a_integrity_ledger.json` →
`operational_events["2026-08"].cycle_gap_2026_08_06`.

### Mid-month batch item 2 — `_previous_leaderboard()` must validate completeness

**Logged 2026-08-10 from the same incident. FIXED 2026-08-10** — the predicate
now gates on a complete cohort EOD (`_complete_eod_dates()`), walking back when
a date is incomplete. The published 2026-08-07 arrows are unchanged: the fix
skips 08-06 and lands on 08-05, which is the same answer the old selector
reached by content. Kept below as the record of what was wrong and why.

`src/reports/daily_report.py::_previous_leaderboard()` selects the previous
day's rank baseline by globbing `data/leaderboard/*.json`, keeping stems
strictly less than the run date, and taking the newest. The predicate is
**"prior-dated"**. It needs to be **"prior-dated AND a complete six-model EOD
snapshot."**

The 2026-08-06 file satisfies the current predicate and is not an EOD snapshot
at all — it is a mid-session intraday write, frozen when the chain halted,
carrying the 2026-08-05 close. The 2026-08-07 report consumed it for rank
arrows. Those arrows came out **correct**, because that file holds the 08-05
ranks and 08-05 was the true prior close — correct by content, not by rule.

The rule fails on a case only slightly different from the one we got: a chain
halt *later* in a session, after some models had written updated rows and
others had not, produces a snapshot that is genuinely mixed — part today, part
yesterday — and the selector would propagate it as "yesterday" with no
complaint. Same failure at a different clock time, no longer benign.

Fix shape: gate selection on the day having a complete EOD — all six models
present in `data/performance/{model}.jsonl` for that date — and walk further
back when it doesn't, rather than trusting the filename. Note this is the same
defect as the blind spot above wearing different clothes: an instrument
selecting on an available-but-wrong predicate (freshness there, file date here)
when the predicate that matters is data completeness. Worth fixing both against
one definition of "a complete trading day" rather than twice, separately.

The 2026-08-06 file itself now carries a per-row `_staleness` marker
(`data_as_of: 2026-08-05`, `is_observation_for_file_date: false`, ledger ref) so
it discloses its own staleness to anything that opens it. That marker is a
one-off disclosure on a known-bad artifact, **not** the fix — the selector must
not come to depend on hand-annotated files.

## Per-boundary idempotency — and the concurrency-group tripwire

`src/logging/boundary_ledger.py` enforces exactly-once decision-making per
30-minute **intraday** tick. The pipeline records each boundary in
`data/state/handled_boundaries.json` once its decisions are made, and checks that
ledger before prompting any model, so a duplicate run for the same boundary
(a Fix A dispatch retry, a manual force-run, a backup-cron double-fire, or
multi-trigger redundancy) cleanly no-ops instead of double-trading. The ledger is
committed in the same commit as the decision logs, so a boundary's marker exists
iff its decisions were durably committed.

The **EOD wrap-up is deliberately not guarded**: it executes no trades (the
`is_eod` early-return in `run_one_model` does snapshot/report only), so it carries
no double-trade risk and keeps its own per-day idempotency (`log_daily_snapshot`,
the once-per-day digest) plus its intentional two-trigger redundancy (chain
post-close handoff + the 21:00 UTC cron). Guarding it would needlessly neuter
that redundancy.

> ⚠️ **TRIPWIRE — do not remove the intraday concurrency group.** The guard is a
> read-check-then-write and is race-free *only* because
> `concurrency: group: intraday-pipeline, cancel-in-progress: false` in
> `.github/workflows/intraday.yml` serializes runs — each run checks out the
> prior run's committed ledger before it starts, so two runs for the same
> boundary never execute at once. **The future Path 2 (stateless external
> scheduler) rework must keep that concurrency group, or first add the atomic
> claim-commit hardening** (commit+push a claim before the model loop; abort if
> the push is rejected non-fast-forward). Without serialization, two concurrent
> runs could both pass the check before either writes — and both would trade.

**Known limitation (v1, accepted for Phase A paper only):** the boundary is
marked at *completion*. A hard kill mid-model-loop (runner eviction / OOM) after
some models have filled but before the commit leaves the boundary unmarked, so a
retry re-trades the models that already filled. This is a real-money double-trade
risk in **Phase B** and is tracked as **"per-model idempotency"** on the
pre-Phase-B list. Do not go live with real money without it.

If the ledger is ever unreadable (corrupt JSON), the guard **fails open**
(proceeds, so it can never silently halt trading) and fires a CRITICAL email
*plus* a watchdog `/fail` ping — a loud SMS/push page, not a silent log line.

---

## Competitor paper monitor — delivery loop (permanent, ratified 2026-08-13)

`scripts/competitor_monitor.py` runs every Monday 14:00 UTC
(`.github/workflows/competitor_monitor.yml`) and writes
`reports/competitor_digest_YYYY-WW.md` plus the queryable
`reports/competitor_index.jsonl`.

**The relay expectation is part of the monitor, not an afterthought.** Each
weekly digest *and its heartbeat line* is surfaced to the Synthesis Hub through
the PI relay — **including weeks with zero hits**. A monitor whose output
reaches no reader is not a monitor; that is the exact defect diagnosed on
2026-08-13, when the structured index turned out never to have been written by
any scheduled run because the feature landed hours after that week's run.

**Heartbeat format**, emitted to both the digest body and the CI log so an empty
week is visible without opening a file:

```
[heartbeat] 2026-08-17T14:00:03Z — week of 2026-34: 0 escalations, 3 digest, 5 silent
```

**Escalation is same-day, not weekly.** An ESCALATE-tier hit means a paper may
threaten a registered RQ; it goes to the hub when found, carrying title, link,
venue, date, and a two-line overlap assessment naming the threatened RQ(s). The
weekly digest and the silent log ride the normal Monday cadence.

**Reading the tiers:**

| Tier | What it means | Delivery |
|---|---|---|
| ESCALATE | Meets criterion (a)–(d) of the 2026-08-13 triage spec | Same day, to the hub |
| Digest | Adjacent subject matter — single-model agents, simulated markets, sentiment, benchmarks | Weekly digest, one line each |
| Silent | RL-only, crypto, human-subject, or query-matched but unclassifiable | Logged to the index only |

**Retroactive triage:** `python -m scripts.competitor_monitor --retro-triage`
re-runs the full history through the current criteria and writes nothing. It
reads `competitor_index.jsonl` when present and falls back to parsing the digest
markdown when it is not, reporting which source it used — weeks 22–33 predate
the index and are only recoverable from markdown, where abstracts are truncated
at ~600 characters. That truncation is a real limit on retroactive escalation
and is reported with the results rather than hidden.
