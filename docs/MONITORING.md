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
