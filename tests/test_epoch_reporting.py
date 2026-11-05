"""Epoch-aware reporting: R1, the R2 seam, and R6.

Registered at the 2026-11-06 cutover: every equity and cumulative-return series
re-anchors at its epoch's inception; no surface computes a return across an epoch
boundary. The fixture is the real shape: a cohort book whose log runs straight
from a ~$120k paper close into a $2,000 broker-paper book, beside a DeepSeek book
still on its $100,000 paper epoch.
"""
import json

import pytest

from src.alerts import digest, events
from src.analytics import performance
from src.portfolio import portfolio as portfolio_mod
from src.reports import daily_report

SETTINGS = {
    "mode": "broker_paper",
    "experiment_start_date": "2026-04-09",
    "starting_capital": {"_pending_confirmation": [], "paper": 100000, "broker_paper": 2000, "live": 2000},
    "models": {"claude": {"enabled": True, "display_name": "Claude", "cohort": "core"},
               "deepseek": {"enabled": True, "display_name": "DeepSeek", "cohort": "core"}},
    "capital_structure": {"live_cohort_keys": ["claude"], "live_cohort_book_count": 1},
    "alerts": {"ath_min_days": 3},
}


def row(date, value, cum):
    return {"date": date, "total_value": value, "cash": value, "cash_pct": 1.0, "num_positions": 0,
            "cumulative_return": cum, "halted": False, "benchmark_value": 700.0, "api_success": True}


PAPER_TAIL = [row("2026-11-03", 118000.0, 0.18), row("2026-11-04", 119000.0, 0.19),
              row("2026-11-05", 120352.73, 0.2035)]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    perf, state = tmp_path / "performance", tmp_path / "state"
    perf.mkdir(); state.mkdir()
    monkeypatch.setattr(performance, "PERFORMANCE_DIR", perf)
    monkeypatch.setattr(portfolio_mod, "STATE_DIR", state)
    monkeypatch.setattr(performance, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(events, "_perf_history", performance.load_performance_history)

    def write(key, rows, inception=None):
        (perf / f"{key}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        if inception:
            (state / f"{key}.json").write_text(json.dumps(
                {"inception_date": inception[0], "inception_value": inception[1]}), encoding="utf-8")
    return write


FRESH = ("2026-11-06", 2000.0)
PAPER = ("2026-04-09", 100000.0)


def test_each_book_reads_its_own_denominator(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005), row("2026-11-09", 2020.0, 0.01)], FRESH)
    lab("deepseek", PAPER_TAIL, PAPER)
    cohort = performance.compute_metrics("claude", SETTINGS, spy_return=0.10)
    paper = performance.compute_metrics("deepseek", SETTINGS, spy_return=0.10)
    assert cohort["cumulative_return"] == pytest.approx(0.01)
    # The measured failure: on the lab-wide $2,000 anchor this read +5,918%.
    assert paper["cumulative_return"] == pytest.approx(0.2035273)


def test_the_seam_is_not_a_crash(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005), row("2026-11-09", 1990.0, -0.005)], FRESH)
    m = performance.compute_metrics("claude", SETTINGS, spy_return=0.10)
    assert m["days"] == 2
    assert m["daily_pnl_pct"] == pytest.approx(1990.0 / 2010.0 - 1)
    assert m["max_drawdown"] == pytest.approx(1990.0 / 2010.0 - 1)  # not -98%


def test_alpha_never_spans_an_epoch_boundary(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005), row("2026-11-09", 2020.0, 0.01)], FRESH)
    lab("deepseek", PAPER_TAIL, PAPER)
    assert performance.compute_metrics("claude", SETTINGS, spy_return=0.10)["alpha_vs_spy"] is None
    assert performance.compute_metrics("deepseek", SETTINGS, spy_return=0.10)["alpha_vs_spy"] == pytest.approx(0.1035273)


def test_a_first_row_book_is_measured_against_its_own_inception(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005)], FRESH)
    m = performance.compute_metrics("claude", SETTINGS, spy_return=0.10)
    assert m["days"] == 1 and m["cumulative_return"] == pytest.approx(0.005)


def test_unincepted_venue_book_shows_no_paper_rows(lab, monkeypatch):
    # Between the archive and Friday's first tick: no state file, paper rows on disk.
    monkeypatch.setattr("src.data.market_data.inception_session_date", lambda reference=None: "2026-11-06")
    lab("claude", PAPER_TAIL)
    m = performance.compute_metrics("claude", SETTINGS, spy_return=0.10)
    assert m["days"] == 0 and m["cumulative_return"] == 0.0


def test_reading_an_epoch_never_creates_a_book(lab, tmp_path):
    lab("claude", PAPER_TAIL)
    assert portfolio_mod.read_book_inception("claude") is None
    assert not (tmp_path / "state" / "claude.json").exists()


def test_paper_book_with_no_state_keeps_the_lab_anchor(tmp_path, monkeypatch):
    # Pre-launch shape. Mode paper: lab start and $100k, exactly as before.
    settings = {**SETTINGS, "mode": "paper"}
    assert performance.book_epoch("claude", settings) == ("2026-04-09", 100000.0)


# ---- R6 ---------------------------------------------------------------------

def test_day_one_loss_does_not_fire_negative_crossing(lab):
    # The finding's case: first $2,000 close down, previous row the paper book at +20%.
    lab("claude", PAPER_TAIL + [row("2026-11-06", 1995.0, -0.0025)], FRESH)
    assert events.detect_negative_crossings(SETTINGS) == []


def test_control_the_raw_series_would_have_fired(lab):
    # The same rows without the epoch stamp: proves the test above is not vacuous.
    lab("claude", PAPER_TAIL + [row("2026-11-06", 1995.0, -0.0025)], PAPER)
    specs = events.detect_negative_crossings(SETTINGS)
    assert [s["kind"] for s in specs] == ["negative_return"]
    assert "+20" in specs[0]["body"]


def test_a_real_crossing_inside_the_epoch_fires(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005), row("2026-11-09", 1995.0, -0.0025)], FRESH)
    specs = events.detect_negative_crossings(SETTINGS)
    assert len(specs) == 1 and "0.50" in specs[0]["body"]


def test_ath_measures_against_the_epochs_own_peak(lab):
    rows = [row("2026-11-06", 2000.0, 0.0), row("2026-11-09", 2010.0, 0.005), row("2026-11-10", 2020.0, 0.01)]
    lab("claude", PAPER_TAIL + rows, FRESH)
    assert [s["kind"] for s in events.detect_new_ath(SETTINGS)] == ["ath"]


# ---- the digest and report money lines --------------------------------------

def test_digest_first_day_is_not_a_six_figure_loss(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005)], FRESH)
    assert digest._daily_pnl_dollars("claude") == pytest.approx(10.0)


def test_report_first_day_is_measured_from_inception(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005)], FRESH)
    delta, pct = daily_report._daily_pnl("claude")
    assert delta == pytest.approx(10.0) and pct == pytest.approx(0.005)


def test_report_second_day_uses_the_prior_close(lab):
    lab("claude", PAPER_TAIL + [row("2026-11-06", 2010.0, 0.005), row("2026-11-09", 2020.1, 0.01005)], FRESH)
    delta, _ = daily_report._daily_pnl("claude")
    assert delta == pytest.approx(10.1)
