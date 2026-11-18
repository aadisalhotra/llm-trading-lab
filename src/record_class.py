"""Decision-log row classes — the one discriminator every reader uses.

Governing text: docs/registrations/eod_stop_record_and_evaluation_ruling.md §1
and §3. A row in data/trades/{book}_{month}.jsonl is either a model-call row or
an ADMINISTRATIVE row. One rule covers every reader:

  * model-call populations EXCLUDE administrative rows before any statistic is
    computed: completeness and G-COMPLETE, latency, cost, RQ1's decision sets
    and tick alignment, RQ3's confidence legs, RQ6, the spacing guard's "last
    decided" reference, the slot watcher and EOD backstop, the identifier-echo
    counts;
  * execution-replay populations INCLUDE them: book conservation, RQ2's
    average-cost replay, RQ3's closed-trade reconstruction, RQ5's pre-trade
    weights; and so does the model's memory feed, because an EOD stop must
    appear to the model exactly as an intraday stop does (§1);
  * no reader may crash on an administrative row's null model-call fields.

Discriminator: `record_class == "ADMINISTRATIVE"` (new rows) OR
`provider == "administrative"` (both eras: the two 2026-10-23 BOUNDARY_ADMIN
rows carry only the provider marker, and report `api_success: true`,
`api_attempt: 1`, `api_latency_seconds: 0.0` for a call that never happened —
§3 rules them out of model-call populations by this discriminator).

Stdlib only and import-free, so any module can use it.
"""
from __future__ import annotations

from typing import Any

RECORD_CLASS_ADMINISTRATIVE = "ADMINISTRATIVE"
ADMIN_PROVIDER = "administrative"

# Closed set (§1), extended only by registration.
ADMIN_CLASS_EOD_POSITION_STOP = "EOD_POSITION_STOP"
ADMIN_CLASS_BOUNDARY_ADMIN = "BOUNDARY_ADMIN"
REGISTERED_ADMIN_CLASSES = frozenset({ADMIN_CLASS_EOD_POSITION_STOP, ADMIN_CLASS_BOUNDARY_ADMIN})

# §4 cases A/B: an EOD stop the venue cannot execute after the close (rejected,
# or accepted for the next open and cancelled at our fill deadline). Its own
# tag, deliberately NOT stop_unprotected (§4 consequence 1).
EOD_STOP_NOT_EXECUTABLE = "EOD_STOP_NOT_EXECUTABLE"


def is_administrative_row(row: Any) -> bool:
    """True for an administrative decision-log row of either era."""
    if not isinstance(row, dict):
        return False
    return (row.get("record_class") == RECORD_CLASS_ADMINISTRATIVE
            or row.get("provider") == ADMIN_PROVIDER)


def is_model_call_row(row: Any) -> bool:
    """True for a row that records a model call (successful or failed)."""
    return isinstance(row, dict) and not is_administrative_row(row)


def last_model_call_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last row of `rows` that records a model call, or None."""
    for r in reversed(rows):
        if is_model_call_row(r):
            return r
    return None
