#!/usr/bin/env python
"""CI preflight: fail the run when an alerting channel is wired but cannot deliver.

Exits 1 with a readable diagnosis when a channel's recipient secret is missing
while Gmail credentials are present; exits 0 otherwise. That non-zero exit is
the whole point — it is the only failure signal in this class that does not
itself depend on email working (see src/alerts/preflight.py).

Where this runs, and why it runs there rather than somewhere simpler:

  * `intraday.yml` — as its OWN job, needed by nothing. A step inside the `run`
    job would be wrong: `chain` gates on `success()` of `run`, so failing that
    job would break the self-chain and stop the trading day over a missing
    email address. An independent job reds the workflow (visible in the Actions
    UI, and GitHub emails the actor on a failed run through its own channel)
    while `run` -> `chain` proceeds untouched.
  * `competitor_monitor.yml` — as a step BEFORE the scan. Failing early is
    correct there: no trading is at stake, and a scan that cannot escalate is
    exactly the defect that went unnoticed.

Usage:
    python scripts/check_alerting_config.py --channel daily
    python scripts/check_alerting_config.py --channel competitor
    python scripts/check_alerting_config.py --channel all      # default
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.alerts import preflight  # noqa: E402
from src.config_loader import force_utf8_console, load_env, load_settings  # noqa: E402

CHOICES = {
    "daily": (preflight.DAILY,),
    "competitor": (preflight.COMPETITOR,),
    "all": preflight.ALL_CHANNELS,
}


def main() -> int:
    force_utf8_console()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    ap = argparse.ArgumentParser(description="Alerting configuration preflight")
    ap.add_argument("--channel", choices=sorted(CHOICES), default="all",
                    help="Which alerting channel(s) to verify (default: all)")
    args = ap.parse_args()

    load_env()
    try:
        settings = load_settings()
    except Exception:  # noqa: BLE001 — a settings read failure must not mask the check
        settings = None

    channels = CHOICES[args.channel]
    problems = preflight.check(channels, settings)

    if not problems:
        if not preflight.transport_is_wired():
            print("OK — Gmail credentials are not set; email is deliberately off here.")
        else:
            print(f"OK — every checked channel can deliver ({', '.join(channels)}).")
        return 0

    print("")
    print("ALERTING MISCONFIGURED — wired code, absent secret, silent skip.")
    print("")
    for p in problems:
        print(f"  channel : {p['channel']}")
        print(f"  secret  : {p['env_var']}")
        print(f"  affects : {p['purpose']}")
        print(f"  detail  : {p['reason']}")
        print("")
    print("This job fails on purpose. Without it the sends are skipped silently and")
    print("the failure notifier cannot report the fault, because it is the same")
    print("channel. Set the secret, then re-run.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
