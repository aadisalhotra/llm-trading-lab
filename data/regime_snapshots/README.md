# SPY regime snapshots (frozen, committed)

Regime stratification for the RQ analysis is assigned post-hoc from SPY daily
closes. The live path (`regime_classifier.fetch_spy_daily`) re-queries yfinance
on every run and its adjustments drift between fetches — the same class of
instability that de-anchored the perf-log SPY benchmark. For a reproducible
pre-registered analysis, regime classification reads a **frozen, committed
snapshot** instead of re-querying.

- `spy_daily.csv` — the frozen `date,close` series.
- `spy_daily.meta.json` — provenance: `captured_at_utc`, source, ticker,
  `auto_adjust`, date range, row count, and a content `sha256`.

## Reading (reproducible, default)

`classify_regimes(use_snapshot=True)` (the default) reads `spy_daily.csv`. If no
snapshot exists it falls back to a live yfinance fetch **and warns** that the
result is not the frozen series.

## Refreshing (explicit, provenance-stamped)

```python
from src.analytics.regime_classifier import snapshot_spy_daily
snapshot_spy_daily()  # fetches live, rewrites spy_daily.csv + meta with provenance
```

A refresh is a deliberate, stamped act — never a silent per-run re-query. Commit
the refreshed snapshot so the analysis that reads it is reproducible.

> Note: this snapshot is populated by a live (networked) capture. In an offline
> environment the files are absent and `classify_regimes` degrades to the live
> fetch with a warning until a capture runs.
