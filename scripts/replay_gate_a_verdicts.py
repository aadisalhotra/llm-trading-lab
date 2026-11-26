"""Gate A verdict replay — condition 2 of tick_admission_guards_ruling.md §3.

    "For every tick since 2026-11-11, the new guard code, run against that
    tick's committed pre-state, returns *proceed* for every book. Zero
    differences on the validated record."

For each intraday tick commit reachable from REF since SINCE, the pre-state is
that commit's parent: the ledger and every enabled book's decision log exactly
as the tick's run checked them out. Both guards are run twice against it —
the code at BASE (before the CRITICAL and before Gate A) and the code in this
working tree — and the verdicts are compared.

A tick passes when the new code does not withhold, the new ledger load has no
error, and the new spacing verdict's (skip, reason) equals BASE's. The tick's
start time is its own cycle_id (read from the decision rows the tick added);
a tick that added no row (a spacing skip) falls back to its subject stamp.

Read-only: blobs are read with `git cat-file` into a temp directory; nothing
under data/ is opened. Prints a Markdown table and exits 1 on any difference.

Usage:  python scripts/replay_gate_a_verdicts.py [--ref main] [--since 2026-11-11]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.logging import boundary_ledger as new_ledger  # noqa: E402
from src.logging import tick_spacing as new_spacing  # noqa: E402

logger = logging.getLogger("llmlab.replay_gate_a")

BASE = "71d025fb4"
SUBJECT = re.compile(r"^intraday tick (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$")
CYCLE_FMT = "%Y%m%dT%H%M%SZ"
SLOT_REASON = {"spaced": "slot_open", "too_soon": "slot_taken", "no_prior_tick": "no_prior_tick"}
EASTERN = "America/New_York"


def _git(*args: str, binary: bool = False):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode("utf-8")


def _load_base(path: str, name: str) -> types.ModuleType:
    src = _git("show", f"{BASE}:{path}")
    mod = types.ModuleType(name)
    mod.__package__ = "src.logging"
    exec(compile(src, f"{path}@{BASE}", "exec"), mod.__dict__)
    return mod


def _blob(commit: str, path: str) -> bytes | None:
    try:
        return _git("show", f"{commit}:{path}", binary=True)
    except subprocess.CalledProcessError:
        return None


def _tick_start(commit: str, parent: str, month: str, books: list[str],
                fallback: datetime) -> datetime:
    """The tick's own cycle_id, from the first decision row it added."""
    for book in books:
        path = f"data/trades/{book}_{month}.jsonl"
        diff = subprocess.run(["git", "-C", str(ROOT), "diff", "-U0", parent, commit, "--", path],
                              capture_output=True).stdout.decode("utf-8", "replace")
        for line in diff.splitlines():
            if line.startswith("+{"):
                try:
                    cyc = json.loads(line[1:]).get("cycle_id") or ""
                    return datetime.strptime(cyc, CYCLE_FMT).replace(tzinfo=timezone.utc)
                except (ValueError, json.JSONDecodeError):
                    break
    return fallback


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate A verdict replay (condition 2)")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--since", default="2026-11-11")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    from zoneinfo import ZoneInfo
    et = ZoneInfo(EASTERN)

    old_spacing = _load_base("src/logging/tick_spacing.py", "src.logging._ts_base")
    old_ledger = _load_base("src/logging/boundary_ledger.py", "src.logging._bl_base")
    settings = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8"))
    books = [k for k, v in settings["models"].items() if (v or {}).get("enabled")]

    log = _git("log", args.ref, f"--since={args.since}T00:00:00", "--format=%H %P|%s")
    ticks = []
    for line in log.splitlines():
        head, subject = line.split("|", 1)
        m = SUBJECT.match(subject)
        parts = head.split()
        if m and len(parts) == 2:                       # a tick: exactly one parent
            ticks.append((parts[0], parts[1], datetime.strptime(
                m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)))
    ticks.sort(key=lambda t: t[2])

    rows, failures = [], 0
    for commit, parent, stamp in ticks:
        month = stamp.strftime("%Y-%m")
        start = _tick_start(commit, parent, month, books, stamp)
        with tempfile.TemporaryDirectory() as tmp:
            tmpd = Path(tmp)
            trades = tmpd / "trades"
            trades.mkdir()
            for book in books:
                blob = _blob(parent, f"data/trades/{book}_{month}.jsonl")
                if blob is not None:
                    (trades / f"{book}_{month}.jsonl").write_bytes(blob)
            ledger_path = tmpd / "handled_boundaries.json"
            lblob = _blob(parent, "data/state/handled_boundaries.json")
            if lblob is not None:
                ledger_path.write_bytes(lblob)
            o_led, o_err = old_ledger.load_ledger(ledger_path)
            n_led, n_err = new_ledger.load_ledger(ledger_path)
            day, slot = new_ledger.boundary_parts(start.astimezone(et))
            handled = new_ledger.is_boundary_handled(n_led, day, slot)
            ov = old_spacing.check_spacing(settings, start, trades)
            nv = new_spacing.check_spacing(settings, start, trades)
        # Slot identity (2026-11-26) renamed the verdicts: an open slot was
        # "spaced", a slot the cohort already decided was "too_soon". Same skip.
        ok = (n_err is None and o_err is None and not nv.withhold and not handled
              and (nv.skip, nv.reason) == (ov.skip, SLOT_REASON.get(ov.reason, ov.reason)))
        failures += not ok
        rows.append((commit[:9], start.strftime("%Y-%m-%d %H:%M:%SZ"), f"{day} {slot}",
                     "ok" if n_err is None else f"ERR {n_err}",
                     f"{ov.skip}/{ov.reason}", f"{nv.skip}/{nv.reason}/withhold={nv.withhold}",
                     "proceed" if ok and not nv.skip else ("skip" if ok else "DIFF")))

    out = ["| tick | start (cycle_id) | boundary | ledger (new) | spacing BASE | spacing new | verdict |",
           "|---|---|---|---|---|---|---|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    out.append("")
    out.append(f"{len(rows)} ticks replayed, {failures} difference(s). BASE = {BASE}.")
    sys.stdout.write("\n".join(out) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
