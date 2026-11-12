"""Hard programmatic risk controls.

Every decision the LLM produces flows through `validate_decisions` before execution.
The model can't bypass these. They mirror the rules in the prompt but are enforced
independently — defense in depth.

Shorting is gated behind the `shorting_enabled` config flag (default FALSE). With
the flag off the validator runs the long-only rule book unchanged and rejects any
stray SHORT/COVER outright, so production behavior is identical to before shorting
existed. With the flag on, the short-aware path additionally enforces the gross-short
cap, the gross-exposure cap, and the long-exposure cap, with shorts counting toward
the per-name and total-position limits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..config_loader import load_settings, universe_symbols
from ..price_checks import is_finite_number, is_valid_price
from .portfolio import Portfolio

logger = logging.getLogger("llmlab.risk")

_LONG_ACTIONS = ("BUY", "SELL")
_SHORT_ACTIONS = ("SHORT", "COVER")
# Actions that increase exposure (and so can breach an exposure cap). SELL and
# COVER only ever reduce exposure, so they never need a cap check.
_EXPOSURE_INCREASING = ("BUY", "SHORT")

# Validator drop for a decision on a ticker with no valid price this cycle
# (price_validity_and_source_ruling.md §1a): added to the closed validator
# reason set. Such a ticker is not presented as tradable; a decision on it
# anyway is a named-reason decision-record event, never an order.
PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"

# Stop-evaluation outcomes. "Can't evaluate" is its own classified state: a
# missing or non-finite price must never read as a quiet pass.
STOP_BREACH = "breach"
STOP_CLEAR = "clear"
STOP_UNEVALUABLE = "unevaluable"


def _price_unavailable(i: int, action: str, ticker: str,
                       prices: dict[str, float]) -> RiskViolation | None:
    if action == "HOLD" or is_valid_price(prices.get(ticker)):
        return None
    return RiskViolation(i, PRICE_UNAVAILABLE,
                         f"{action} {ticker} dropped: no valid price this cycle (not tradable)")


@dataclass
class RiskViolation:
    decision_index: int
    rule: str
    detail: str


def validate_decisions(
    decisions: list[dict[str, Any]],
    portfolio: Portfolio,
    prices: dict[str, float],
    trades_already_executed_today: int = 0,
    shorting_enabled: bool | None = None,
) -> tuple[list[dict[str, Any]], list[RiskViolation]]:
    """Filter decisions against the rule book.

    Returns (accepted_decisions, violations). Violations are logged but other
    decisions still execute. Order matters: orders that would push the portfolio
    over a cap are dropped or resized.

    `trades_already_executed_today` is the persistent count of trades the model
    has used so far this trading session (across prior intraday runs). The cap
    enforced is `max_trades_per_day - trades_already_executed_today`.

    `shorting_enabled` overrides the config flag (used by the test harness to
    exercise the short path without touching production config). When None it
    is read from settings — which is FALSE in production until the July 1
    change window.
    """
    settings = load_settings()
    rules = settings["portfolio_rules"]
    if shorting_enabled is None:
        shorting_enabled = bool(rules.get("shorting_enabled", False))

    if portfolio.halted:
        return [], [RiskViolation(-1, "PORTFOLIO_HALTED",
                                  "Portfolio is halted; no trades accepted")]

    if shorting_enabled:
        accepted, violations = _validate_with_shorting(
            decisions, portfolio, prices, trades_already_executed_today, settings)
    else:
        accepted, violations = _validate_long_only(
            decisions, portfolio, prices, trades_already_executed_today, settings)

    if violations:
        logger.warning("[%s] %d violations: %s", portfolio.model_key, len(violations),
                       [(v.rule, v.detail) for v in violations])
    return accepted, violations


def _validate_long_only(
    decisions: list[dict[str, Any]],
    portfolio: Portfolio,
    prices: dict[str, float],
    trades_already_executed_today: int,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[RiskViolation]]:
    """Original long-only rule book. Unchanged behavior, plus a defensive reject
    of any SHORT/COVER that slips through while shorting is disabled."""
    rules = settings["portfolio_rules"]
    max_positions = int(rules["max_positions"])
    max_position_pct = float(rules["max_position_pct"])
    max_trades_per_day = int(rules["max_trades_per_day"])
    universe = set(universe_symbols())

    accepted: list[dict[str, Any]] = []
    violations: list[RiskViolation] = []

    projected_holdings = set(portfolio.holdings.keys())
    trades_today = max(0, int(trades_already_executed_today))
    initial_trades_today = trades_today

    for i, d in enumerate(decisions):
        action = str(d.get("action", "")).upper()

        if action in _SHORT_ACTIONS:
            violations.append(RiskViolation(
                i, "SHORTING_DISABLED",
                f"{action} rejected: shorting is disabled (shorting_enabled=false)"))
            continue

        if trades_today >= max_trades_per_day:
            violations.append(RiskViolation(
                i, "DAILY_TRADE_CAP",
                f"Already at {max_trades_per_day} trades today "
                f"({initial_trades_today} from prior intraday runs + "
                f"{trades_today - initial_trades_today} this run)"))
            continue

        ticker = d["ticker"]
        target_weight = float(d.get("target_weight", 0))

        if ticker not in universe:
            violations.append(RiskViolation(i, "OFF_UNIVERSE", f"{ticker} not in universe"))
            continue

        unpriced = _price_unavailable(i, action, ticker, prices)
        if unpriced:
            violations.append(unpriced)
            continue

        if target_weight < 0 or target_weight > max_position_pct + 1e-9:
            violations.append(RiskViolation(
                i, "MAX_POSITION_WEIGHT",
                f"{ticker} target_weight={target_weight:.4f} exceeds {max_position_pct}"))
            d["target_weight"] = min(max(target_weight, 0.0), max_position_pct)

        if action == "HOLD":
            accepted.append(d)
            continue

        if action == "BUY":
            if ticker not in projected_holdings:
                if len(projected_holdings) >= max_positions:
                    violations.append(RiskViolation(
                        i, "MAX_POSITIONS",
                        f"Cannot open {ticker}; already at {max_positions}-position cap"))
                    continue
                projected_holdings.add(ticker)
            accepted.append(d)
            trades_today += 1
            continue

        if action == "SELL":
            if ticker not in projected_holdings:
                violations.append(RiskViolation(i, "SELL_NOT_HELD", f"{ticker} not currently held"))
                continue
            if d.get("target_weight", 0) <= 1e-9:
                projected_holdings.discard(ticker)
            accepted.append(d)
            trades_today += 1
            continue

        violations.append(RiskViolation(i, "UNKNOWN_ACTION", f"Unrecognized action: {action}"))

    return accepted, violations


def _validate_with_shorting(
    decisions: list[dict[str, Any]],
    portfolio: Portfolio,
    prices: dict[str, float],
    trades_already_executed_today: int,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[RiskViolation]]:
    """Short-aware rule book.

    Caps (all as fractions of current equity, which is value-neutral across a
    decision batch since every fill is value-neutral at its fill price):
      - per-name |weight| <= max_position_pct (longs and shorts alike) — resized
      - gross long  Σ long value      <= max_long_exposure_pct   (default 100%)
      - gross short Σ |short value|    <= max_gross_short_pct     (default  20%)
      - gross total long + |short|     <= max_gross_exposure_pct  (default 120%)
      - total positions (shorts incl.) <= max_positions
      - daily trade cap (shorts/covers count)
    Net exposure is left free: with longs >= 0 and gross short <= 20%, net falls
    out in [-20%, +100%] without a separate constraint.
    """
    rules = settings["portfolio_rules"]
    max_positions = int(rules["max_positions"])
    max_position_pct = float(rules["max_position_pct"])
    max_trades_per_day = int(rules["max_trades_per_day"])
    max_long_pct = float(rules.get("max_long_exposure_pct", 1.0))
    max_gross_short_pct = float(rules.get("max_gross_short_pct", 0.20))
    max_gross_pct = float(rules.get("max_gross_exposure_pct", 1.20))
    universe = set(universe_symbols())

    equity = portfolio.total_value(prices)

    accepted: list[dict[str, Any]] = []
    violations: list[RiskViolation] = []

    # Project each name's signed market value (positive long, negative short)
    # forward as we accept decisions. Seeded from the current book.
    proj_value: dict[str, float] = {}
    for t, h in portfolio.holdings.items():
        proj_value[t] = h.market_value(portfolio.mark(t, prices))

    trades_today = max(0, int(trades_already_executed_today))
    initial_trades_today = trades_today

    def gross_long() -> float:
        return sum(v for v in proj_value.values() if v > 0)

    def gross_short() -> float:
        return sum(-v for v in proj_value.values() if v < 0)

    def open_positions() -> int:
        return sum(1 for v in proj_value.values() if abs(v) > 1e-9)

    for i, d in enumerate(decisions):
        action = str(d.get("action", "")).upper()
        ticker = str(d.get("ticker", "")).upper()
        target_weight = float(d.get("target_weight", 0))

        if action not in ("HOLD",) + _LONG_ACTIONS + _SHORT_ACTIONS:
            violations.append(RiskViolation(i, "UNKNOWN_ACTION", f"Unrecognized action: {action}"))
            continue

        if ticker not in universe:
            violations.append(RiskViolation(i, "OFF_UNIVERSE", f"{ticker} not in universe"))
            continue

        unpriced = _price_unavailable(i, action, ticker, prices)
        if unpriced:
            violations.append(unpriced)
            continue

        # Per-name weight cap — clamp/resize (longs and shorts alike).
        if target_weight < 0 or target_weight > max_position_pct + 1e-9:
            violations.append(RiskViolation(
                i, "MAX_POSITION_WEIGHT",
                f"{ticker} target_weight={target_weight:.4f} exceeds per-name cap {max_position_pct}"))
            target_weight = min(max(target_weight, 0.0), max_position_pct)
            d["target_weight"] = target_weight

        if action == "HOLD":
            accepted.append(d)
            continue

        cur = proj_value.get(ticker, 0.0)
        cur_is_long = cur > 1e-9
        cur_is_short = cur < -1e-9

        # ----- direction integrity -----
        if action == "BUY" and cur_is_short:
            violations.append(RiskViolation(
                i, "DIRECTION_CONFLICT", f"Cannot BUY {ticker}: held short — COVER it first"))
            continue
        if action == "SHORT" and cur_is_long:
            violations.append(RiskViolation(
                i, "DIRECTION_CONFLICT", f"Cannot SHORT {ticker}: held long — SELL it first"))
            continue
        if action == "SELL" and not cur_is_long:
            violations.append(RiskViolation(i, "SELL_NOT_HELD", f"{ticker} not currently held long"))
            continue
        if action == "COVER" and not cur_is_short:
            violations.append(RiskViolation(i, "COVER_NOT_SHORT", f"{ticker} not currently held short"))
            continue

        # ----- daily trade cap -----
        if trades_today >= max_trades_per_day:
            violations.append(RiskViolation(
                i, "DAILY_TRADE_CAP",
                f"Already at {max_trades_per_day} trades today "
                f"({initial_trades_today} from prior runs + "
                f"{trades_today - initial_trades_today} this run)"))
            continue

        if equity <= 0:
            # Can't size exposure against non-positive equity. Allow only
            # exposure-reducing actions through.
            if action in _EXPOSURE_INCREASING:
                violations.append(RiskViolation(
                    i, "NO_EQUITY", f"Equity <= 0; cannot open/add {action} on {ticker}"))
                continue

        # ----- exposure-reducing actions: always allowed -----
        if action in ("SELL", "COVER"):
            target_value = equity * target_weight if equity > 0 else 0.0
            # SELL targets a smaller long; COVER targets a smaller short.
            new_value = target_value if action == "SELL" else -target_value
            # Never let a reduce flip past flat or grow the position.
            if action == "SELL":
                new_value = min(max(0.0, new_value), cur)
            else:  # COVER
                new_value = max(min(0.0, new_value), cur)
            if abs(new_value) <= 1e-9:
                proj_value.pop(ticker, None)
            else:
                proj_value[ticker] = new_value
            accepted.append(d)
            trades_today += 1
            continue

        # ----- exposure-increasing actions: BUY (long) / SHORT (short) -----
        # New position? Check the total-position cap (shorts count).
        is_new = not (cur_is_long or cur_is_short)
        if is_new and open_positions() >= max_positions:
            violations.append(RiskViolation(
                i, "MAX_POSITIONS",
                f"Cannot open {ticker}; already at {max_positions}-position cap"))
            continue

        desired_value = equity * target_weight  # magnitude desired for this name

        if action == "BUY":
            gl_excl = gross_long() - max(cur, 0.0)
            gs = gross_short()
            long_headroom = max(0.0, equity * max_long_pct - gl_excl)
            gross_headroom = max(0.0, equity * max_gross_pct - (gl_excl + gs))
            allowed = min(desired_value, long_headroom, gross_headroom)
            if allowed <= max(cur, 0.0) + 1e-9:
                # No room to increase beyond what's already held.
                violations.append(RiskViolation(
                    i, "EXPOSURE_CAP",
                    f"BUY {ticker} rejected: long/gross exposure cap leaves no headroom "
                    f"(long_headroom={long_headroom:.2f}, gross_headroom={gross_headroom:.2f})"))
                continue
            if allowed < desired_value - 1e-9:
                violations.append(RiskViolation(
                    i, "EXPOSURE_CAP_RESIZED",
                    f"BUY {ticker} resized to fit exposure caps: "
                    f"{desired_value:.2f} -> {allowed:.2f}"))
                d["target_weight"] = allowed / equity
            proj_value[ticker] = allowed
            accepted.append(d)
            trades_today += 1
            continue

        if action == "SHORT":
            gs_excl = gross_short() - max(-cur, 0.0)
            gl = gross_long()
            short_headroom = max(0.0, equity * max_gross_short_pct - gs_excl)
            gross_headroom = max(0.0, equity * max_gross_pct - (gl + gs_excl))
            allowed = min(desired_value, short_headroom, gross_headroom)
            if allowed <= max(-cur, 0.0) + 1e-9:
                violations.append(RiskViolation(
                    i, "EXPOSURE_CAP",
                    f"SHORT {ticker} rejected: gross-short/gross exposure cap leaves no headroom "
                    f"(short_headroom={short_headroom:.2f}, gross_headroom={gross_headroom:.2f})"))
                continue
            if allowed < desired_value - 1e-9:
                violations.append(RiskViolation(
                    i, "EXPOSURE_CAP_RESIZED",
                    f"SHORT {ticker} resized to fit exposure caps: "
                    f"{desired_value:.2f} -> {allowed:.2f}"))
                d["target_weight"] = allowed / equity
            proj_value[ticker] = -allowed
            accepted.append(d)
            trades_today += 1
            continue

    return accepted, violations


def evaluate_portfolio_stop(portfolio: Portfolio, prices: dict[str, float]) -> str:
    """The drawdown halt as a three-state verdict: breach, clear or unevaluable.

    Equity (total_value) already nets short liabilities, so a short that moves
    against the book pulls equity down and counts toward the drawdown halt with
    no special handling.

    With every held name priced, this is the registered test on equity. When a
    held name has no valid price this cycle (price_validity_and_source_ruling.md)
    the halt is never decided on a stand-in mark. Instead the check is bounded:
    unpriced longs are valued at zero, the least they can be worth. If the book
    is clear of the stop even then, the result is proven and the verdict is
    "clear". Otherwise -- or if any unpriced holding is a short, whose loss has
    no bound -- the verdict is "unevaluable", which the caller alerts on. A NaN
    must never read as a quiet pass, which is what `drawdown >= stop` did.
    """
    settings = load_settings()
    stop_pct = float(settings["risk_controls"]["stop_loss_portfolio_pct"])
    if not settings["risk_controls"]["halt_on_stop_loss"]:
        return STOP_CLEAR
    if portfolio.inception_value <= 0:
        return STOP_CLEAR
    unpriced = [t for t in portfolio.holdings if not is_valid_price(prices.get(t))]
    if not unpriced:
        current = portfolio.total_value(prices)
        if not is_finite_number(current):
            return STOP_UNEVALUABLE
        drawdown = 1.0 - (current / portfolio.inception_value)
        if drawdown >= stop_pct:
            logger.error("[%s] PORTFOLIO STOP TRIGGERED — drawdown %.2f%% ≥ %.2f%%",
                         portfolio.model_key, drawdown * 100, stop_pct * 100)
            return STOP_BREACH
        return STOP_CLEAR
    if any(portfolio.holdings[t].is_short for t in unpriced):
        logger.error("[%s] Drawdown halt UNEVALUABLE: unpriced short(s) %s",
                     portfolio.model_key, unpriced)
        return STOP_UNEVALUABLE
    floor = portfolio.cash + sum(h.market_value(float(prices[t]))
                                 for t, h in portfolio.holdings.items() if t not in unpriced)
    if not is_finite_number(floor):
        return STOP_UNEVALUABLE
    if 1.0 - (floor / portfolio.inception_value) < stop_pct:
        return STOP_CLEAR
    logger.error("[%s] Drawdown halt UNEVALUABLE: with unpriced %s at zero the book "
                 "is not provably clear of the %.2f%% stop", portfolio.model_key,
                 unpriced, stop_pct * 100)
    return STOP_UNEVALUABLE


def check_portfolio_stop(portfolio: Portfolio, prices: dict[str, float]) -> bool:
    """True only on a proven breach. See evaluate_portfolio_stop for the
    unevaluable state, which callers must handle; this wrapper keeps the
    original boolean contract for existing callers."""
    return evaluate_portfolio_stop(portfolio, prices) == STOP_BREACH


def position_stops_unevaluable(portfolio: Portfolio, prices: dict[str, float]) -> list[str]:
    """Held tickers whose per-position stop cannot be evaluated this cycle
    because they have no valid price. Classified and alerted by the caller;
    check_position_stops skips them rather than testing a stand-in mark."""
    return sorted(t for t, h in portfolio.holdings.items()
                  if h.avg_cost and not is_valid_price(prices.get(t)))


def check_position_stops(portfolio: Portfolio, prices: dict[str, float]) -> list[str]:
    """Returns tickers that breached their per-position stop and should be force-closed.

    Long stop: price falls >= stop_loss_position_pct (15%) below entry.
    Short stop: price rises >= stop_loss_short_pct (10%) above entry — the same
    mechanism inverted, at the tighter short threshold. The executor decides
    whether to SELL (long) or COVER (short) from the holding's sign.

    A held ticker without a valid price is not tested at all (a NaN compares
    False, which used to pass silently): position_stops_unevaluable() names it.
    """
    settings = load_settings()
    rc = settings["risk_controls"]
    long_stop = float(rc["stop_loss_position_pct"])
    short_stop = float(rc.get("stop_loss_short_pct", 0.10))
    triggered: list[str] = []
    for ticker, h in portfolio.holdings.items():
        price = prices.get(ticker)
        if not is_valid_price(price) or not h.avg_cost:
            continue
        if h.is_short:
            gain_against = (price / h.avg_cost) - 1.0  # short loses as price rises
            if gain_against >= short_stop:
                logger.warning("[%s] Short stop on %s: +%.2f%% ≥ %.2f%% above entry",
                               portfolio.model_key, ticker, gain_against * 100, short_stop * 100)
                triggered.append(ticker)
        else:
            loss = 1.0 - (price / h.avg_cost)
            if loss >= long_stop:
                logger.warning("[%s] Position stop on %s: loss %.2f%% ≥ %.2f%%",
                               portfolio.model_key, ticker, loss * 100, long_stop * 100)
                triggered.append(ticker)
    return triggered
