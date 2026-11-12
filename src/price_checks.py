"""Price and number validity predicates. STANDARD LIBRARY ONLY.

These live outside the src.data package on purpose. Importing anything under
src.data runs src/data/__init__.py, which imports pandas and yfinance; the
alerting path (src/alerts) must stay importable in the install-nothing CI jobs
-- the alerting preflight, the failure notifier and the dashboard staleness
monitor -- so an email fault never depends on a pip install to report itself.
2026-11-12: the price-validity guard first imported these from
src/data/price_validity.py into src/alerts/events.py and broke all three
(ModuleNotFoundError: pandas); tests/test_stdlib_only_entry_points.py pins it.

Do not import anything here beyond the standard library.
"""
from __future__ import annotations

import math
import numbers
from typing import Any


def is_valid_price(x: Any) -> bool:
    """A usable price: a real number (any numbers.Real, not only float), finite, above zero."""
    return (isinstance(x, numbers.Real) and not isinstance(x, bool)
            and math.isfinite(x) and x > 0)


def is_finite_number(x: Any) -> bool:
    """A real, finite number of any sign. For values that may be zero or negative."""
    return isinstance(x, numbers.Real) and not isinstance(x, bool) and math.isfinite(x)
