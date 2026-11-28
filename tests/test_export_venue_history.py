"""The venue-history export (Batch 4 item (e)): read-only, complete, scrubbed."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_venue_history as ev  # noqa: E402

T0 = datetime(2026, 11, 6, 14, 35, tzinfo=timezone.utc)
ACCT = "PA3XYZ9QWERT"


def _order(i: int, t: datetime, filled="1", status="filled", legs=None) -> dict:
    o = {"id": f"o{i:04d}", "client_order_id": f"gpt-c-{i:02d}-a0", "symbol": "AAPL",
         "submitted_at": t.isoformat().replace("+00:00", "Z"), "filled_qty": filled, "status": status}
    if legs is not None:
        o["legs"] = legs
    return o


class FakeVenue:
    """Serves /v2/orders like Alpaca: submitted_at strictly after `after`,
    ascending, `limit` per page; activities paginated by page_token."""

    def __init__(self, orders, activities):
        self.orders, self.activities, self.calls = orders, activities, []

    def request(self, method, path, params=None):
        self.calls.append((method, path, dict(params or {})))
        if path == "/v2/orders":
            after = datetime.fromisoformat(params["after"])
            rows = sorted((o for o in self.orders if ev._ts(o["submitted_at"]) > after),
                          key=lambda o: (o["submitted_at"], o["id"]))
            return rows[: params["limit"]]
        if path == "/v2/account/activities":
            rows = self.activities
            if params.get("page_token"):
                idx = [a["id"] for a in rows].index(params["page_token"])
                rows = rows[idx + 1:]
            return rows[: params["page_size"]]
        raise AssertionError(path)


def test_orders_sharing_a_timestamp_across_a_page_boundary_are_all_kept(monkeypatch):
    monkeypatch.setattr(ev, "ORDER_PAGE", 5)
    # Orders 4..7 share one timestamp, straddling the first page's end.
    times = [T0 + timedelta(seconds=i) for i in range(4)] + [T0 + timedelta(seconds=10)] * 4 \
        + [T0 + timedelta(seconds=20 + i) for i in range(5)]
    orders = [_order(i, t) for i, t in enumerate(times)]
    v = FakeVenue(orders, [])
    got = ev.fetch_orders(v.request, T0 - timedelta(days=1))
    assert sorted(o["id"] for o in got) == sorted(o["id"] for o in orders)
    assert all(m == "GET" for m, _, _ in v.calls)


def test_a_full_page_that_adds_nothing_stops_the_export(monkeypatch):
    monkeypatch.setattr(ev, "ORDER_PAGE", 3)
    orders = [_order(i, T0) for i in range(3)]
    v = FakeVenue(orders, [])
    v.request = lambda m, p, params=None: orders     # never advances
    with pytest.raises(ev.ExportError, match="cannot paginate"):
        ev.fetch_orders(v.request, T0 - timedelta(days=1))


def test_activities_are_paginated_by_the_last_id(monkeypatch):
    monkeypatch.setattr(ev, "ACTIVITY_PAGE", 4)
    acts = [{"id": f"a{i:03d}", "activity_type": "FILL"} for i in range(11)]
    got = ev.fetch_activities(FakeVenue([], acts).request)
    assert [a["id"] for a in got] == [a["id"] for a in acts]


def test_nested_legs_become_rows_of_their_own():
    leg = _order(2, T0, filled="0", status="canceled")
    rows = ev.flatten_orders([_order(1, T0, legs=[leg])])
    assert [r["id"] for r in rows] == ["o0001", "o0002"]
    assert rows[0]["leg_ids"] == ["o0002"] and rows[1]["parent_order_id"] == "o0001"


def test_cross_checks_name_every_inconsistency():
    orders = [_order(1, T0, filled="2"), _order(2, T0, filled="1"), _order(3, T0, filled="0", status="canceled")]
    acts = [{"id": "f1", "activity_type": "FILL", "order_id": "o0001", "qty": "1.5"},
            {"id": "f2", "activity_type": "FILL", "order_id": "zzz", "qty": "1"}]
    cc = ev.cross_check(orders, acts)
    assert cc["fills_naming_an_unknown_order"] == ["f2"]
    assert cc["filled_orders_without_a_fill_activity"] == ["o0002"]
    assert cc["fill_qty_mismatches"] == [{"order_id": "o0001", "order_filled_qty": "2",
                                          "fill_activity_qty": "1.5"}]
    assert cc["consistent"] is False


def _raw():
    return {
        "account": {"id": "acct-uuid-1234", "account_number": ACCT, "cash": "89999.99", "status": "ACTIVE"},
        "configurations": {"no_shorting": True},
        "positions": [],
        "orders": [_order(1, T0)],
        "activities": [{"id": "f1", "activity_type": "FILL", "order_id": "o0001", "qty": "1"},
                       {"id": "e1", "activity_type": "FEE", "description": f"REG fee account {ACCT}",
                        "account_id": "acct-uuid-1234"}],
        "portfolio_history": {"equity": [1, 2]},
    }


def test_render_removes_the_account_identifiers_everywhere():
    files = ev.render(_raw(), label="t", mode="broker_paper", venue="test", captured_at="now")
    blob = b"".join(files.values())
    assert ACCT.encode() not in blob and b"acct-uuid-1234" not in blob
    acts = [json.loads(line) for line in files["activities.jsonl"].decode().splitlines()]
    fee = next(a for a in acts if a["activity_type"] == "FEE")
    assert fee["description"] == "REG fee account [account]" and "account_id" not in fee
    account = json.loads(files["account.json"])
    assert account == {"cash": "89999.99", "status": "ACTIVE"}
    m = json.loads(files["MANIFEST.json"])
    assert m["read_only"] is True and m["cross_checks"]["consistent"] is True
    assert m["counts"] == {"orders": 1, "orders_top_level": 1, "activities": 2, "positions": 0}
    import hashlib
    for name, digest in m["sha256"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest


def test_a_secret_in_any_file_stops_the_write():
    files = {"a.json": b'{"x": "sk-live-abcdef"}'}
    with pytest.raises(ev.ExportError, match="a.json") as e:
        ev.assert_no_secret(files, ["sk-live-abcdef"])
    assert "sk-live" not in str(e.value)
    ev.assert_no_secret(files, ["nothere", ""])


def test_the_export_never_overwrites(tmp_path, monkeypatch):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "orders.jsonl").write_text("{}")
    monkeypatch.setattr(ev, "load_env", lambda: None)
    monkeypatch.setattr(ev, "load_settings", lambda: {"mode": "broker_paper"})

    class _Client:
        def __init__(self, mode):
            pass
    monkeypatch.setattr("src.execution.broker.BrokerClient", _Client)
    assert ev.main(["--label", "x", "--out", str(tmp_path / "x")]) == 2


def test_only_get_requests_are_made():
    src = (ROOT / "scripts" / "export_venue_history.py").read_text(encoding="utf-8")
    assert 'assert method == "GET", "read-only export"' in src
    for verb in ('"POST"', '"DELETE"', '"PATCH"', '"PUT"'):
        assert verb not in src
