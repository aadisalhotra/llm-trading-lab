"""The pinned venue account: the order path trades only on the registered account.

Why (Batch 4 item (f); hub 2026-11-21, venue clearing). The handover of
2026-12-05/06 moves the venue path to a new paper account, which comes with new
keys. Until now nothing on the order path checked WHICH account the keys
reach: only the demo gate compared account numbers. A stale or swapped secret
would have the books trade, and reconcile, against the wrong account, and the
first sign would be a cash reconciliation failure after the orders had gone in.

The pin. `venue_account.<mode>.account_sha256` in config/settings.json is the
sha256 of the account number. The repo is public, so the number itself is
never committed or logged. scripts/venue_account_pin.py prints the hash of
the account the keys resolve to, and nothing else; the PI commits it under
review, as any gate change.

Enforcement. When a pin is set, Executor() reads the account once
(GET /v2/account) before any book runs, and a mismatch raises
VenueAccountMismatch. The tick then stops before its first order, the
pipeline pages CRITICAL, and the workflow's fail-stop runs as for any failed
tick. With no pin (account_sha256 null) nothing is read and nothing changes:
the check exists from the handover, when the new account is pinned. Never a
fallback to the simulator: a venue book on the wrong account does not trade
anywhere.

`since` (same block) is the instant the pinned account starts serving the
venue path. The probe's residual cash and positions are counted from it, so
probe runs on the old account stop counting against the new one
(administrative.administrative_cash_usd / administrative_positions).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


class VenueAccountMismatch(RuntimeError):
    """The broker keys reach an account other than the pinned one."""


def account_fingerprint(account_number: str) -> str:
    return hashlib.sha256(str(account_number).encode("utf-8")).hexdigest()


def pin(settings: dict[str, Any], mode: str) -> dict[str, Any]:
    """{account_sha256, since} for `mode`; both None when unpinned."""
    block = ((settings.get("venue_account") or {}).get(mode) or {})
    sha = block.get("account_sha256")
    if sha is not None and (len(str(sha)) != 64 or any(c not in "0123456789abcdef" for c in str(sha))):
        raise ValueError(f"venue_account.{mode}.account_sha256 must be 64 lowercase hex characters")
    since = block.get("since")
    if since is not None:
        parsed = datetime.fromisoformat(since)
        if parsed.tzinfo is None:
            raise ValueError(f"venue_account.{mode}.since must carry a UTC offset, got {since!r}")
    return {"account_sha256": sha, "since": since}


def since(settings: dict[str, Any], mode: str) -> datetime | None:
    raw = pin(settings, mode)["since"]
    return datetime.fromisoformat(raw) if raw else None


def check_venue_account(client: Any, settings: dict[str, Any], mode: str) -> dict[str, Any]:
    """Read the account and compare it with the pin. Returns
    {"pinned": bool, "matches": bool | None}. Raises VenueAccountMismatch on
    a mismatch, and on an account object without a number (an unreadable
    identity is not a match). Never logs or returns the number."""
    want = pin(settings, mode)["account_sha256"]
    if want is None:
        return {"pinned": False, "matches": None}
    acct = client.get_account()
    number = (acct or {}).get("account_number")
    if not number:
        raise VenueAccountMismatch("the venue account object names no account number")
    if account_fingerprint(number) != want:
        raise VenueAccountMismatch(
            f"the {mode} keys reach an account whose fingerprint is not the pinned "
            f"venue_account.{mode}.account_sha256; no order is placed")
    return {"pinned": True, "matches": True}
