"""R4 and the late-EOD guards: a book's epoch begins at its own first session.

Hub rulings 2026-11-04 and 2026-11-05. The cutover's epoch begins Friday
2026-11-06, so no late Thursday run may create a book, touch a book, or write a
row that predates one.
"""
import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src import config_loader
from src import pipeline
from src.data.market_data import inception_session_date
from src.logging import decision_log
from src.logging.decision_log import eod_row_exists, log_daily_snapshot
from src.portfolio import portfolio as portfolio_mod

ET = ZoneInfo("America/New_York")


def et(*args):
    return datetime(*args, tzinfo=ET)


@pytest.mark.parametrize("when, expected, why", [
    (et(2026, 11, 5, 17, 45), "2026-11-06", "after Thursday's close: Friday"),
    (et(2026, 11, 5, 16, 0), "2026-11-06", "at the bell: the session is over"),
    (et(2026, 11, 5, 15, 59), "2026-11-05", "before the bell: still Thursday"),
    (et(2026, 11, 5, 20, 52), "2026-11-06", "the latest observed backup slot"),
    (et(2026, 11, 6, 8, 0), "2026-11-06", "pre-open on a session day"),
    (et(2026, 11, 6, 9, 35), "2026-11-06", "the first effective session"),
    (et(2026, 11, 6, 19, 30), "2026-11-09", "UTC already reads Saturday; weekend skipped"),
    (et(2026, 11, 7, 12, 0), "2026-11-09", "Saturday"),
    (et(2026, 11, 25, 17, 0), "2026-11-27", "Thanksgiving skipped"),
    (et(2026, 11, 27, 13, 30), "2026-11-30", "after the 13:00 early close, not the 16:00 constant"),
])
def test_inception_session_comes_from_the_calendar(when, expected, why):
    assert inception_session_date(when) == expected, why


def test_inception_refuses_to_guess_without_a_calendar():
    with patch.dict("sys.modules", {"pandas_market_calendars": None}):
        with pytest.raises(ImportError):
            inception_session_date(et(2026, 11, 5, 17, 45))


class _Clock(datetime):
    """datetime.now frozen at a Thursday-after-close instant."""
    @classmethod
    def now(cls, tz=None):
        return et(2026, 11, 5, 17, 45).astimezone(tz) if tz else et(2026, 11, 5, 17, 45)


@pytest.fixture
def venue_settings(tmp_path, monkeypatch):
    settings = json.loads((config_loader.CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    settings["mode"] = "broker_paper"
    monkeypatch.setattr(portfolio_mod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(portfolio_mod, "load_settings", lambda: settings)
    monkeypatch.setattr("src.data.market_data.datetime", _Clock)
    return settings


def test_book_created_after_thursdays_close_records_friday(venue_settings):
    book = portfolio_mod.load_portfolio("claude")
    assert book.inception_date == "2026-11-06"
    assert book.inception_epoch == "broker_paper"
    assert book.cash == book.inception_value == 2000.0
    assert book.holdings == {}


def test_experiment_start_stays_a_lab_constant(venue_settings):
    portfolio_mod.load_portfolio("claude")
    assert venue_settings["experiment_start_date"] == "2026-04-09"


def test_month_tag_stays_lab_level_for_a_reincepted_book():
    # The trade logs stamp the experiment month. A cutover book is in month 8 of
    # the experiment, not month 1 of its own life.
    assert decision_log._month_tag(et(2026, 11, 6, 9, 35), "2026-04-09") == "M8"
    source = (config_loader.PROJECT_ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    assert "inception_date=portfolio.inception_date" not in source


# ---- the structural guard ---------------------------------------------------

def _snapshot(inception="2026-11-06", total=2000.0):
    return {"total_value": total, "cash": total, "cash_pct": 1.0, "holdings": [],
            "cumulative_return": 0.0, "halted": False, "inception_date": inception}


@pytest.fixture
def perf_dir(tmp_path, monkeypatch):
    d = tmp_path / "performance"
    monkeypatch.setattr(decision_log, "PERFORMANCE_DIR", d)
    return d


def test_no_row_may_predate_its_books_inception(perf_dir):
    with pytest.raises(ValueError, match="predates the book's own inception 2026-11-06"):
        log_daily_snapshot("claude", et(2026, 11, 5, 20, 52), _snapshot(), 700.0)
    assert not (perf_dir / "claude.jsonl").exists()


def test_a_row_on_the_inception_day_is_valid(perf_dir):
    log_daily_snapshot("claude", et(2026, 11, 6, 17, 5), _snapshot(), 700.0)
    assert json.loads((perf_dir / "claude.jsonl").read_text(encoding="utf-8"))["date"] == "2026-11-06"


def test_eod_row_exists(perf_dir):
    assert eod_row_exists("claude", "2026-11-06") is False  # no log at all
    log_daily_snapshot("claude", et(2026, 11, 6, 17, 5), _snapshot(), 700.0)
    assert eod_row_exists("claude", "2026-11-06") is True
    assert eod_row_exists("claude", "2026-11-05") is False


# ---- the late-EOD guard -----------------------------------------------------

SETTINGS = {"models": {"claude": {"enabled": True}, "gpt": {"enabled": True},
                       "old": {"enabled": False}}}


def _with_rows(perf_dir, day, keys):
    perf_dir.mkdir(parents=True, exist_ok=True)
    for k in keys:
        (perf_dir / f"{k}.jsonl").write_text(json.dumps({"date": day}) + "\n", encoding="utf-8")


@pytest.mark.parametrize("when", [
    et(2026, 11, 5, 17, 16), et(2026, 11, 5, 19, 54), et(2026, 11, 5, 20, 52)])
def test_late_backup_with_all_rows_is_skipped(perf_dir, when):
    _with_rows(perf_dir, "2026-11-05", ["claude", "gpt"])
    assert "already has" in pipeline._eod_skip_reason(SETTINGS, when)


def test_missing_row_keeps_the_backup_useful(perf_dir):
    # A backup exists to fill a genuine gap: the primary EOD failed for a book.
    _with_rows(perf_dir, "2026-11-05", ["claude"])
    assert pipeline._eod_skip_reason(SETTINGS, et(2026, 11, 5, 17, 16)) is None


def test_no_rows_runs_at_the_normal_slot(perf_dir):
    assert pipeline._eod_skip_reason(SETTINGS, et(2026, 11, 6, 17, 5)) is None


def test_cron_delayed_past_midnight_does_not_book_the_next_session(perf_dir):
    # Thursday's 22:00Z cron, hours late, now carries Friday's date pre-open.
    reason = pipeline._eod_skip_reason(SETTINGS, et(2026, 11, 6, 0, 30))
    assert "has not closed" in reason


def test_early_close_is_read_from_the_calendar(perf_dir):
    assert pipeline._eod_skip_reason(SETTINGS, et(2026, 11, 27, 14, 5)) is None


def _run_guarded(monkeypatch, when):
    """Drive run_pipeline's EOD path up to its guard, with every load trapped."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    def trap(*a, **k):
        raise AssertionError("the skip path reached a book load or a data fetch")

    monkeypatch.setattr(pipeline, "datetime", Frozen)
    monkeypatch.setattr(pipeline, "load_env", lambda: None)
    monkeypatch.setattr(pipeline, "ensure_dirs", lambda: None)
    monkeypatch.setattr(pipeline, "alert_preflight", lambda **k: None)
    monkeypatch.setattr(pipeline, "load_settings", lambda: {
        **SETTINGS, "mode": "broker_paper", "phase": "x", "benchmark_ticker": "SPY"})
    for name in ("load_portfolio", "save_portfolio", "fetch_universe_data",
                 "build_dashboard_payload", "generate_daily_report", "Executor"):
        monkeypatch.setattr(pipeline, name, trap)
    return pipeline.run_pipeline(mode="end-of-day")


def test_late_run_after_the_archive_touches_no_book(perf_dir, monkeypatch):
    # The hub's scenario: state files gone, rows present, cron fires at 20:52 ET.
    _with_rows(perf_dir, "2026-11-05", ["claude", "gpt"])
    assert _run_guarded(monkeypatch, et(2026, 11, 5, 20, 52)) == 0


def test_control_a_day_without_rows_does_reach_the_load(perf_dir, monkeypatch):
    # Without this the trap above could be passing because it is never armed.
    with pytest.raises(AssertionError, match="reached a book load or a data fetch"):
        _run_guarded(monkeypatch, et(2026, 11, 6, 17, 5))
