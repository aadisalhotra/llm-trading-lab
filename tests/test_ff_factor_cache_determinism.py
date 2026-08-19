"""The Fama-French factor cache is a committed fixture, not a scratch file.

Before 2026-08-19 ``load_ff_factors()`` fell through to a live download from the
Ken French Data Library whenever ``data/factors/ff_factors_daily.json`` was
absent, and ``data/factors/`` was gitignored as a "regenerable research cache".
Regenerable it is; deterministic it is not — the upstream file gains a month of
rows every publication cycle. The observed consequence on 2026-08-18: a clean
worktree computed RQ4 with ``factor_data_last_date = 2026-06-30`` and
``n_aligned_factor_days = 57`` where the committed May record says ``2026-03-31``
and ``0``. Same code, same trade logs, different published record.

These tests pin the two halves of the fix: the fixture is tracked, and every
network path is explicit opt-in with a loud failure as the default.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analytics import research_metrics as rm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "data", "factors", "ff_factors_daily.json")
PRE_BACKFILL = os.path.join(
    ROOT, "reports", "monthly", "2026-05", "data_layer.pre_backfill.json"
)
MAY_LAYER = os.path.join(ROOT, "reports", "monthly", "2026-05", "data_layer.json")


def _tracked(path: str) -> bool:
    """True when git tracks the path (staged or committed), not merely present."""
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", rel],
        cwd=ROOT, capture_output=True, text=True,
    )
    return rel in out.stdout.split()


@pytest.fixture(autouse=True)
def _no_ambient_opt_in(monkeypatch):
    """Every test starts from "live fetch not authorized", whatever the shell says."""
    monkeypatch.delenv(rm._FF_LIVE_FETCH_ENV, raising=False)


# ---------------------------------------------------------------- fixtures are real


def test_ff_cache_fixture_is_present_and_not_ignored():
    assert os.path.exists(FIXTURE), "committed FF factor cache is missing"
    assert _tracked(FIXTURE), (
        "data/factors/ff_factors_daily.json is invisible to git — the negation in "
        ".gitignore was lost, and a fresh clone will silently refetch"
    )


def test_pre_backfill_snapshot_is_present_and_not_ignored():
    assert os.path.exists(PRE_BACKFILL), "May pre-backfill snapshot is missing"
    assert _tracked(PRE_BACKFILL), (
        "reports/monthly/2026-05/data_layer.pre_backfill.json is invisible to git — "
        "it has no regeneration path, so reproduce-May cannot run without it"
    )


def test_fixture_end_date_matches_the_committed_may_record():
    """The determinism claim, stated as an equality rather than a comment."""
    with open(FIXTURE, encoding="utf-8") as f:
        factors = json.load(f)
    with open(MAY_LAYER, encoding="utf-8") as f:
        layer = json.load(f)
    rq4 = layer["methodology_data_integrity_rq"]["rq_update"]["accumulating_inputs"]["RQ4"]
    assert max(factors) == rq4["factor_data_last_date"] == "2026-03-31"


# ------------------------------------------------------- default path is fail-loud


def test_missing_cache_raises_instead_of_fetching(monkeypatch, tmp_path):
    monkeypatch.setattr(rm, "FACTORS_DIR", tmp_path / "factors")
    monkeypatch.setattr(rm, "_download_ff_csv", _forbidden)
    with pytest.raises(rm.FactorCacheMissing) as exc:
        rm.load_ff_factors()
    assert rm._FF_LIVE_FETCH_ENV in str(exc.value)


def test_unparseable_cache_raises_instead_of_refetching(monkeypatch, tmp_path):
    d = tmp_path / "factors"
    d.mkdir()
    (d / "ff_factors_daily.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(rm, "FACTORS_DIR", d)
    monkeypatch.setattr(rm, "_download_ff_csv", _forbidden)
    with pytest.raises(rm.FactorCacheMissing):
        rm.load_ff_factors()


def test_use_cache_false_still_needs_the_opt_in(monkeypatch, tmp_path):
    """Bypassing the cache *is* the network path, so it is gated the same way."""
    monkeypatch.setattr(rm, "FACTORS_DIR", tmp_path / "factors")
    monkeypatch.setattr(rm, "_download_ff_csv", _forbidden)
    with pytest.raises(rm.FactorCacheMissing):
        rm.load_ff_factors(use_cache=False)


def test_compute_rq4_propagates_rather_than_publishing_an_open_block(monkeypatch, tmp_path):
    """The failure must reach the caller, not be rendered as a published status."""
    monkeypatch.setattr(rm, "FACTORS_DIR", tmp_path / "factors")
    monkeypatch.setattr(rm, "_download_ff_csv", _forbidden)
    with pytest.raises(rm.FactorCacheMissing):
        rm.compute_rq4({}, ["gpt"])


# ------------------------------------------------------------ opt-in works, loudly


def test_env_var_authorizes_the_live_path(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(rm, "FACTORS_DIR", tmp_path / "factors")
    monkeypatch.setattr(rm, "_download_ff_csv", lambda url: calls.append(url) or None)
    monkeypatch.setenv(rm._FF_LIVE_FETCH_ENV, "1")
    assert rm.load_ff_factors() is None       # upstream unreachable, genuinely None
    assert calls, "opt-in did not reach the download path"


def test_explicit_argument_authorizes_the_live_path(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(rm, "FACTORS_DIR", tmp_path / "factors")
    monkeypatch.setattr(rm, "_download_ff_csv", lambda url: calls.append(url) or None)
    assert rm.load_ff_factors(allow_live_fetch=True) is None
    assert calls, "explicit allow_live_fetch did not reach the download path"


def test_falsey_env_values_do_not_authorize(monkeypatch, tmp_path):
    monkeypatch.setattr(rm, "FACTORS_DIR", tmp_path / "factors")
    monkeypatch.setattr(rm, "_download_ff_csv", _forbidden)
    for value in ("", "0", "false", "no", "off", "maybe"):
        monkeypatch.setenv(rm._FF_LIVE_FETCH_ENV, value)
        with pytest.raises(rm.FactorCacheMissing):
            rm.load_ff_factors()


def test_present_cache_is_read_without_touching_the_network(monkeypatch, tmp_path):
    d = tmp_path / "factors"
    d.mkdir()
    payload = {"2026-03-31": {"Mkt-RF": 0.001, "SMB": 0.0, "HML": 0.0,
                              "RMW": 0.0, "CMA": 0.0, "RF": 0.0001, "MOM": 0.0}}
    (d / "ff_factors_daily.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(rm, "FACTORS_DIR", d)
    monkeypatch.setattr(rm, "_download_ff_csv", _forbidden)
    assert rm.load_ff_factors() == payload


def _forbidden(url):
    raise AssertionError(f"network path taken without opt-in: {url}")
