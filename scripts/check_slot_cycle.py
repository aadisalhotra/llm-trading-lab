"""Real-time missed-cycle watcher: did this slot produce a cycle?

Run by .github/workflows/slot_watch.yml, which intraday.yml's chain job dispatches
right after it dispatches a slot:

    python scripts/check_slot_cycle.py --slot 2026-11-16T20:30:00Z

It polls origin/main for a decision record whose fetch_started_at falls inside
the slot (src/cycle_watch.py defines a cycle). Outcomes:

  * a record appears by the deadline  -> PASS, no email, exit 0
  * none by the deadline              -> CRITICAL "slot HH:MM produced no cycle"
  * a record appears after the CRITICAL but still inside the slot -> a WARN
    follow-up saying the cycle landed late, so the CRITICAL is not left standing
    as false. (A run that starts in the NEXT slot's window never counts for this
    one: that slot was missed.)

DEADLINE = slot + 15 min. The hub asked for "about 10". Measured over the 76
intraday tick commits from 2026-11-06 to 2026-11-16, a tick commits at a median
of slot + 5.1 min, p90 + 6.0, max + 9.4 (2026-11-10 15:39:23Z). Ten minutes
would leave 0.6 min of margin over the worst observed tick and page on a healthy
slow cycle. Fifteen leaves 5.6.

The watcher only DETECTS. It never dispatches, re-runs or touches the order path,
and it commits nothing: the durable completeness record is written by the EOD
sweep (src/alerts/events.py detect_missed_cycles), which already commits.

STDLIB ONLY (tests/test_stdlib_only_entry_points.py): the workflow installs
nothing, so a dependency fault cannot silence it. It reads origin/main with the
git CLI that actions/checkout leaves on the runner.

Exit codes: 0 = verdict delivered (PASS, or the CRITICAL was sent); 1 = a CRITICAL
was due but the email failed, or origin could not be read by the deadline (the
red run is then the signal); 2 = bad arguments.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alerts.email_alerts import send_email  # noqa: E402
from src.alerts.events import render_event_html  # noqa: E402
from src.config_loader import force_utf8_console, load_env, load_settings  # noqa: E402
from src.cycle_watch import (  # noqa: E402
    SLOT, admission_log_name, books_in_slot, classify_slot, month_log_name, parse_utc,
    slot_label, withhold_in_slot,
)

logger = logging.getLogger("check_slot_cycle")

DEADLINE = timedelta(minutes=15)
FIRST_LOOK = timedelta(minutes=3)      # no tick has ever committed before slot + 4 min
POLL_SECONDS = 60
SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:(00|30):00Z$")
# The day's NYSE close as the chain's gate read it (hub H-OPS-41): "HH:MM", or
# "closed" on a day with no session. Empty = not given (full session assumed,
# the behaviour before this input existed).
CLOSE_RE = re.compile(r"^(closed|\d{2}:\d{2})$")
EASTERN = ZoneInfo("America/New_York")


def outside_session(slot: datetime, close_et: str) -> str | None:
    """Why `slot` has no session to watch, or None. A slot starting at or
    after the day's close cannot produce a cycle: the pipeline's own market-
    open check skips it, and a watch would page CRITICAL for nothing (the
    13:00-15:30 pages after the 2026-11-27 early close). On a day with no
    session ("closed") every slot is outside it (the 2026-11-26 pages)."""
    if not close_et:
        return None
    if close_et == "closed":
        return "no NYSE session today"
    hh, mm = (int(x) for x in close_et.split(":"))
    et = slot.astimezone(EASTERN)
    if (et.hour, et.minute) >= (hh, mm):
        return f"slot starts at or after the {close_et} ET close"
    return None

Reader = Callable[[list[str], datetime], "dict[str, list[str]] | None"]
# The slot's admission file lines, read from the same fetch; None = unreadable.
AdmissionReader = Callable[[datetime], "list[str] | None"]
Sender = Callable[[str, str, str], bool]


def enabled_books(settings: dict) -> list[str]:
    return sorted(k for k, cfg in (settings.get("models") or {}).items()
                  if (cfg or {}).get("enabled", True))


def git_reader(repo: Path) -> Reader:
    """Fetch origin/main and read each book's month log from it, without
    touching the worktree. None = origin could not be read this poll."""
    def read(books: list[str], slot: datetime) -> dict[str, list[str]] | None:
        fetch = subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "--depth=1", "origin", "main"],
                               capture_output=True, text=True, timeout=120)
        if fetch.returncode != 0:
            logger.warning("git fetch failed: %s", fetch.stderr.strip()[:300])
            return None
        out: dict[str, list[str]] = {}
        for book in books:
            path = f"data/trades/{month_log_name(book, slot)}"
            show = subprocess.run(["git", "-C", str(repo), "show", f"FETCH_HEAD:{path}"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=60)
            # A path absent from main (first slot of a month) is an empty log.
            out[book] = show.stdout.splitlines() if show.returncode == 0 else []
        return out
    return read


def git_admission_reader(repo: Path) -> AdmissionReader:
    """Read the slot's admission file from FETCH_HEAD, which `git_reader` has
    just fetched in the same poll. A path absent from main reads as no lines
    (no withhold this month); any other failure is None."""
    def read(slot: datetime) -> list[str] | None:
        path = f"data/admission/{admission_log_name(slot)}"
        show = subprocess.run(["git", "-C", str(repo), "show", f"FETCH_HEAD:{path}"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=60)
        if show.returncode == 0:
            return show.stdout.splitlines()
        if "does not exist" in show.stderr or "exists on disk, but not in" in show.stderr:
            return []
        logger.warning("could not read %s from origin: %s", path, show.stderr.strip()[:300])
        return None
    return read


def email_sender(settings: dict) -> Sender:
    def send(severity: str, title: str, body: str) -> bool:
        now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime(
            "%Y-%m-%d %H:%M ET")
        html = render_event_html("missed_cycle", severity, title, body, None, now_et)
        return send_email(f"[ALERT] {title}", html, text_body=re.sub(r"<[^>]+>", "", body),
                          alert_type="event", trigger="missed_cycle", settings=settings)
    return send


def summary(line: str) -> None:
    """Append to the Actions job summary when running in CI; always log."""
    logger.info(line)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("could not write job summary: %s", exc)


def watch(slot: datetime, books: list[str], reader: Reader, send: Sender,
          now: Callable[[], datetime], sleep: Callable[[float], None],
          deadline: timedelta = DEADLINE, admission: AdmissionReader | None = None) -> int:
    label = slot_label(slot)
    due = slot + deadline
    slot_end = slot + SLOT
    if now() < slot + FIRST_LOOK:
        sleep((slot + FIRST_LOOK - now()).total_seconds())

    critical_sent = False
    ever_read = False
    while True:
        t = now()
        logs = reader(books, slot)
        if logs is not None:
            ever_read = True
            counts = books_in_slot(logs, slot)
            if classify_slot(counts) != "MISSED_CYCLE":
                have = sorted(b for b, n in counts.items() if n > 0)
                if not critical_sent:
                    summary(f"PASS slot {label} ET: cycle present ({len(have)}/{len(books)} books) at "
                            f"{t:%H:%M:%S}Z")
                    return 0
                title = f"Slot {label} cycle landed late"
                body = (f"The slot {label} ET cycle was reported missing at {due:%H:%M}Z, but a "
                        f"decision record inside the slot appeared at {t:%H:%M}Z "
                        f"({len(have)}/{len(books)} books). The cycle ran late; it did run.")
                ok = send("WARN", title, body)
                summary(f"LATE slot {label} ET: record appeared at {t:%H:%M:%S}Z; follow-up "
                        f"{'sent' if ok else 'NOT sent'}")
                return 0
        if not critical_sent and t >= due:
            if not ever_read:
                summary(f"UNVERIFIED slot {label} ET: origin/main unreadable through {t:%H:%M:%S}Z")
                return 1
            title = f"Slot {label} produced no cycle"
            # If the slot's admission record is visible, say so: the miss has a
            # registered cause (hub ruling 2026-11-17). The page itself stays.
            withheld = withhold_in_slot((admission(slot) or []) if admission else [], slot)
            if withheld is not None:
                body = (f"No book wrote a decision record for the <b>{label} ET</b> slot "
                        f"({slot:%Y-%m-%d %H:%M}Z): it was <b>withheld by the admission "
                        f"guard</b> ({withheld.get('event')}, guard {withheld.get('guard')}, "
                        f"reason {withheld.get('reason')}, at {withheld.get('timestamp')}). "
                        f"No model was prompted and no order was submitted. The EOD sweep "
                        f"records this slot under that cause, not as a missed cycle. Repair "
                        f"the file the admission record names; every tick withholds until then.")
            else:
                body = (f"No book wrote a decision record for the <b>{label} ET</b> slot "
                        f"({slot:%Y-%m-%d %H:%M}Z) by {due:%H:%M}Z, the watch deadline. "
                        f"The chain dispatched this slot; a dispatched run that never starts, or "
                        f"starts too late, looks exactly like this. Nothing was re-dispatched. "
                        f"Check the Actions run for the slot and the broker for orders.")
            ok = send("CRITICAL", title, body)
            summary(f"{'WITHHELD' if withheld is not None else 'MISSED'} slot {label} ET: no "
                    f"record by {due:%H:%M}Z; CRITICAL {'sent' if ok else 'NOT sent'}")
            if not ok:
                return 1
            critical_sent = True
        if critical_sent and t >= slot_end:
            # Past the slot: no record can count for it any more. Verdict stands.
            return 0
        sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    force_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slot", required=True, help="slot start, UTC, YYYY-MM-DDTHH:(00|30):00Z")
    ap.add_argument("--close-et", default="", help="the day's NYSE close, ET HH:MM, or 'closed'")
    args = ap.parse_args(argv)
    # The value arrives from a workflow_dispatch input: validate strictly.
    if not SLOT_RE.match(args.slot or ""):
        logger.error("--slot must be YYYY-MM-DDTHH:(00|30):00Z, got %r", args.slot)
        return 2
    close_et = (args.close_et or "").strip()
    if close_et and not CLOSE_RE.match(close_et):
        logger.error("--close-et must be HH:MM or 'closed', got %r", close_et)
        return 2
    why = outside_session(parse_utc(args.slot), close_et)
    if why:
        summary(f"SKIP slot {slot_label(parse_utc(args.slot))} ET: {why}; not watched")
        return 0
    load_env()
    settings = load_settings()
    books = enabled_books(settings)
    if not books:
        logger.error("no enabled books in settings; nothing to watch")
        return 2
    return watch(parse_utc(args.slot), books, git_reader(ROOT), email_sender(settings),
                 now=lambda: datetime.now(timezone.utc), sleep=_time.sleep,
                 admission=git_admission_reader(ROOT))


if __name__ == "__main__":
    sys.exit(main())
