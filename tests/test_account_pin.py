"""The pinned venue account and probe cash by account (Batch 4 item (f))."""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.execution import account_pin as ap  # noqa: E402
from src.execution import administrative as adm  # noqa: E402
from src.execution import executor as ex  # noqa: E402

SHIPPED = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
NEW = "PAFAKE00001"


def pinned(number: str = NEW, since: str | None = "2026-12-05T14:00:00+00:00") -> dict:
    s = copy.deepcopy(SHIPPED)
    s["venue_account"]["broker_paper"] = {"account_sha256": ap.account_fingerprint(number),
                                          "since": since}
    return s


class FakeBroker:
    reads = 0

    def __init__(self, mode, **kw):
        self.mode, self.base_url, self.fill_deadline_seconds = mode, "https://paper-api.example", 300
        self.number = FakeBroker.number

    def get_account(self):
        FakeBroker.reads += 1
        return {"account_number": self.number, "cash": "1"}


@pytest.fixture
def broker(monkeypatch):
    FakeBroker.reads, FakeBroker.number = 0, NEW
    monkeypatch.setattr(ex, "BrokerClient", FakeBroker)
    return FakeBroker


def test_the_shipped_settings_pin_nothing_yet():
    assert ap.pin(SHIPPED, "broker_paper") == {"account_sha256": None, "since": None}


def test_unpinned_the_executor_reads_nothing(broker):
    e = ex.Executor(settings=SHIPPED)
    assert e.broker_enabled and broker.reads == 0


def test_pinned_the_executor_accepts_the_pinned_account(broker):
    ex.Executor(settings=pinned())
    assert broker.reads == 1


def test_pinned_the_executor_refuses_another_account_before_any_order(broker):
    broker.number = "PAOLD000009"
    with pytest.raises(ap.VenueAccountMismatch) as e:
        ex.Executor(settings=pinned())
    assert "PAOLD000009" not in str(e.value) and NEW not in str(e.value)


def test_an_account_without_a_number_is_not_a_match(broker):
    broker.number = None
    with pytest.raises(ap.VenueAccountMismatch, match="names no account number"):
        ex.Executor(settings=pinned())


@pytest.mark.parametrize("bad", [{"account_sha256": "ABC", "since": None},
                                 {"account_sha256": None, "since": "2026-12-05T14:00:00"}])
def test_a_malformed_pin_raises(bad):
    s = copy.deepcopy(SHIPPED)
    s["venue_account"]["broker_paper"] = bad
    with pytest.raises(ValueError):
        ap.pin(s, "broker_paper")


def test_the_paper_mode_never_reads_an_account(broker):
    s = pinned()
    s["mode"] = "paper"
    ex.Executor(settings=s)
    assert broker.reads == 0


# ------------------------------------------------------- probe cash by account

def _probe(tmp_path, rows):
    d = tmp_path / "barrier_probe"
    d.mkdir()
    (d / "2026-12.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d


def _run(started, cash, delta=None):
    return {"phase": "FINISHED", "mode": "broker_paper", "started_utc": started,
            "residual_cash_usd": cash, "position_delta": delta or {}, "cycle_id": started}


def test_probe_cash_and_positions_count_from_the_pinned_account_start(tmp_path):
    d = _probe(tmp_path, [_run("2026-11-27T15:37:31+00:00", -3.01, {"F": 1.0}),
                          _run("2026-12-07T15:37:31+00:00", -0.10),
                          _run("2026-12-08T15:37:31+00:00", -0.07, {"F": 2.0})])
    since = datetime(2026, 12, 5, 14, tzinfo=timezone.utc)
    assert adm.administrative_cash_usd("broker_paper", d) == pytest.approx(-3.18)
    assert adm.administrative_cash_usd("broker_paper", d, since=since) == pytest.approx(-0.17)
    assert adm.administrative_positions("broker_paper", d) == {"F": 3.0}
    assert adm.administrative_positions("broker_paper", d, since=since) == {"F": 2.0}


def test_a_probe_record_without_a_start_cannot_be_scoped(tmp_path):
    rec = _run("2026-12-07T15:37:31+00:00", -0.1)
    del rec["started_utc"]
    d = _probe(tmp_path, [rec])
    with pytest.raises(ValueError, match="started_utc"):
        adm.administrative_cash_usd("broker_paper", d, since=datetime(2026, 12, 5, tzinfo=timezone.utc))


def test_both_reconciliation_callers_scope_probe_terms_by_the_pin():
    for rel in ("src/execution/reconciliation.py", "src/logging/venue_check.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "administrative_cash_usd(mode, since=start)" in src, rel
        assert "administrative_positions(mode, since=start)" in src, rel


def test_the_pipeline_pages_and_stops_on_a_mismatch():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    i = src.index("executor = Executor()")
    block = src[i - 40:i + 600]
    assert "except VenueAccountMismatch" in block and '"CRITICAL"' in block and "raise" in block
    # The guard sits before any book runs.
    assert i < src.index("ThreadPoolExecutor(max_workers")


def test_the_pin_helper_prints_only_the_fingerprint(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import venue_account_pin as vp

    class C:
        def __init__(self, mode):
            pass

        def get_account(self):
            return {"account_number": NEW}
    monkeypatch.setattr("src.execution.broker.BrokerClient", C)
    monkeypatch.setattr(vp, "load_env", lambda: None)
    assert vp.main() == 0
    out = capsys.readouterr().out.strip()
    assert out == ap.account_fingerprint(NEW) and NEW not in out
