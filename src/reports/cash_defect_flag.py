"""The day-one cash defect flag (executor sizing-floor ruling, section 4.4).

docs/registrations/executor_sizing_floor_ruling.md: "any rendering of day-one cash
levels (Opus 80.7%, Sonnet 60%) carries the defect flag -- those figures are
defect-dominated and must never be read as pure model behavior."

On 2026-11-06 the one-share floor deleted 140 intended BUYs across the five
broker-paper books, so their cash that day is mostly orders that never reached
the venue. This module is the one place that says which (book, date) pairs carry
the flag and what it reads, so every surface renders the same words and a test
can pin them.

Scope is exact, not approximate:
  * the five cohort books only. DeepSeek ran on the paper simulator that day and
    hit the floor zero times, so its 2026-11-06 cash is not defect-dominated and
    flagging it would be false;
  * 2026-11-06 only. Monday's books open on states reached under the defect, but
    that carry-forward is disclosed in the ledger as the corrected segment's
    initial condition, not flagged on every later rendering.

Not a rendering surface for this purpose: the model-facing prompt. The models'
view of their cash was true (ruling 4.3), and the prompt is frozen under the v4
hash check.
"""
from __future__ import annotations

from typing import Any, Iterable

FLAG_CODE = "EXECUTOR_DEFECT_2026_11_06"
FLAG_TEXT = ("figures dominated by orders suppressed by an executor defect; see ledger")
LEDGER_REF = ("scripts/phase_a_integrity_ledger.json "
              "operational_events['2026-11'].executor_floor_defect_2026_11_06")
FLAGGED_DATES = frozenset({"2026-11-06"})
FLAGGED_BOOKS = frozenset({"claude", "claude_opus", "gemini", "gpt", "grok"})
MARKER = "‡"


def cash_flag(model_key: str, date: str | None) -> dict[str, str] | None:
    """The flag for one book's cash as of `date` (YYYY-MM-DD), or None."""
    if not date or model_key not in FLAGGED_BOOKS or date[:10] not in FLAGGED_DATES:
        return None
    return {"code": FLAG_CODE, "date": date[:10], "text": FLAG_TEXT, "ledger_ref": LEDGER_REF}


def window_flags(model_keys: Iterable[str], start: str, end: str) -> dict[str, dict[str, Any]]:
    """Flags for aggregates over [start, end]: a mean, min or max of daily cash
    that includes a flagged day is itself flagged, for that book."""
    out: dict[str, dict[str, Any]] = {}
    days = sorted(d for d in FLAGGED_DATES if start <= d <= end)
    for key in model_keys:
        if key in FLAGGED_BOOKS and days:
            out[key] = {"code": FLAG_CODE, "dates": days, "text": FLAG_TEXT,
                        "ledger_ref": LEDGER_REF}
    return out


def footnote(dates: Iterable[str] = ("2026-11-06",)) -> str:
    """The one-line markdown footnote that explains the marker."""
    ds = ", ".join(sorted(set(dates)))
    return (f"{MARKER} Defect flag ({ds} cash): {FLAG_TEXT} "
            f"(`{LEDGER_REF}`).")
