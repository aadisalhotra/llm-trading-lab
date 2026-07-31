"""Holiday short-circuit gate for the chain/pages CI jobs.

Prints exactly "true" (today is an NYSE session) or "false" (holiday /
non-trading day) to stdout — the single value the workflow's gate job captures
into $GITHUB_OUTPUT. On a non-trading day the chain job's ~30-minute
sleep-and-dispatch loop and the pages deploy are pure churn (the pipeline
no-ops every tick), so the workflow skips them; the run job itself still fires
from the backup crons and stays the authority on market state.

Decision source: src.data.market_data.is_market_open_today — the SAME
pandas_market_calendars NYSE calendar the pipeline's own is_market_open_now()
gate uses, evaluated on the America/New_York date.

FAIL-OPEN: any failure (missing dependency, calendar error) prints "true" —
wrongly stopping the chain on a real trading day degrades the primary trigger
to the backup crons, which is the harmful direction; a wasted holiday tick is
benign. Diagnostics go to stderr via logging, never stdout (stdout is the
value contract).

Run:  python scripts/check_trading_day.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(levelname)s %(message)s")
logger = logging.getLogger("llmlab.trading_day_gate")


def main() -> int:
    try:
        from zoneinfo import ZoneInfo

        from src.data.market_data import is_market_open_today

        et_now = datetime.now(ZoneInfo("America/New_York"))
        open_today = bool(is_market_open_today(et_now))
        logger.info("ET date %s -> NYSE session: %s", et_now.date(), open_today)
        print("true" if open_today else "false")
    except Exception:
        logger.exception("Trading-day gate failed — failing OPEN (true)")
        print("true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
