"""Alpaca broker client — the live order lifecycle.

Scope. This module owns exactly one thing: getting a single order to a
terminal, broker-confirmed state and reporting what actually happened. It does
not decide what to trade, does not touch a Portfolio, and does not coordinate
across books. Cross-book submission ordering (the wash-trade barrier) is P1 and
deliberately lives above this layer.

Why it exists at all. Before this, `Executor._submit_alpaca_order` submitted an
order and read `filled_avg_price` off the *submission* response — which is
`None` for a market order, verified against the paper endpoint 2026-08-29 — so
the fill price silently fell back to the pre-trade simulated price, and the book
was mutated whether or not the order ever filled. A rejected order still moved
the book. Everything here exists to make the book a function of confirmed fills.

VENUE FACTS, all verified against https://paper-api.alpaca.markets on
2026-08-29 (spike; market closed). Each one is load-bearing somewhere below:

  * `client_order_id` is capped at 128 characters and must be unique
    ACCOUNT-WIDE and PERMANENTLY — a cancelled order's id stays consumed. It
    therefore cannot serve as a retry idempotency key, which is the usual
    design. Retries get a fresh id via an attempt suffix; see `build_order_id`.
  * Fractional orders MUST be DAY orders. `gtc`/`opg` are rejected outright
    ("fractional orders must be DAY orders"), and `ioc` is rejected outside
    market hours. Every order this module places is therefore `day`.
  * Quantities carry at most 9 decimal places and the venue TRUNCATES silently
    past that rather than erroring. We truncate ourselves so our record and the
    venue's agree exactly.
  * An order whose cost basis is below $1 is rejected (HTTP 403, "cost basis
    must be >= minimal amount of order 1"). Fractional sizing can produce these,
    so they are caught before submission.
  * `BRK-B` does not exist; the venue spells it `BRK.B`. 78 of the 79 universe
    names match exactly. See `to_venue_symbol`.
  * An opposite-side order that is OPEN on the same symbol causes a
    wash-trade rejection (HTTP 403) account-wide. Six books share one account,
    so this is reachable whenever two books disagree on a name in one cycle.
    This module DETECTS and classifies it; it does not resolve it. Resolution
    needs cross-book ordering and a registered parity rule, both P1.
  * The account-wide rate limit advertises 200 requests/minute.

Fail-closed endpoint binding. The mode picks the endpoint and the credential
pair, and the two are asserted to agree before any request goes out. A
`broker_paper` run cannot reach the live endpoint and cannot authenticate with
live keys, and vice versa — a misconfigured `ALPACA_BASE_URL` secret can no
longer point a paper run at production, because the base URL is derived in code
and never read from the environment.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger("llmlab.broker")

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
# The three execution modes. `paper` is the in-process simulator that has run
# Phase A since April and reaches no venue at all. The other two both exercise
# the real order lifecycle; they differ only in which Alpaca environment and
# which credential pair they bind to.
MODE_SIMULATOR = "paper"
MODE_BROKER_PAPER = "broker_paper"
MODE_LIVE = "live"
VALID_MODES = (MODE_SIMULATOR, MODE_BROKER_PAPER, MODE_LIVE)
# Modes that place real orders at the venue.
BROKER_MODES = (MODE_BROKER_PAPER, MODE_LIVE)

_ENDPOINTS = {
    MODE_BROKER_PAPER: "https://paper-api.alpaca.markets",
    MODE_LIVE: "https://api.alpaca.markets",
}

# Credential env vars, per mode. The legacy single pair is accepted ONLY for
# broker_paper: the keys committed to CI before the split were paper keys, and
# allowing them to authenticate a live run is exactly the accident the
# fail-closed rule exists to prevent.
_CREDENTIALS = {
    MODE_BROKER_PAPER: (("ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET"),
                        ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")),
    MODE_LIVE: (("ALPACA_LIVE_KEY", "ALPACA_LIVE_SECRET"),),
}

# ---------------------------------------------------------------------------
# Venue limits
# ---------------------------------------------------------------------------
MAX_CLIENT_ORDER_ID = 128
QTY_DECIMALS = 9
MIN_ORDER_NOTIONAL = 1.00
DEFAULT_FILL_DEADLINE_SECONDS = 300      # registered 2026-08-29: 5 minutes
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
RATE_LIMIT_PER_MINUTE = 200

# Terminal order states. `pending_cancel` and `pending_replace` are explicitly
# NOT terminal — an order in one of those can still fill.
TERMINAL_STATES = frozenset({
    "filled", "canceled", "cancelled", "expired", "rejected",
    "done_for_day", "replaced", "stopped", "suspended",
})
# States that mean the venue accepted the order and it may still fill.
OPEN_STATES = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled",
    "pending_cancel", "pending_replace", "calculated", "held",
})

# ---------------------------------------------------------------------------
# Symbol mapping
# ---------------------------------------------------------------------------
# yfinance (our market-data source, and therefore config/universe.json) spells
# share classes with a hyphen; Alpaca spells them with a dot. Exactly one name
# in the v2 universe is affected, but the mapping is expressed as a rule with an
# explicit table so a future universe addition does not silently fail at
# submission. Verified 2026-08-29: BRK-B -> 422 asset not found; BRK.B -> 200.
_SYMBOL_TO_VENUE = {
    "BRK-B": "BRK.B",
    "BRK-A": "BRK.A",
    "BF-B": "BF.B",
    "BF-A": "BF.A",
}
_SYMBOL_FROM_VENUE = {v: k for k, v in _SYMBOL_TO_VENUE.items()}


def to_venue_symbol(ticker: str) -> str:
    """Pipeline ticker -> venue symbol."""
    t = (ticker or "").upper().strip()
    return _SYMBOL_TO_VENUE.get(t, t)


def from_venue_symbol(symbol: str) -> str:
    """Venue symbol -> pipeline ticker. Used when reading broker state back."""
    s = (symbol or "").upper().strip()
    return _SYMBOL_FROM_VENUE.get(s, s)


def truncate_qty(shares: float) -> float:
    """Truncate to the venue's 9-dp quantity precision.

    Truncation, never rounding: rounding up can push the notional above the
    cash that was checked against it, which is the same reasoning behind the
    executor's existing 4-dp truncation.
    """
    factor = 10 ** QTY_DECIMALS
    return int(float(shares) * factor) / factor


def build_order_id(book: str, cycle_id: str, seq: int, attempt: int = 0) -> str:
    """Compose a `client_order_id` that carries book identity.

    Format: ``{book}-{cycle_id}-{seq:02d}-a{attempt}``. The book segment is the
    order's book tag — the join key every downstream per-book metric (G-EXEC,
    fee attribution, the two-level reconciliation) resolves through.

    The attempt suffix is not decoration. Venue `client_order_id` uniqueness is
    permanent and survives cancellation, so a resubmission MUST carry a new id;
    reusing the original returns 422 rather than acting idempotently.

    Truncation, if it were ever needed, would eat into the book segment and
    destroy attribution, so an over-long id raises instead.
    """
    oid = f"{book}-{cycle_id}-{seq:02d}-a{attempt}"
    if len(oid) > MAX_CLIENT_ORDER_ID:
        raise ValueError(
            f"client_order_id {len(oid)} chars exceeds the venue limit of "
            f"{MAX_CLIENT_ORDER_ID}: {oid!r} — shorten the cycle id rather than "
            "truncating, which would corrupt book attribution")
    return oid


def book_from_order_id(client_order_id: str) -> str:
    """Recover the book tag from a client_order_id. Empty if unparseable."""
    if not client_order_id or "-" not in client_order_id:
        return ""
    return client_order_id.split("-", 1)[0]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class BrokerConfigError(RuntimeError):
    """Credentials or endpoint binding are wrong. Always fatal, never retried."""


class BrokerAPIError(RuntimeError):
    """The venue rejected or failed a request."""

    def __init__(self, message: str, *, status: int = 0, code: int = 0):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def is_wash_trade(self) -> bool:
        """The account-wide opposite-side collision (HTTP 403, code 40310000).

        Distinguished because it is an artifact of six books sharing one
        account, not a market constraint — the caller must be able to surface
        it as such rather than filing it with ordinary rejections.
        """
        return self.code == 40310000 and "wash trade" in str(self).lower()

    @property
    def is_below_minimum(self) -> bool:
        """Cost basis under the venue's $1 floor (HTTP 403, code 40310000)."""
        return self.code == 40310000 and "minimal amount" in str(self).lower()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class _TokenBucket:
    """Shared request budget across all six books.

    The limit is account-wide, not per book, so the six pipeline threads draw
    from one bucket. This is the mechanism behind registered partition-control
    disclosure (5) — shared API capacity, symmetric across books.
    """

    def __init__(self, per_minute: int = RATE_LIMIT_PER_MINUTE):
        self.capacity = float(per_minute)
        self.tokens = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity,
                    self.tokens + (now - self.updated) * self.refill_per_second)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                deficit = (1.0 - self.tokens) / self.refill_per_second
            if time.monotonic() + deficit > deadline:
                logger.warning("Rate-limit wait exceeded %.0fs; proceeding", timeout)
                return
            time.sleep(min(deficit, 1.0))


# ---------------------------------------------------------------------------
# Order record
# ---------------------------------------------------------------------------
@dataclass
class BrokerOrder:
    """A venue order and whatever the venue has told us about it."""

    client_order_id: str
    broker_order_id: str
    symbol: str                  # venue spelling
    ticker: str                  # pipeline spelling
    side: str                    # buy / sell
    requested_qty: float
    status: str
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0
    submitted_at: str = ""
    filled_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def filled_notional(self) -> float:
        return self.filled_qty * self.filled_avg_price

    @property
    def book(self) -> str:
        return book_from_order_id(self.client_order_id)

    def to_dict(self) -> dict[str, Any]:
        """The reconciliation record. No account identifiers: the repo is
        public and this is written into committed data."""
        return {
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "book": self.book,
            "ticker": self.ticker,
            "venue_symbol": self.symbol,
            "side": self.side,
            "requested_qty": self.requested_qty,
            "status": self.status,
            "filled_qty": self.filled_qty,
            "filled_avg_price": self.filled_avg_price,
            "filled_notional": self.filled_notional,
            "submitted_at": self.submitted_at,
            "filled_at": self.filled_at,
        }


def _parse_order(payload: dict[str, Any]) -> BrokerOrder:
    sym = str(payload.get("symbol") or "")
    return BrokerOrder(
        client_order_id=str(payload.get("client_order_id") or ""),
        broker_order_id=str(payload.get("id") or ""),
        symbol=sym,
        ticker=from_venue_symbol(sym),
        side=str(payload.get("side") or ""),
        requested_qty=float(payload.get("qty") or 0.0),
        status=str(payload.get("status") or "").lower(),
        filled_qty=float(payload.get("filled_qty") or 0.0),
        # None until something fills — the defect this module exists to fix.
        filled_avg_price=float(payload.get("filled_avg_price") or 0.0),
        submitted_at=str(payload.get("submitted_at") or ""),
        filled_at=str(payload.get("filled_at") or ""),
        raw=payload,
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class BrokerClient:
    """Thin, synchronous Alpaca REST client for the order lifecycle."""

    def __init__(self, mode: str, *,
                 fill_deadline_seconds: int = DEFAULT_FILL_DEADLINE_SECONDS,
                 poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
                 session: Any = None):
        if mode not in BROKER_MODES:
            raise BrokerConfigError(
                f"BrokerClient requires one of {BROKER_MODES}, got {mode!r}. "
                f"Mode {MODE_SIMULATOR!r} reaches no venue and must not construct one.")
        self.mode = mode
        self.base_url = _ENDPOINTS[mode]
        self.fill_deadline_seconds = int(fill_deadline_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._key, self._secret, self._credential_source = self._resolve_credentials(mode)
        self._bucket = _TokenBucket()
        self._session = session or requests.Session()
        self._assert_endpoint_binding()

    # ----- configuration -----

    @staticmethod
    def _resolve_credentials(mode: str) -> tuple[str, str, str]:
        for key_var, secret_var in _CREDENTIALS[mode]:
            key, secret = os.getenv(key_var), os.getenv(secret_var)
            if key and secret:
                if key_var == "ALPACA_API_KEY":
                    logger.warning(
                        "Using legacy %s/%s for %s. The credential split "
                        "(ALPACA_PAPER_KEY / ALPACA_LIVE_KEY) is the ratified "
                        "target; legacy keys are accepted for broker_paper only.",
                        key_var, secret_var, mode)
                return key, secret, key_var
        expected = " or ".join("/".join(p) for p in _CREDENTIALS[mode])
        raise BrokerConfigError(
            f"No credentials for mode {mode!r}. Expected {expected} in the "
            f"environment. Live credentials are never inherited from the paper "
            f"pair — that substitution is what fail-closed binding forbids.")

    def _assert_endpoint_binding(self) -> None:
        """Refuse to run if the mode, endpoint and credential class disagree.

        The base URL is derived from the mode in code and is deliberately NOT
        read from ALPACA_BASE_URL: a stale or wrong secret must not be able to
        point a paper run at production or a live run at paper.
        """
        if self.mode == MODE_LIVE:
            if "paper-api" in self.base_url:
                raise BrokerConfigError(
                    "Live mode resolved to the paper endpoint — refusing to run.")
            if self._credential_source != "ALPACA_LIVE_KEY":
                raise BrokerConfigError(
                    f"Live mode authenticated from {self._credential_source!r}; "
                    "live runs require ALPACA_LIVE_KEY/ALPACA_LIVE_SECRET.")
        if self.mode == MODE_BROKER_PAPER and "paper-api" not in self.base_url:
            raise BrokerConfigError(
                f"broker_paper mode resolved to {self.base_url} — refusing to run.")
        env_url = os.getenv("ALPACA_BASE_URL")
        if env_url and env_url.rstrip("/") != self.base_url:
            logger.warning(
                "ALPACA_BASE_URL is set to %s but mode %s binds to %s. The "
                "environment value is ignored by design.",
                env_url, self.mode, self.base_url)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
            "accept": "application/json",
        }

    # ----- transport -----

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 params: dict | None = None, timeout: int = 20) -> Any:
        self._bucket.take()
        url = self.base_url + path
        try:
            r = self._session.request(method, url, headers=self.headers,
                                      json=body, params=params, timeout=timeout)
        except requests.RequestException as e:
            raise BrokerAPIError(f"{method} {path} failed: {e}") from e
        try:
            payload = r.json()
        except ValueError:
            payload = {"message": r.text}
        if r.status_code >= 400:
            code = 0
            message = str(payload)
            if isinstance(payload, dict):
                code = int(payload.get("code") or 0)
                message = str(payload.get("message") or payload)
            raise BrokerAPIError(message, status=r.status_code, code=code)
        return payload

    # ----- account state (level-2 reconciliation input) -----

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", "/v2/account")

    def get_positions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v2/positions")

    def account_snapshot(self) -> dict[str, Any]:
        """Broker-authoritative state, scrubbed of identifiers.

        This is the aggregate side of the registered two-level conservation
        audit. Account number and internal ids are dropped rather than
        redacted: the repo is public and CI commits `data/`.
        """
        acct = self.get_account()
        positions = self.get_positions()
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "cash": float(acct.get("cash") or 0.0),
            "equity": float(acct.get("equity") or 0.0),
            "position_market_value": float(acct.get("position_market_value") or 0.0),
            "status": acct.get("status"),
            "trading_blocked": bool(acct.get("trading_blocked")),
            "account_blocked": bool(acct.get("account_blocked")),
            "positions": [
                {
                    "ticker": from_venue_symbol(str(p.get("symbol") or "")),
                    "qty": float(p.get("qty") or 0.0),
                    "avg_entry_price": float(p.get("avg_entry_price") or 0.0),
                    "market_value": float(p.get("market_value") or 0.0),
                }
                for p in positions
            ],
        }

    # ----- order lifecycle -----

    def submit_market_order(self, *, ticker: str, shares: float, side: str,
                            client_order_id: str) -> BrokerOrder:
        """Submit a fractional-capable market DAY order.

        `time_in_force` is hard-coded to `day` because the venue rejects
        fractional orders on any other TIF. Callers do not get to choose.
        """
        qty = truncate_qty(shares)
        if qty <= 0:
            raise BrokerAPIError(
                f"Refusing to submit {side} {ticker}: quantity truncates to zero")
        body = {
            "symbol": to_venue_symbol(ticker),
            "qty": f"{qty:.9f}".rstrip("0").rstrip("."),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        payload = self._request("POST", "/v2/orders", body=body)
        order = _parse_order(payload)
        logger.info("[%s] submitted %s %s %g -> %s (%s)",
                    order.book or "?", side, ticker, qty, order.status,
                    client_order_id)
        return order

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        return _parse_order(self._request("GET", f"/v2/orders/{broker_order_id}"))

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrder:
        """Resolve an order by our own tag — the reconciliation join key."""
        return _parse_order(self._request(
            "GET", "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id}))

    def cancel_order(self, broker_order_id: str) -> None:
        try:
            self._request("DELETE", f"/v2/orders/{broker_order_id}")
        except BrokerAPIError as e:
            # A cancel that races a fill is normal, not an error.
            logger.info("Cancel of %s returned %s (likely already terminal): %s",
                        broker_order_id, e.status, e)

    def await_terminal(self, order: BrokerOrder,
                       deadline_seconds: int | None = None) -> tuple[BrokerOrder, bool]:
        """Poll until terminal or the fill deadline, cancelling the remainder.

        Returns `(order, hit_deadline)`. `hit_deadline` is what the caller needs
        to classify an `unfilled_at_deadline` execution-constraint event, which
        is registered as execution-SUCCESSFUL (occasional-unavailability class)
        while remaining a disclosed per-model descriptor.

        A partial fill at the deadline stands; only the unfilled remainder is
        cancelled. After cancelling we re-read the order, because a fill can
        land between the last poll and the cancel.
        """
        limit = self.fill_deadline_seconds if deadline_seconds is None else deadline_seconds
        started = time.monotonic()
        current = order
        while not current.is_terminal:
            if time.monotonic() - started >= limit:
                logger.warning(
                    "[%s] %s %s unfilled after %ds — cancelling remainder "
                    "(filled %g of %g)", current.book or "?", current.side,
                    current.ticker, limit, current.filled_qty, current.requested_qty)
                self.cancel_order(current.broker_order_id)
                time.sleep(self.poll_interval_seconds)
                try:
                    current = self.get_order(current.broker_order_id)
                except BrokerAPIError as e:
                    logger.error("Post-cancel re-read failed for %s: %s",
                                 current.client_order_id, e)
                return current, True
            time.sleep(self.poll_interval_seconds)
            try:
                current = self.get_order(current.broker_order_id)
            except BrokerAPIError as e:
                logger.warning("Status poll failed for %s: %s",
                               current.client_order_id, e)
        return current, False

    def place_and_confirm(self, *, ticker: str, shares: float, side: str,
                          client_order_id: str,
                          deadline_seconds: int | None = None
                          ) -> tuple[BrokerOrder, bool]:
        """Submit, then resolve to a terminal broker-confirmed state.

        This is the only entry point callers should use to move a position. Its
        contract is the one the book depends on: the returned order's
        `filled_qty` and `filled_avg_price` are what the venue actually did, so
        a rejection returns zero fill and the book must not move.
        """
        order = self.submit_market_order(
            ticker=ticker, shares=shares, side=side,
            client_order_id=client_order_id)
        return self.await_terminal(order, deadline_seconds)
