"""Daily digest email — one HTML summary per trading day after the EOD run.

Built from the same on-disk sources of truth the dashboard and daily report
read, so the digest, the dashboard, and the markdown report never disagree:

  - summary["leaderboard"]          → ranks, daily %, cumulative, alpha vs SPY
  - /data/performance/{model}.jsonl → each model's daily P&L in dollars
  - summary["models"]               → trades executed today
  - /data/dashboard.json            → today's MVP trade
  - /data/trades/{model}_*.jsonl    → API cost today (via analytics)
  - /data/alerts/state.json         → event-alert overflow to bundle in

`send_daily_digest()` is idempotent per ET trading day (the EOD pass can fire
several times — chain handoff + cron backup), and skips non-trading days.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..config_loader import DATA_DIR, load_settings
from .alert_state import ensure_daily, load_state, save_state
from .email_alerts import send_email

logger = logging.getLogger("llmlab.alerts.digest")

GREEN = "#1e7e34"
RED = "#c0392b"
INK = "#1f2937"
MUTED = "#6b7280"
BORDER = "#e5e7eb"


# ---- formatters -----------------------------------------------------------

def _pct(x: float | None, sign: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x*100:+.2f}%" if sign else f"{x*100:.2f}%"


def _money(x: float | None) -> str:
    return "—" if x is None else f"${x:,.2f}"


def _signed_money(x: float | None) -> str:
    if x is None:
        return "—"
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.2f}"


def _color(x: float | None) -> str:
    if x is None:
        return INK
    return GREEN if x >= 0 else RED


# ---- data helpers ---------------------------------------------------------

def _daily_pnl_dollars(model_key: str) -> float | None:
    """Today minus prior-session total value, from the EOD performance log."""
    from ..analytics.performance import load_performance_history
    df = load_performance_history(model_key)
    if df.empty:
        return None
    if len(df) == 1:
        return 0.0
    try:
        return float(df["total_value"].iloc[-1]) - float(df["total_value"].iloc[-2])
    except (KeyError, IndexError, ValueError):
        return None


def _market_summary_line() -> str:
    """One-line S&P / Nasdaq / Dow move. Best-effort — never raises."""
    try:
        from ..data.market_data import fetch_index_data, INDEX_SYMBOLS
        index_data = fetch_index_data(lookback_days=5)
        parts = []
        for sym, label in INDEX_SYMBOLS.items():
            df = index_data.get(sym)
            if df is None or len(df) < 2:
                continue
            close = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            pct = (close / prev - 1) if prev else 0.0
            short = "S&P 500" if "S&P" in label else ("Nasdaq" if "Nasdaq" in label else "Dow")
            color = _color(pct)
            parts.append(f'{short} <span style="color:{color};font-weight:600;">{_pct(pct)}</span>')
        if parts:
            return " &nbsp;·&nbsp; ".join(parts)
    except Exception:
        logger.exception("digest: market summary line failed")
    return "Index data unavailable for this session."


def _api_cost_today(model_keys: list[str]) -> tuple[dict[str, float], float]:
    """Per-model and total API spend for the UTC trading day."""
    from ..analytics.performance import compute_api_cost_summary_window
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    per_model: dict[str, float] = {}
    total = 0.0
    for key in model_keys:
        try:
            window = compute_api_cost_summary_window(key, since=today_start)
            cost = float(window["cost_usd"])
        except Exception:
            cost = 0.0
        per_model[key] = cost
        total += cost
    return per_model, total


def _mvp_trade(date_str: str) -> dict[str, Any] | None:
    """Today's MVP trade from the freshly-built dashboard.json (None if the
    MVP on file isn't from today's session)."""
    import json
    path = DATA_DIR / "dashboard.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            mvp = json.load(f).get("mvp_trade")
    except (json.JSONDecodeError, OSError):
        return None
    if not mvp or mvp.get("date") != date_str:
        return None
    return mvp


def _day_counter(settings: dict[str, Any], date_str: str) -> str:
    try:
        inception = datetime.strptime(settings["experiment_start_date"], "%Y-%m-%d").date()
        end = datetime.strptime(settings["experiment_end_date"], "%Y-%m-%d").date()
        run = datetime.strptime(date_str, "%Y-%m-%d").date()
        day_num = max(1, (run - inception).days + 1)
        total_days = (end - inception).days + 1
        return f"Day {day_num} / {total_days}"
    except (ValueError, KeyError):
        return "Day —"


def _health_lines(summary: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    """System-health bullets. Empty list ⇒ all nominal."""
    issues: list[str] = []
    models_cfg = settings.get("models", {})

    def name(k: str) -> str:
        return models_cfg.get(k, {}).get("display_name", k.upper())

    for row in summary.get("leaderboard", []):
        key = row.get("model_key")
        if row.get("halted"):
            issues.append(f"{name(key)} — portfolio HALTED (hard stop-loss).")
        elif not row.get("last_api_success", True):
            issues.append(f"{name(key)} — API failure on the latest run.")

    for m in summary.get("models", []):
        status = m.get("status")
        if status in ("API_FAIL", "ERROR"):
            issues.append(f"{name(m.get('model_key'))} — run status {status}"
                          + (f": {m.get('error')}" if m.get("error") else "."))

    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for i in issues:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ---- HTML sections --------------------------------------------------------

def _leaderboard_table(summary: dict[str, Any], settings: dict[str, Any]) -> str:
    models_cfg = settings.get("models", {})
    head_cells = ["#", "Model", "Daily %", "Daily P&L", "Cumulative", "Alpha vs SPY"]
    aligns = ["center", "left", "right", "right", "right", "right"]
    head = "".join(
        f'<th style="padding:8px 10px;text-align:{a};font-size:12px;color:{MUTED};'
        f'text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid {BORDER};">{c}</th>'
        for c, a in zip(head_cells, aligns)
    )
    rows = ""
    for row in summary.get("leaderboard", []):
        key = row.get("model_key")
        name = models_cfg.get(key, {}).get("display_name", str(key).upper())
        daily = row.get("daily_pnl_pct")
        cum = row.get("cumulative_return")
        alpha = row.get("alpha_vs_spy")
        pnl = _daily_pnl_dollars(key)
        rank = row.get("rank", "")
        bold = "font-weight:700;" if rank == 1 else ""
        cells = [
            (f'<span style="display:inline-block;min-width:20px;{bold}">{rank}</span>', "center", INK, ""),
            (name, "left", INK, bold),
            (_pct(daily), "right", _color(daily), ""),
            (_signed_money(pnl), "right", _color(pnl), ""),
            (_pct(cum), "right", _color(cum), ""),
            (_pct(alpha) if alpha is not None else "—", "right", _color(alpha), ""),
        ]
        tds = "".join(
            f'<td style="padding:9px 10px;text-align:{a};color:{c};font-size:14px;{extra}'
            f'border-bottom:1px solid {BORDER};">{val}</td>'
            for val, a, c, extra in cells
        )
        rows += f"<tr>{tds}</tr>"
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;">'
        f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    )


def _stat_cards(total_trades: int, total_cost: float) -> str:
    def card(label: str, value: str) -> str:
        return (
            f'<td width="50%" style="padding:6px;">'
            f'<div style="background:#f9fafb;border:1px solid {BORDER};border-radius:8px;padding:14px 16px;">'
            f'<div style="font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.04em;">{label}</div>'
            f'<div style="font-size:22px;font-weight:700;color:{INK};margin-top:4px;">{value}</div>'
            f"</div></td>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + card("Total trades today", str(total_trades))
        + card("API cost today", f"${total_cost:,.4f}")
        + "</tr></table>"
    )


def _mvp_card(mvp: dict[str, Any] | None) -> str:
    if not mvp:
        return (
            f'<div style="color:{MUTED};font-size:14px;padding:4px 2px;">'
            f"No trades executed today.</div>"
        )
    side = mvp.get("side", "")
    ticker = mvp.get("ticker", "")
    name = mvp.get("display_name", mvp.get("model_key", ""))
    pnl = mvp.get("pnl_pct")
    conf = mvp.get("confidence")
    summary_txt = mvp.get("summary", "") or ""
    fill = mvp.get("fill_price")
    pnl_str = ""
    if pnl is not None:
        pnl_str = (f'<span style="color:{_color(pnl)};font-weight:700;">{_pct(pnl)}</span>')
    reason = mvp.get("selection_reason", "")
    reason_label = {
        "highest_gain": "best return today",
        "highest_conviction": "highest conviction",
        "only_trade": "only trade today",
    }.get(reason, "")
    head = f'<b>{name}</b> &nbsp;·&nbsp; {side} <b>{ticker}</b>'
    if fill:
        head += f' @ {_money(fill)}'
    if pnl_str:
        head += f' &nbsp;·&nbsp; {pnl_str}'
    if conf is not None:
        head += f' &nbsp;·&nbsp; conviction {conf}/10'
    sub = f'<div style="color:{MUTED};font-size:12px;margin-top:2px;">{reason_label}</div>' if reason_label else ""
    body = f'<div style="color:{INK};font-size:13px;margin-top:8px;font-style:italic;">&ldquo;{summary_txt}&rdquo;</div>' if summary_txt else ""
    return (
        f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px 16px;">'
        f'<div style="font-size:15px;color:{INK};">{head}</div>{sub}{body}</div>'
    )


def _health_section(issues: list[str]) -> str:
    if not issues:
        return (
            f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;'
            f'padding:12px 16px;color:{GREEN};font-weight:600;font-size:14px;">'
            f"✓ All systems nominal</div>"
        )
    items = "".join(
        f'<li style="margin:4px 0;color:{INK};font-size:14px;">{i}</li>' for i in issues
    )
    return (
        f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 16px;">'
        f'<div style="color:{RED};font-weight:700;font-size:14px;margin-bottom:6px;">'
        f"{len(issues)} issue(s) detected</div>"
        f'<ul style="margin:0;padding-left:20px;">{items}</ul></div>'
    )


def _overflow_section(overflow: list[dict[str, Any]]) -> str:
    if not overflow:
        return ""
    items = ""
    for ev in overflow:
        sev = ev.get("severity", "INFO")
        color = RED if sev == "CRITICAL" else ("#d35400" if sev == "WARN" else MUTED)
        items += (
            f'<li style="margin:6px 0;font-size:13px;color:{INK};">'
            f'<span style="color:{color};font-weight:700;">[{sev}]</span> '
            f'{ev.get("title","")}<br>'
            f'<span style="color:{MUTED};">{ev.get("body","")}</span></li>'
        )
    return (
        f'<div style="margin-top:8px;">'
        f'<div style="font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">'
        f"Additional alerts (beyond the daily cap)</div>"
        f'<ul style="margin:0;padding-left:18px;list-style:none;">{items}</ul></div>'
    )


def _section_header(title: str) -> str:
    return (
        f'<div style="font-size:13px;font-weight:700;color:{INK};text-transform:uppercase;'
        f'letter-spacing:.05em;margin:22px 0 10px 0;border-bottom:1px solid {BORDER};padding-bottom:6px;">'
        f"{title}</div>"
    )


# ---- assembly -------------------------------------------------------------

def build_digest(summary: dict[str, Any], settings: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return (subject, html) for the daily digest."""
    if settings is None:
        settings = load_settings()
    date_str = summary.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    model_keys = [m.get("model_key") for m in summary.get("models", []) if m.get("model_key")]
    if not model_keys:
        model_keys = [k for k, c in settings.get("models", {}).items() if c.get("enabled", True)]

    total_trades = sum(int(m.get("trades_today", 0) or 0) for m in summary.get("models", []))
    _, total_cost = _api_cost_today(model_keys)
    mvp = _mvp_trade(date_str)
    issues = _health_lines(summary, settings)
    day_counter = _day_counter(settings, date_str)
    phase = settings.get("phase", "")
    market_line = _market_summary_line()

    daily_bucket = load_state().get("daily") or {}
    overflow = daily_bucket.get("overflow", []) if daily_bucket.get("date") == date_str else []

    subject = f"LLM Trading Lab — Daily Digest {date_str}"

    html = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid {BORDER};">

  <tr><td style="background:{INK};padding:20px 24px;">
    <div style="color:#fff;font-size:20px;font-weight:700;">LLM Trading Lab — Daily Digest</div>
    <div style="color:#cbd5e1;font-size:13px;margin-top:4px;">{date_str} &nbsp;·&nbsp; {day_counter} &nbsp;·&nbsp; {phase}</div>
  </td></tr>

  <tr><td style="padding:18px 24px 0 24px;">
    <div style="font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.04em;">Market close</div>
    <div style="font-size:15px;color:{INK};margin-top:4px;">{market_line}</div>

    {_section_header("Leaderboard")}
    {_leaderboard_table(summary, settings)}

    {_section_header("Activity &amp; cost")}
    {_stat_cards(total_trades, total_cost)}

    {_section_header("Today's MVP trade")}
    {_mvp_card(mvp)}

    {_section_header("System health")}
    {_health_section(issues)}
    {_overflow_section(overflow)}
  </td></tr>

  <tr><td style="padding:20px 24px;color:{MUTED};font-size:11px;border-top:1px solid {BORDER};margin-top:16px;">
    Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &nbsp;·&nbsp; automated end-of-day digest.
  </td></tr>

</table>
</td></tr></table>
</body></html>"""
    return subject, html


def send_daily_digest(summary: dict[str, Any], settings: dict[str, Any] | None = None) -> bool:
    """Send the digest once per ET trading day. Skips weekends/holidays and
    re-sends (the EOD pass can trigger multiple times in a day)."""
    if settings is None:
        settings = load_settings()
    date_str = summary.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Belt-and-suspenders: never digest a non-trading day even if forced.
    try:
        from ..data.market_data import is_market_open_today
        ref = datetime.strptime(date_str, "%Y-%m-%d")
        if not is_market_open_today(ref):
            logger.info("digest: %s is not a trading day — skipping", date_str)
            return False
    except Exception:
        logger.exception("digest: trading-day check failed — continuing")

    # Idempotent per day.
    state = load_state()
    daily = ensure_daily(state, date_str)
    if daily.get("digest_sent"):
        logger.info("digest: already sent for %s — skipping", date_str)
        return False

    subject, html = build_digest(summary, settings)
    ok = send_email(subject, html, alert_type="daily_digest",
                    trigger="eod_digest", settings=settings)
    if ok:
        daily["digest_sent"] = True
        save_state(state)
    return ok
