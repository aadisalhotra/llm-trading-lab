"""load_performance_history keeps file order among rows that share a date.

Consumers dedup to the LAST row per date (the EOD write). With the default
(unstable) quicksort that row depended on the file's length: on the real
performance logs, duplicate launch-window rows reorder once a book's file
reaches 143 rows, so the 2026-10-21 EOD tick changed May's published drawdown
series (test_reproduce_may_data_layer_exact went red).
"""
import json

import pandas as pd

from src.analytics import performance

# The launch window's duplicate-date shape, as in data/performance/*.jsonl.
DUPES = (["2026-04-08"] * 4 + ["2026-04-09"] * 3 + ["2026-04-10"] * 2 + ["2026-04-13"] * 2)


def _dates(n_total):
    tail = pd.bdate_range("2026-04-14", periods=n_total - len(DUPES))
    return DUPES + [d.strftime("%Y-%m-%d") for d in tail]


def test_duplicate_dates_keep_file_order_at_every_length(tmp_path, monkeypatch):
    monkeypatch.setattr(performance, "PERFORMANCE_DIR", tmp_path)
    for n in range(120, 260):
        rows = [{"date": d, "total_value": float(i)} for i, d in enumerate(_dates(n))]
        (tmp_path / "m.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        df = performance.load_performance_history("m")
        assert df["total_value"].tolist() == [float(i) for i in range(n)], f"reordered at {n} rows"
