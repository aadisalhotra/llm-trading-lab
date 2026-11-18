"""Missed-cycle detection (2026-11-16 hub dispatch).

The chain proved only that a run was CREATED. On 2026-11-16 the 15:30 ET run was
created on time, held by GitHub with no jobs for 30 minutes, and finished green
after the close, so nothing alerted. The same silent miss happened on 11-03
(14:00), 11-05 (10:00) and 11-11 (15:30).

Covered here:
  * src/cycle_watch.py: slot arithmetic, what counts as a cycle, idempotent recording
  * scripts/check_slot_cycle.py: the real-time watcher's verdicts on a fake clock
  * detect_missed_cycles: the EOD backstop and its durable completeness record
  * the workflow wiring: detection only, and outside the production concurrency group
  * a regression over the real decision logs: exactly the four known misses
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from scripts import chain_schedule as cs
from scripts import check_slot_cycle as watcher
from src import cycle_watch as cw
from src.alerts import events

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
BOOKS = ["claude", "claude_opus", "deepseek", "gemini", "gpt", "grok"]
SLOT_1530 = datetime(2026, 11, 16, 20, 30, tzinfo=UTC)   # 15:30 EST


def rec(fetch: str | None, **extra) -> str:
    r = {"date": "2026-11-16", **extra}
    if fetch is not None:
        r["fetch_started_at"] = fetch
    return json.dumps(r)


# ----------------------------------------------------------------- slot math
def test_full_est_day_has_13_slots_0930_to_1530():
    slots = cw.expected_slots(date(2026, 11, 16), time(16, 0))
    assert [cw.slot_label(s) for s in slots][0] == "09:30"
    assert [cw.slot_label(s) for s in slots][-1] == "15:30"
    assert len(slots) == 13
    assert slots[-1] == SLOT_1530


def test_early_close_has_7_slots_and_none_at_or_after_1300():
    slots = cw.expected_slots(date(2026, 11, 27), time(13, 0))
    assert [cw.slot_label(s) for s in slots] == ["09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30"]


def test_edt_day_slots_are_in_et_not_utc():
    slots = cw.expected_slots(date(2026, 10, 26), time(16, 0))
    assert slots[0] == datetime(2026, 10, 26, 13, 30, tzinfo=UTC)
    assert slots[-1] == datetime(2026, 10, 26, 19, 30, tzinfo=UTC)


# ---------------------------------------------------------- what is a cycle
def test_slot_window_is_half_open_and_reads_fetch_started_at_only():
    lines = {
        "a": [rec("2026-11-16T20:30:00+00:00")],            # first instant: in
        "b": [rec("2026-11-16T20:59:59.999+00:00")],        # last instant: in
        "c": [rec("2026-11-16T21:00:00+00:00")],            # next slot: out
        "d": [rec(None, timestamp="2026-11-16T20:35:00")],  # no fetch_started_at: not a tick
        "e": [rec("2026-11-16T20:29:59+00:00")],            # previous slot: out
    }
    assert cw.books_in_slot(lines, SLOT_1530) == {"a": 1, "b": 1, "c": 0, "d": 0, "e": 0}


def test_a_corrupt_line_is_skipped_not_fatal():
    lines = {"a": ["{not json", rec("2026-11-16T20:32:50Z"), ""]}
    assert cw.books_in_slot(lines, SLOT_1530) == {"a": 1}


def test_naive_fetch_timestamp_reads_as_utc():
    assert cw.books_in_slot({"a": [rec("2026-11-16T20:32:50")]}, SLOT_1530) == {"a": 1}


def test_classification():
    assert cw.classify_slot({"a": 0, "b": 0}) == cw.MISSED_CYCLE
    assert cw.classify_slot({"a": 1, "b": 0}) == cw.PARTIAL_CYCLE
    assert cw.classify_slot({"a": 1, "b": 2}) is None


def test_recording_is_idempotent_on_kind_and_slot(tmp_path):
    ev = {"kind": cw.MISSED_CYCLE, "date": "2026-11-16", "slot_et": "15:30",
          "slot_utc": "2026-11-16T20:30:00Z", "books_expected": BOOKS, "books_with_record": [],
          "detected_by": "eod_sweep", "detected_at": "2026-11-16T22:07:00Z"}
    assert cw.record_events([ev], tmp_path) == 1
    assert cw.record_events([dict(ev, detected_at="2026-11-16T22:50:00Z")], tmp_path) == 0
    rows = (tmp_path / "2026-11.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["slot_et"] == "15:30"


# ------------------------------------------------------- real-time watcher
class Clock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class Mail:
    def __init__(self, ok: bool = True):
        self.sent: list[tuple[str, str, str, datetime]] = []
        self.ok = ok
        self.clock: Clock | None = None

    def __call__(self, severity: str, title: str, body: str) -> bool:
        self.sent.append((severity, title, body, self.clock.t if self.clock else None))
        return self.ok


def reader_from(schedule):
    """schedule: list of (visible_from_utc, book, fetch_started_at). A record is
    on origin/main only once the fake clock reaches its visible_from time."""
    def make(clock: Clock):
        def read(books, slot):
            return {b: [rec(f) for (vis, bb, f) in schedule if bb == b and clock.t >= vis]
                    for b in books}
        return read
    return make


def run_watch(schedule, *, start=None, mail_ok=True, reader=None):
    clock = Clock(start or SLOT_1530 + timedelta(seconds=40))
    mail = Mail(mail_ok)
    mail.clock = clock
    read = reader(clock) if reader else reader_from(schedule)(clock)
    code = watcher.watch(SLOT_1530, BOOKS, read, mail, now=clock.now, sleep=clock.sleep)
    return code, mail, clock


def test_cycle_on_time_passes_silently():
    vis = SLOT_1530 + timedelta(minutes=5, seconds=6)   # the measured median
    code, mail, clock = run_watch([(vis, b, "2026-11-16T20:30:50Z") for b in BOOKS])
    assert code == 0 and mail.sent == []
    assert clock.t < SLOT_1530 + timedelta(minutes=7)


def test_slowest_observed_tick_does_not_page():
    # 2026-11-10 15:39:23Z committed 9.4 min into its slot, the worst of 76.
    vis = SLOT_1530 + timedelta(minutes=9, seconds=23)
    code, mail, _ = run_watch([(vis, "gpt", "2026-11-16T20:31:10Z")])
    assert code == 0 and mail.sent == []


def test_no_cycle_raises_one_critical_at_the_deadline_never_before():
    code, mail, clock = run_watch([])
    assert code == 0
    assert len(mail.sent) == 1
    severity, title, body, at = mail.sent[0]
    assert severity == "CRITICAL"
    assert title == "Slot 15:30 produced no cycle"
    assert at >= SLOT_1530 + watcher.DEADLINE
    assert at < SLOT_1530 + watcher.DEADLINE + timedelta(seconds=watcher.POLL_SECONDS + 1)
    assert "Nothing was re-dispatched" in body
    assert clock.t >= SLOT_1530 + cw.SLOT   # kept watching to the end of the slot


def test_the_2026_11_16_shape_a_run_that_starts_after_the_slot_never_counts():
    # The held run executed at 21:02Z: its fetch would fall in the next slot.
    code, mail, _ = run_watch([(datetime(2026, 11, 16, 21, 2, 10, tzinfo=UTC), "gpt",
                                "2026-11-16T21:02:05Z")])
    assert [m[0] for m in mail.sent] == ["CRITICAL"]


def test_a_late_cycle_inside_the_slot_gets_a_follow_up():
    vis = SLOT_1530 + timedelta(minutes=19)
    code, mail, _ = run_watch([(vis, "gemini", "2026-11-16T20:47:00Z")])
    assert code == 0
    assert [m[0] for m in mail.sent] == ["CRITICAL", "WARN"]
    assert mail.sent[1][1] == "Slot 15:30 cycle landed late"


def test_a_partial_slot_is_not_a_missed_cycle_for_the_watcher():
    vis = SLOT_1530 + timedelta(minutes=5)
    code, mail, _ = run_watch([(vis, "grok", "2026-11-16T20:31:00Z")])
    assert code == 0 and mail.sent == []


def test_a_failed_critical_email_reds_the_run():
    code, mail, _ = run_watch([], mail_ok=False)
    assert code == 1 and len(mail.sent) == 1


def test_unreadable_origin_reds_the_run_and_claims_nothing():
    code, mail, _ = run_watch([], reader=lambda clock: (lambda books, slot: None))
    assert code == 1 and mail.sent == []


def test_a_watcher_started_late_still_judges_its_own_slot():
    # GitHub delays the watcher itself by 40 min: a record that was there all
    # along still passes; it does not invent a miss.
    vis = SLOT_1530 + timedelta(minutes=5)
    code, mail, _ = run_watch([(vis, "gpt", "2026-11-16T20:31:00Z")],
                              start=SLOT_1530 + timedelta(minutes=40))
    assert code == 0 and mail.sent == []


def test_deadline_basis():
    # Measured 2026-11-06..16: max slot-to-commit 9.4 min. The deadline must
    # clear it with margin; the first look must precede the fastest tick (~4 min).
    assert watcher.DEADLINE >= timedelta(minutes=9.4) + timedelta(minutes=5)
    assert watcher.FIRST_LOOK < timedelta(minutes=4)


@pytest.mark.parametrize("bad", ["2026-11-16T20:15:00Z", "2026-11-16 20:30:00", "",
                                 'x"; curl evil', "2026-11-16T20:30:00+00:00"])
def test_slot_argument_is_strictly_validated(bad):
    assert watcher.main(["--slot", bad]) == 2


def test_watcher_never_dispatches_or_writes():
    src = (ROOT / "scripts" / "check_slot_cycle.py").read_text(encoding="utf-8")
    assert "/dispatches" not in src and "actions/workflows" not in src
    assert "git push" not in src and '"push"' not in src
    assert "write_text" not in src


# ---------------------------------------------------------- chain contract
@pytest.mark.parametrize("now,slot", [
    ("2026-11-16T20:30:30+00:00", "2026-11-16T20:30:00Z"),   # the dispatch instant
    ("2026-11-16T20:31:06+00:00", "2026-11-16T20:30:00Z"),   # after a 36 s verify
    ("2026-11-16T14:30:31+00:00", "2026-11-16T14:30:00Z"),   # 09:30 EST
    ("2026-10-26T19:30:30+00:00", "2026-10-26T19:30:00Z"),   # 15:30 EDT
])
def test_current_slot(now, slot, capsys):
    assert cs.main(["--current-slot", "--now", now]) == 0
    assert capsys.readouterr().out.strip() == slot
    assert watcher.SLOT_RE.match(slot)


def test_chain_dispatches_the_watcher_only_after_the_intraday_dispatch():
    wf = (ROOT / ".github" / "workflows" / "intraday.yml").read_text(encoding="utf-8")
    tail = wf[wf.index("          dispatch_intraday\n\n"):]
    assert "slot_watch.yml/dispatches" in tail
    assert "chain_schedule.py --current-slot" in tail
    # a watcher-dispatch failure warns; it never exits the chain non-zero
    block = tail[:tail.index("  pages:")]
    assert "exit 1" not in block


def test_slot_watch_workflow_is_read_only_and_outside_the_production_group():
    wf = (ROOT / ".github" / "workflows" / "slot_watch.yml").read_text(encoding="utf-8")
    assert "intraday-pipeline" not in wf.replace("OUT of the\n# intraday-pipeline group", "")
    assert not re.search(r"^concurrency:", wf, re.M)
    assert re.search(r"^permissions:\n  contents: read\n", wf, re.M)
    assert "SLOT: ${{ github.event.inputs.slot }}" in wf
    run_line = [l for l in wf.splitlines() if l.strip().startswith("run:")]
    assert run_line == ['        run: python scripts/check_slot_cycle.py --slot "$SLOT"']
    code = [l for l in wf.splitlines() if not l.strip().startswith("#")]
    assert not any("pip install" in l for l in code)


# ------------------------------------------------------------- EOD backstop
def _write_day(trades: Path, skip: dict[str, set[str]] | None = None):
    """A full 13-slot 2026-11-16 for every book, minus skip[book] slot labels."""
    skip = skip or {}
    trades.mkdir(parents=True, exist_ok=True)
    for b in BOOKS:
        lines = []
        for s in cw.expected_slots(date(2026, 11, 16), time(16, 0)):
            if cw.slot_label(s) in skip.get(b, set()) or cw.slot_label(s) in skip.get("*", set()):
                continue
            lines.append(rec((s + timedelta(minutes=2, seconds=50)).isoformat()))
        (trades / f"{b}_2026-11.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def eod(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "TRADES_DIR", tmp_path / "trades")
    monkeypatch.setattr(events, "_session_close_et", lambda d: time(16, 0))
    settings = {"models": {b: {"enabled": True} for b in BOOKS}}
    return tmp_path, settings


def test_eod_clean_day_raises_nothing_and_records_nothing(eod):
    tmp, settings = eod
    _write_day(tmp / "trades")
    assert events.detect_missed_cycles(settings, "2026-11-16") == []
    assert not (tmp / "cycle_completeness").exists()


def test_eod_missed_slot_is_one_critical_and_one_durable_event(eod):
    tmp, settings = eod
    _write_day(tmp / "trades", {"*": {"15:30"}})
    specs = events.detect_missed_cycles(settings, "2026-11-16")
    assert len(specs) == 1
    s = specs[0]
    assert (s["kind"], s["severity"], s["title"]) == ("missed_cycle", "CRITICAL", "Slot 15:30 produced no cycle")
    assert s["dedup_key"] == "missed_cycle:2026-11-16"
    rows = [json.loads(l) for l in (tmp / "cycle_completeness" / "2026-11.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["kind"], r["slot_et"], r["detected_by"]) for r in rows] == [("MISSED_CYCLE", "15:30", "eod_sweep")]
    # an EOD rerun (the late 22:00Z backup) writes nothing twice
    events.detect_missed_cycles(settings, "2026-11-16")
    assert len((tmp / "cycle_completeness" / "2026-11.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_eod_partial_slot_is_recorded_but_does_not_page_alone(eod):
    tmp, settings = eod
    _write_day(tmp / "trades", {"gemini": {"11:00"}})
    assert events.detect_missed_cycles(settings, "2026-11-16") == []
    rows = (tmp / "cycle_completeness" / "2026-11.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(rows[0])["kind"] == "PARTIAL_CYCLE"


def test_eod_names_partials_in_the_critical_body(eod):
    tmp, settings = eod
    _write_day(tmp / "trades", {"*": {"10:00", "15:30"}, "grok": {"12:00"}})
    spec = events.detect_missed_cycles(settings, "2026-11-16")[0]
    assert spec["title"] == "2 slots produced no cycle: 10:00, 15:30"
    assert "12:00 (5/6 books)" in spec["body"]


def test_eod_non_session_day_is_silent(eod, monkeypatch):
    tmp, settings = eod
    monkeypatch.setattr(events, "_session_close_et", lambda d: None)
    assert events.detect_missed_cycles(settings, "2026-11-26") == []


def test_production_record_lives_beside_the_trade_logs():
    from src.config_loader import DATA_DIR, TRADES_DIR
    assert events.CYCLE_COMPLETENESS_DIR is None
    assert TRADES_DIR.parent / "cycle_completeness" == DATA_DIR / "cycle_completeness"


def test_eod_sweep_runs_the_detector():
    import inspect
    assert '("missed_cycles", lambda: detect_missed_cycles(settings, date_str))' in \
        inspect.getsource(events.run_eod_alert_sweep)


# ----------------------------------------------- regression on the real logs
@pytest.mark.parametrize("day,missed", [
    ("2026-11-03", ["14:00"]),
    ("2026-11-05", ["10:00"]),
    ("2026-11-11", ["15:30"]),
    ("2026-11-16", ["15:30"]),
    ("2026-11-13", []),
    ("2026-11-12", []),
])
def test_real_decision_logs_show_exactly_the_known_misses(day, missed):
    from src.config_loader import TRADES_DIR
    ev = cw.day_events(TRADES_DIR, BOOKS, date.fromisoformat(day), time(16, 0), "test")
    assert [e["slot_et"] for e in ev if e["kind"] == cw.MISSED_CYCLE] == missed
    assert [e for e in ev if e["kind"] != cw.MISSED_CYCLE] == []


# ------------------------- single classification of a withheld tick (2026-11-17)
# A Gate A withhold writes a TICK_WITHHELD_* admission record and no decision
# record, so the slot looks missed. The EOD sweep records it ONCE, under its
# cause, never as MISSED_CYCLE; the watcher keeps its page but says why.
from src.logging import tick_admission  # noqa: E402

SLOT_1100 = datetime(2026, 11, 16, 16, 0, tzinfo=UTC)    # 11:00 EST


def _withhold(admission_dir: Path, at: datetime, event=tick_admission.TICK_WITHHELD_GUARD_UNREADABLE):
    """An admission row written by the REAL writer, so the reader is tested
    against the format the pipeline actually produces."""
    row = tick_admission.build_withhold_event(
        event, guard="tick_spacing", now_utc=at, books=BOOKS,
        files=["data/trades/grok_2026-11.jsonl"], reason="read_error")
    assert tick_admission.record_withhold(row, path=admission_dir / f"{at:%Y-%m}.jsonl")
    return row


def test_eod_withheld_slot_is_recorded_once_under_its_cause_and_never_as_a_miss(eod):
    tmp, settings = eod
    _write_day(tmp / "trades", {"*": {"11:00"}})
    _withhold(tmp / "admission", SLOT_1100 + timedelta(seconds=40))
    assert events.detect_missed_cycles(settings, "2026-11-16") == []     # no generic page
    path = tmp / "cycle_completeness" / "2026-11.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert [(r["kind"], r["slot_et"]) for r in rows] == [("TICK_WITHHELD_GUARD_UNREADABLE", "11:00")]
    assert rows[0]["admission_record"]["guard"] == "tick_spacing"
    assert not any(r["kind"] == cw.MISSED_CYCLE for r in rows)
    events.detect_missed_cycles(settings, "2026-11-16")                 # EOD rerun
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_eod_withheld_and_genuine_miss_page_only_the_miss_and_name_the_withhold(eod):
    tmp, settings = eod
    _write_day(tmp / "trades", {"*": {"11:00", "15:30"}})
    _withhold(tmp / "admission", SLOT_1100 + timedelta(seconds=40))
    spec = events.detect_missed_cycles(settings, "2026-11-16")
    assert len(spec) == 1 and spec[0]["title"] == "Slot 15:30 produced no cycle"
    assert "Withheld by the admission guard" in spec[0]["body"]
    assert "11:00 (TICK_WITHHELD_GUARD_UNREADABLE)" in spec[0]["body"]
    rows = [json.loads(l) for l in (tmp / "cycle_completeness" / "2026-11.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert sorted((r["kind"], r["slot_et"]) for r in rows) == [
        ("MISSED_CYCLE", "15:30"), ("TICK_WITHHELD_GUARD_UNREADABLE", "11:00")]


def test_eod_withhold_in_another_slot_does_not_excuse_a_miss(eod):
    tmp, settings = eod
    _write_day(tmp / "trades", {"*": {"11:00"}})
    _withhold(tmp / "admission", SLOT_1100 - timedelta(seconds=1))      # 10:30 slot
    spec = events.detect_missed_cycles(settings, "2026-11-16")
    assert spec and spec[0]["title"] == "Slot 11:00 produced no cycle"


def test_a_withhold_in_a_slot_that_then_ran_records_nothing(eod):
    tmp, settings = eod
    _write_day(tmp / "trades")                                           # every slot ran
    _withhold(tmp / "admission", SLOT_1100 + timedelta(seconds=40))
    assert events.detect_missed_cycles(settings, "2026-11-16") == []
    assert not (tmp / "cycle_completeness").exists()


def test_withhold_reader_skips_corrupt_and_foreign_rows():
    lines = ["{not json", json.dumps({"event": "BOOK_WITHHELD_UNACCOUNTED_ORDERS",
                                      "timestamp": "2026-11-16T16:00:40+00:00"}),
             json.dumps({"event": "TICK_WITHHELD_GUARD_UNREADABLE", "timestamp": "garbage"}), ""]
    assert cw.withhold_in_slot(lines, SLOT_1100) is None


def test_the_admission_file_name_matches_the_writer():
    at = SLOT_1100 + timedelta(seconds=40)
    assert cw.admission_log_name(SLOT_1100) == tick_admission.admission_log_path(at, Path("x")).name


def test_production_admission_path_lives_beside_the_trade_logs():
    from src.config_loader import DATA_DIR, TRADES_DIR
    assert events.ADMISSION_DIR is None
    assert TRADES_DIR.parent / "admission" == DATA_DIR / tick_admission.ADMISSION_DIRNAME


def _watch_1530(admission_lines):
    clock = Clock(SLOT_1530 + timedelta(seconds=40))
    mail = Mail()
    mail.clock = clock
    code = watcher.watch(SLOT_1530, BOOKS, lambda books, slot: {b: [] for b in books}, mail,
                         now=clock.now, sleep=clock.sleep,
                         admission=(lambda slot: admission_lines))
    return code, mail


def test_watcher_says_withheld_when_it_can_see_the_admission_record(tmp_path):
    row = _withhold(tmp_path, SLOT_1530 + timedelta(seconds=35))
    lines = (tmp_path / "2026-11.jsonl").read_text(encoding="utf-8").splitlines()
    code, mail = _watch_1530(lines)
    assert code == 0 and len(mail.sent) == 1
    severity, title, body, _ = mail.sent[0]
    assert (severity, title) == ("CRITICAL", "Slot 15:30 produced no cycle")   # the page stays
    assert "withheld by the admission guard" in body
    assert row["event"] in body and "Nothing was re-dispatched" not in body


@pytest.mark.parametrize("lines", [[], None])
def test_watcher_body_is_generic_without_an_admission_record(lines):
    code, mail = _watch_1530(lines)
    body = mail.sent[0][2]
    assert "withheld by the admission guard" not in body and "Nothing was re-dispatched" in body


def test_watcher_reads_admission_from_the_same_fetch_and_never_writes():
    src = (ROOT / "scripts" / "check_slot_cycle.py").read_text(encoding="utf-8")
    assert 'f"FETCH_HEAD:{path}"' in src and "admission=git_admission_reader(ROOT)" in src
