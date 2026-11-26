"""Slot-identity guard replay (slot_identity_guard_ruling_2026-11-21 section 4).

For each intraday tick commit reachable from REF since SINCE, the guard in this
working tree is run against that tick's committed pre-state (the parent's
decision logs), at the tick's own start (its cycle_id), exactly as the run
would have checked it. It reports, per tick: the slot, the start offset, the
late-start flag (more than 8.5 minutes in, hub H-OPS-05) and the new verdict.

The requirement (section 1): a tick that starts inside its slot's window is
never withheld or skipped because of its predecessor. A tick that started
after the window is reported as a slot the new rule would have recorded as
missed; that is the rule working, not a difference.

Exit 1 when any tick that started inside the window would not be admitted, or
a guard read would withhold. Read-only: blobs go to a temp directory.

Usage:  python scripts/replay_slot_guard.py [--ref main] [--since 2026-11-11]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from replay_gate_a_verdicts import SUBJECT, _blob, _git, _tick_start  # noqa: E402
from src.logging import tick_spacing  # noqa: E402

from datetime import datetime, timezone  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Slot-identity guard replay")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--since", default="2026-11-11")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    settings = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8"))
    books = [k for k, v in settings["models"].items() if (v or {}).get("enabled")]
    window = tick_spacing.start_window_minutes(settings)

    log = _git("log", args.ref, f"--since={args.since}T00:00:00", "--format=%H %P|%s")
    ticks = []
    for line in log.splitlines():
        head, subject = line.split("|", 1)
        m = SUBJECT.match(subject)
        parts = head.split()
        if m and len(parts) == 2:
            ticks.append((parts[0], parts[1], datetime.strptime(
                m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)))
    ticks.sort(key=lambda t: t[2])

    rows, failures, missed, flagged = [], 0, [], []
    for commit, parent, stamp in ticks:
        month = stamp.strftime("%Y-%m")
        start = _tick_start(commit, parent, month, books, stamp)
        with tempfile.TemporaryDirectory() as tmp:
            trades = Path(tmp)
            for book in books:
                blob = _blob(parent, f"data/trades/{book}_{month}.jsonl")
                if blob is not None:
                    (trades / f"{book}_{month}.jsonl").write_bytes(blob)
            v = tick_spacing.check_spacing(settings, start, trades)
        f = tick_spacing.slot_fields(start)
        inside = f["slot_start_offset_s"] is not None and f["slot_start_offset_s"] < window * 60
        if v.withhold or (inside and v.skip):
            failures += 1
            verdict = "FAIL"
        elif v.missed:
            missed.append((f["slot_id"], f["slot_start_offset_s"]))
            verdict = "missed (late start)"
        elif v.skip:
            verdict = f"skip ({v.reason})"
        else:
            verdict = "admit"
        if f["late_start"]:
            flagged.append((f["slot_id"], f["slot_start_offset_s"]))
        rows.append((commit[:9], start.strftime("%Y-%m-%d %H:%M:%SZ"), str(f["slot_id"]),
                     f"{f['slot_start_offset_s']:.0f}" if f["slot_start_offset_s"] is not None else "-",
                     "yes" if f["late_start"] else "", v.reason, verdict))

    out = ["| tick | start (cycle_id) | slot | offset s | late_start | reason | verdict |",
           "|---|---|---|---|---|---|---|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    out += ["", f"{len(rows)} ticks replayed (window {window} min). "
                f"{len(rows) - len(missed) - failures} admitted or duplicate-skipped, "
                f"{len(missed)} would be missed slots, {len(flagged)} flagged late_start, "
                f"{failures} failure(s).",
            "missed: " + (", ".join(f"{s} ({o:.0f} s)" for s, o in missed) or "none"),
            "late_start: " + (", ".join(f"{s} ({o:.0f} s)" for s, o in flagged) or "none")]
    sys.stdout.write("\n".join(out) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
