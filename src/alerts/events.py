"""Event-alert detection + dispatch.

Two halves:

  1. `dispatch_event()` — the single choke point every event alert flows
     through. It applies the anti-spam rules:
       * de-duplication (a given dedup_key fires at most once per ET day),
       * the per-day cap (default 10 event emails/day; anything past the cap
         is queued into the daily "overflow" bucket and bundled into the
         digest instead of emailed), and
       * the once-ever milestone ledger.
     It is the ONLY writer of /data/alerts/state.json, guarded by a process
     lock so the parallel per-model threads can't race it.

  2. The detectors — pure read-from-disk functions that scan the EOD state
     and return a list of event specs. `run_eod_alert_sweep()` runs them in
     priority order (most critical first, so the cap favors them) and
     dispatches each. Detectors never mutate state; dispatch persists.

Intraday alerts (stop-loss, API failure, halts, crashes) come in through the
same `dispatch_event()` via `alerter.send_alert()` as they happen.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..config_loader import (
    DATA_DIR,
    NEWS_CACHE_DIR,
    PERFORMANCE_DIR,
    TRADES_DIR,
    load_settings,
)
from .alert_state import ensure_daily, load_state, save_state
from .email_alerts import send_email

logger = logging.getLogger("llmlab.alerts.events")

EASTERN = ZoneInfo("America/New_York")

# Serializes the read-modify-write of state.json. The intraday pipeline runs
# each model in its own thread and any of them can raise an alert, so without
# this two concurrent dispatches could clobber each other's cap/dedup updates.
_DISPATCH_LOCK = threading.Lock()

# Human-readable labels for the machine `kind` tags, used in email subjects/bodies.
KIND_LABELS: dict[str, str] = {
    "position_stop": "Position stop-loss",
    "portfolio_halt": "Portfolio risk halt",
    "api_failure": "Model API failure",
    "milestone": "Return milestone",
    "negative_return": "Negative cumulative return",
    "ath": "New all-time high",
    "oversized_trade": "Oversized single trade",
    "news_impact": "High-impact news on held stock",
    "macro_event": "Major market event",
    "state_anomaly": "State integrity anomaly",
    "missed_run": "Missed scheduled run",
    "pipeline_error": "Pipeline error",
    "market_data_failure": "Market data failure",
    "dashboard_failure": "Dashboard build failure",
    "report_failure": "Daily report failure",
    "budget": "API budget threshold",
    "model_transition": "Model version transition",
}


# ---------------------------------------------------------------------------
# Trigger #11 — market-wide event taxonomy.
#
# Five categories of genuinely market-wide shocks, each a list of regex
# patterns matched (case-insensitively) against a macro headline's title +
# summary. Everything uses word boundaries so we don't false-positive on
# substrings ("war" must not match "warm/forward/Warner", "coup" must not
# match "couple/coupon"). This is the keyword half of the gate; the sentiment
# half (|VADER| > threshold) is applied in detect_macro_market_events so only
# high-conviction headlines fire. Categories are checked in this order and a
# headline is assigned to the FIRST category it matches.
# ---------------------------------------------------------------------------
MACRO_EVENT_PATTERNS: dict[str, list[str]] = {
    "Geopolitical shock": [
        r"\bwar\b", r"\bwarfare\b", r"\binvasion\b", r"\binvade[sd]?\b",
        r"\bmissile(s)?\b", r"\bairstrike(s)?\b", r"\bair strike(s)?\b",
        r"\bceasefire\b", r"\btruce\b",
        r"\bmilitary (conflict|strike|action|offensive|escalation)\b",
        r"\bterror(ism|ist)?\b", r"\bcoup\b",
        r"\b(nuclear|chemical) (attack|strike|test|threat|war)\b",
    ],
    "Monetary policy surprise": [
        r"\bemergency\b.{0,30}\b(rate|fed|fomc|cut|hike|meeting|decision)\b",
        r"\b(surprise|unexpected|shock|unscheduled|inter-?meeting)\b.{0,30}\b(rate|hike|cut|fed|fomc)\b",
        r"\b(rate|fed|fomc)\b.{0,30}\b(emergency|surprise|unscheduled|unexpected|inter-?meeting)\b",
        r"\bemergency (rate|fed|fomc)\b",
    ],
    "Macro crisis": [
        r"\bmarket crash\b", r"\bstock market crash\b",
        r"\bcircuit breaker(s)?\b",
        r"\b(stocks?|markets?|dow|nasdaq|s ?& ?p ?500|wall street|equities)\b.{0,25}\b(crash|plunge|collapse|tumble|plummet|nosedive|rout|meltdown)\b",
        r"\b(crash|plunge|collapse|sell-?off|rout|meltdown)\b.{0,25}\b(stocks?|markets?|dow|nasdaq|s ?& ?p|wall street|equities)\b",
        r"\bbank(ing)? (failure|collapse|run|crisis|bailout)\b",
        r"\b(bank|lender) (fail|fails|failed|collapse|collapses|collapsed)\b",
        r"\bsovereign default\b", r"\bdebt default\b",
        r"\bdefault(s|ed|ing)?\b.{0,25}\b(debt|bond|sovereign|nation|country|loan)\b",
        r"\bdebt ceiling\b", r"\bdebt limit\b",
        r"\b(credit (downgrade|rating)|rating (cut|downgrade)|downgrade(s|d)?)\b.{0,25}\b(u\.?s\.?|treasury|sovereign|credit|debt|rating|nation)\b",
        r"\b(u\.?s\.?|treasury|sovereign)\b.{0,25}\b(credit )?(downgrade(s|d)?|rating cut)\b",
    ],
    "Systemic event": [
        r"\bpandemic\b", r"\bepidemic\b", r"\b(public )?health emergency\b",
        r"\b(earthquake|hurricane|tsunami|wildfire(s)?|typhoon|cyclone)\b",
        r"\bnatural disaster\b",
        r"\boil\b.{0,25}\b(shock|embargo|supply|spike(s)?|surge(s)?|soar(s|ed)?|crisis)\b",
        r"\bopec\b.{0,25}\b(cut|production|supply|output|quota)\b",
        r"\b(currency|fx) (crisis|crash|collapse|war)\b",
        r"\b(yen|euro|dollar|pound|peso|lira|ruble|rouble|yuan|won|rupee)\b.{0,20}\b(crash|collapse|plunge(s)?|crisis|tumble(s)?|devalu)\b",
        r"\bdevaluation\b",
    ],
    "Severe economic data": [
        r"\brecession\b", r"\bstagflation\b", r"\beconomic depression\b",
        r"\b(cpi|inflation|jobs report|nonfarm|payrolls?|unemployment|pce)\b.{0,30}\b(surprise|shock|surge(s|d)?|spike(s|d)?|soar(s|ed)?|jump(s|ed)?|plunge(s|d)?|miss(es|ed)?|beat|hotter|cooler|hot)\b",
        r"\b(hot(ter)?|surprise|shock|surging|soaring)\b.{0,20}\b(cpi|inflation|payrolls?|jobs report)\b",
    ],
}

_COMPILED_MACRO_PATTERNS: dict[str, list[re.Pattern]] = {
    cat: [re.compile(p, re.IGNORECASE) for p in pats]
    for cat, pats in MACRO_EVENT_PATTERNS.items()
}


def _match_macro_category(text: str) -> str | None:
    """Return the first market-wide event category whose keywords appear in
    `text`, or None. `text` is normally the headline title + summary."""
    if not text:
        return None
    for category, patterns in _COMPILED_MACRO_PATTERNS.items():
        for pat in patterns:
            if pat.search(text):
                return category
    return None


def session_date() -> str:
    """Current ET trading-day string — the bucket key for daily de-dup."""
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


# ===========================================================================
# EVENT EMAIL RENDERING
# ===========================================================================

def _accent_for(kind: str, severity: str) -> str:
    if kind in ("milestone", "ath"):
        return "#1e7e34"  # green — these are good news
    if severity == "CRITICAL":
        return "#c0392b"
    if severity == "WARN":
        return "#d35400"
    return "#2c3e50"


def render_event_html(
    kind: str,
    severity: str,
    title: str,
    body: str,
    context: dict[str, Any] | None,
    fired_at_et: str,
) -> str:
    """Compact, scannable HTML for a single event alert (mobile-friendly)."""
    context = context or {}
    accent = _accent_for(kind, severity)
    label = KIND_LABELS.get(kind, kind.replace("_", " ").title())

    models = context.get("models")
    if not models:
        single = context.get("model")
        models = [single] if single else []
    models_str = ", ".join(str(m) for m in models) if models else "—"

    numbers = context.get("numbers") or {}
    number_rows = ""
    for k, v in numbers.items():
        number_rows += (
            f'<tr><td style="padding:3px 12px 3px 0;color:#666;white-space:nowrap;">{k}</td>'
            f'<td style="padding:3px 0;color:#111;font-weight:600;">{v}</td></tr>'
        )

    return f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e4e4e7;">
  <tr><td style="background:{accent};padding:14px 20px;">
    <div style="color:#fff;font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.85;">{severity} &middot; {label}</div>
    <div style="color:#fff;font-size:18px;font-weight:700;margin-top:2px;">{title}</div>
  </td></tr>
  <tr><td style="padding:18px 20px 6px 20px;color:#222;font-size:15px;line-height:1.5;">{body}</td></tr>
  <tr><td style="padding:6px 20px 18px 20px;">
    <table role="presentation" cellpadding="0" cellspacing="0" style="font-size:13px;">
      <tr><td style="padding:3px 12px 3px 0;color:#666;white-space:nowrap;">Model(s)</td>
          <td style="padding:3px 0;color:#111;font-weight:600;">{models_str}</td></tr>
      {number_rows}
      <tr><td style="padding:3px 12px 3px 0;color:#666;white-space:nowrap;">Time</td>
          <td style="padding:3px 0;color:#111;">{fired_at_et}</td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:12px 20px;background:#fafafa;border-top:1px solid #eee;color:#9ca3af;font-size:11px;">
    LLM Trading Lab automated alert
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


# ===========================================================================
# DISPATCH — the single choke point with dedup + cap + overflow
# ===========================================================================

def _should_email(severity: str, email: bool | None) -> bool:
    if email is not None:
        return email
    return severity in ("WARN", "CRITICAL")


def dispatch_event(
    kind: str,
    severity: str,
    title: str,
    body: str,
    context: dict[str, Any] | None = None,
    *,
    dedup_key: str | None = None,
    email: bool | None = None,
    mark_milestones: tuple[str, list[int]] | None = None,
    settings: dict[str, Any] | None = None,
) -> str:
    """Route one event. Returns disposition: sent | overflow | deduped |
    logged | send_failed | disabled.

    `email`: None → email iff severity is WARN/CRITICAL. Positive events
    (milestones, ATH) pass email=True explicitly.
    `dedup_key`: defaults to "{kind}:{model}". A key fires at most once/day.
    `mark_milestones`: (model_key, thresholds) recorded into the once-ever
    ledger when the event is actually handled (sent or overflowed).
    """
    context = context or {}
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = {}
    alerts_cfg = settings.get("alerts", {}) or {}

    if not _should_email(severity, email):
        # Log-only event (e.g. INFO model transitions). Already recorded in
        # alerts.jsonl + pipeline log by the caller; nothing more to do.
        return "logged"

    if not alerts_cfg.get("enabled", True):
        return "disabled"

    if dedup_key is None:
        model = context.get("model") or ""
        dedup_key = f"{kind}:{model}" if model else f"{kind}:{title}"

    cap = int(alerts_cfg.get("max_event_alerts_per_day", 10))
    fired_at_et = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M ET")
    event_record = {
        "kind": kind,
        "severity": severity,
        "title": title,
        "body": body,
        "context": context,
        "fired_at_et": fired_at_et,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with _DISPATCH_LOCK:
        state = load_state()
        daily = ensure_daily(state, session_date())

        if dedup_key in daily["dedup_keys"]:
            return "deduped"

        def _mark_milestones() -> None:
            if mark_milestones:
                mk, thresholds = mark_milestones
                existing = set(state["milestones_fired"].get(mk, []))
                existing.update(int(t) for t in thresholds)
                state["milestones_fired"][mk] = sorted(existing)

        # Past the cap → queue into the digest overflow instead of emailing.
        if daily["sent_count"] >= cap:
            daily["overflow"].append(event_record)
            daily["dedup_keys"].append(dedup_key)
            daily["fired_log"].append({**_compact(event_record), "disposition": "overflow"})
            _mark_milestones()
            save_state(state)
            logger.info("Event over daily cap — bundled into digest: [%s] %s", kind, title)
            return "overflow"

        # Under the cap → send now.
        subject = f"[ALERT] {title}"
        html = render_event_html(kind, severity, title, body, context, fired_at_et)
        ok = send_email(
            subject, html,
            text_body=f"{severity} — {title}\n\n{body}\n\nModel(s): "
                      f"{', '.join(str(m) for m in (context.get('models') or ([context['model']] if context.get('model') else [])))}"
                      f"\nTime: {fired_at_et}",
            alert_type="event", trigger=kind, settings=settings,
        )
        if ok:
            daily["sent_count"] += 1
            daily["dedup_keys"].append(dedup_key)
            daily["fired_log"].append({**_compact(event_record), "disposition": "sent"})
            _mark_milestones()
            save_state(state)
            return "sent"
        # Transient send failure: do NOT mark dedup, so a later tick can retry.
        daily["fired_log"].append({**_compact(event_record), "disposition": "send_failed"})
        save_state(state)
        return "send_failed"


def _compact(event_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": event_record["kind"],
        "severity": event_record["severity"],
        "title": event_record["title"],
        "timestamp": event_record["timestamp"],
    }


# ===========================================================================
# DISK READERS shared by the detectors
# ===========================================================================

def _enabled_model_keys(settings: dict[str, Any]) -> list[str]:
    return [k for k, cfg in (settings.get("models") or {}).items()
            if cfg.get("enabled", True)]


def _display_name(settings: dict[str, Any], key: str) -> str:
    return (settings.get("models", {}).get(key, {}) or {}).get("display_name", key.upper())


def _perf_history(model_key: str):
    from ..analytics.performance import load_performance_history
    return load_performance_history(model_key)


def _eod_history(model_key: str):
    """Per-date-deduped performance history (one row per trading day, keeping
    the last value logged for each date).

    The performance logs carry some legacy duplicate-date rows from early
    build/test runs before `log_daily_snapshot` became idempotent. Comparing
    "today vs the prior session" on the raw frame could pick a same-day
    duplicate as the prior session, so every detector that reasons about
    day-over-day transitions reads through this instead. Mirrors the
    dashboard's correlation-matrix dedupe.
    """
    df = _perf_history(model_key)
    if df.empty or "date" not in df.columns:
        return df
    df = df.copy()
    df["_d"] = df["date"].astype(str).str[:10]
    df = df.groupby("_d", sort=True).last().reset_index()
    return df


def _read_today_trade_records(model_key: str, date_str: str) -> list[dict[str, Any]]:
    """All decision-log rows for this model on `date_str` (YYYY-MM-DD)."""
    month_str = date_str[:7]
    path = TRADES_DIR / f"{model_key}_{month_str}.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("date") == date_str:
                out.append(rec)
    return out


def _read_dashboard_portfolios() -> list[dict[str, Any]]:
    """Portfolios block from the freshly-rebuilt dashboard.json (has weights)."""
    path = DATA_DIR / "dashboard.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("portfolios", []) or []
    except (json.JSONDecodeError, OSError):
        return []


def _read_news_cache() -> dict[str, Any]:
    path = NEWS_CACHE_DIR / "cache.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _read_macro_top_cache() -> list[dict[str, Any]]:
    """Macro headlines from the dedicated NewsAPI macro feed cache
    (data/news_cache/macro_top.json), written by news.fetch_top_macro_headlines."""
    path = NEWS_CACHE_DIR / "macro_top.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(json.load(f).get("macro", []) or [])
    except (json.JSONDecodeError, OSError):
        return []


def _collect_macro_headlines(news_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Union of macro headlines from the freshest tick plus both on-disk
    caches, de-duplicated by case-folded title (first occurrence wins).

    Pulling from all three sources means the detector behaves identically
    whether it's called inline on a pipeline tick (news_data supplied) or
    later from the EOD sweep (reads disk only).
    """
    items: list[dict[str, Any]] = []
    if news_data:
        items.extend(news_data.get("macro", []) or [])
    items.extend(_read_macro_top_cache())                      # NewsAPI macro feed
    items.extend(_read_news_cache().get("macro", []) or [])    # provider-chain macro

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _models_positioning(settings: dict[str, Any]) -> dict[str, Any]:
    """Aggregate current positioning across the enabled models, for the
    macro-event alert body so the reader sees how exposed the portfolios are.

    Reads the freshly-built dashboard.json (which carries mark-to-market cash
    and total_value per model). Falls back to the live portfolio state files
    for holder counts if the dashboard payload isn't available — in that
    fallback cash % is left unknown rather than guessed without prices.

    Returns: n_models, total_cash_pct (float|None, dollar-weighted across all
    models), and most_held (list of (ticker, holder_count), top 5).
    """
    enabled = set(_enabled_model_keys(settings))
    snaps = [s for s in _read_dashboard_portfolios() if s.get("model_key") in enabled]

    total_cash = 0.0
    total_value = 0.0
    holders: dict[str, int] = {}
    n_models = 0

    if snaps:
        for snap in snaps:
            n_models += 1
            total_cash += float(snap.get("cash") or 0.0)
            total_value += float(snap.get("total_value") or 0.0)
            for h in snap.get("holdings", []) or []:
                t = h.get("ticker")
                if t:
                    holders[t] = holders.get(t, 0) + 1
        cash_pct = (total_cash / total_value) if total_value > 0 else None
    else:
        # Dashboard payload missing — count holders from live state, leave
        # cash % unknown (no prices on hand to value the holdings).
        from ..portfolio import load_portfolio
        for key in enabled:
            try:
                p = load_portfolio(key)
            except Exception:
                continue
            n_models += 1
            for t in p.holdings:
                holders[t] = holders.get(t, 0) + 1
        cash_pct = None

    most_held = sorted(holders.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {"n_models": n_models, "total_cash_pct": cash_pct, "most_held": most_held}


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.2f}%"


def _fmt_money(x: float | None) -> str:
    return "—" if x is None else f"${x:,.2f}"


# ===========================================================================
# DETECTORS — each returns list[event-spec dict] consumable by dispatch_event
# ===========================================================================

def detect_milestones(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """+N% cumulative-return milestones (+5, +10, +15, ...), once per
    threshold per model, ever. Reads the once-ever ledger to decide what's new.
    """
    step = int((settings.get("alerts", {}) or {}).get("milestone_step_pct", 5))
    if step <= 0:
        return []
    state = load_state()
    fired_ledger = state.get("milestones_fired", {})
    specs: list[dict[str, Any]] = []
    for key in _enabled_model_keys(settings):
        df = _eod_history(key)
        if df.empty or "cumulative_return" not in df.columns:
            continue
        cum = float(df["cumulative_return"].iloc[-1])
        cum_pct = cum * 100.0
        if cum_pct < step:
            continue
        highest = int(cum_pct // step) * step
        already = set(int(t) for t in fired_ledger.get(key, []))
        new_thresholds = [t for t in range(step, highest + 1, step) if t not in already]
        if not new_thresholds:
            continue
        top = max(new_thresholds)
        name = _display_name(settings, key)
        also = [t for t in new_thresholds if t != top]
        also_str = f" (also crossed +{', +'.join(str(t) for t in sorted(also))}% this session)" if also else ""
        specs.append({
            "kind": "milestone",
            "severity": "INFO",
            "email": True,
            "title": f"{name} crossed +{top}%",
            "body": f"{name} reached a cumulative return of <b>{_fmt_pct(cum)}</b>, "
                    f"crossing the +{top}% milestone for the first time{also_str}.",
            "context": {
                "model": key,
                "models": [name],
                "numbers": {
                    "Cumulative return": _fmt_pct(cum),
                    "Milestone": f"+{top}%",
                    "Portfolio value": _fmt_money(float(df["total_value"].iloc[-1])),
                },
            },
            "dedup_key": f"milestone:{key}:{top}",
            "mark_milestones": (key, new_thresholds),
        })
    return specs


def detect_negative_crossings(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Fire when a model crosses from non-negative into negative cumulative
    return (a transition, not every day it stays underwater)."""
    specs: list[dict[str, Any]] = []
    for key in _enabled_model_keys(settings):
        df = _eod_history(key)
        if df.empty or "cumulative_return" not in df.columns:
            continue
        today_cum = float(df["cumulative_return"].iloc[-1])
        if today_cum >= 0:
            continue
        prev_cum = float(df["cumulative_return"].iloc[-2]) if len(df) >= 2 else None
        crossed = prev_cum is None or prev_cum >= 0
        if not crossed:
            continue
        name = _display_name(settings, key)
        specs.append({
            "kind": "negative_return",
            "severity": "WARN",
            "title": f"{name} went negative",
            "body": f"{name}'s cumulative return has dropped below zero to "
                    f"<b>{_fmt_pct(today_cum)}</b>"
                    + (f" (from {_fmt_pct(prev_cum)} the prior session)." if prev_cum is not None else "."),
            "context": {
                "model": key,
                "models": [name],
                "numbers": {
                    "Cumulative return": _fmt_pct(today_cum),
                    "Prior session": _fmt_pct(prev_cum) if prev_cum is not None else "—",
                    "Portfolio value": _fmt_money(float(df["total_value"].iloc[-1])),
                },
            },
            "dedup_key": f"negative:{key}",
        })
    return specs


def detect_new_ath(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """New all-time-high portfolio value, only after >= ath_min_days of data."""
    min_days = int((settings.get("alerts", {}) or {}).get("ath_min_days", 10))
    specs: list[dict[str, Any]] = []
    for key in _enabled_model_keys(settings):
        df = _eod_history(key)
        if df.empty or "total_value" not in df.columns or len(df) < min_days:
            continue
        values = df["total_value"].astype(float)
        today = float(values.iloc[-1])
        prior_max = float(values.iloc[:-1].max())
        if today <= prior_max:
            continue
        name = _display_name(settings, key)
        cum = float(df["cumulative_return"].iloc[-1]) if "cumulative_return" in df.columns else None
        specs.append({
            "kind": "ath",
            "severity": "INFO",
            "email": True,
            "title": f"{name} hit a new all-time high",
            "body": f"{name} closed at a new all-time high of <b>{_fmt_money(today)}</b>, "
                    f"above its prior peak of {_fmt_money(prior_max)} (after {len(df)} trading days).",
            "context": {
                "model": key,
                "models": [name],
                "numbers": {
                    "New high": _fmt_money(today),
                    "Prior peak": _fmt_money(prior_max),
                    "Cumulative return": _fmt_pct(cum),
                },
            },
            "dedup_key": f"ath:{key}",
        })
    return specs


def detect_oversized_trades(settings: dict[str, Any], date_str: str) -> list[dict[str, Any]]:
    """Any single executed order whose notional exceeds oversized_trade_pct of
    the portfolio value at the time of the trade."""
    threshold = float((settings.get("alerts", {}) or {}).get("oversized_trade_pct", 0.15))
    specs: list[dict[str, Any]] = []
    for key in _enabled_model_keys(settings):
        name = _display_name(settings, key)
        for rec in _read_today_trade_records(key, date_str):
            pv = float((rec.get("portfolio_after") or {}).get("total_value") or 0.0)
            if pv <= 0:
                continue
            for ex in rec.get("executions", []):
                if not ex.get("executed") or ex.get("side") not in ("BUY", "SELL"):
                    continue
                notional = float(ex.get("notional") or 0.0)
                frac = notional / pv
                if frac <= threshold:
                    continue
                ticker = ex.get("ticker", "?")
                ts = ex.get("timestamp", rec.get("timestamp", ""))
                specs.append({
                    "kind": "oversized_trade",
                    "severity": "WARN",
                    "title": f"{name}: {frac*100:.1f}% order in {ticker}",
                    "body": f"{name} executed a single {ex.get('side')} of <b>{ticker}</b> worth "
                            f"{_fmt_money(notional)} — <b>{frac*100:.1f}%</b> of its "
                            f"{_fmt_money(pv)} portfolio, above the {threshold*100:.0f}% single-order line.",
                    "context": {
                        "model": key,
                        "models": [name],
                        "numbers": {
                            "Side / ticker": f"{ex.get('side')} {ticker}",
                            "Order notional": _fmt_money(notional),
                            "% of portfolio": f"{frac*100:.1f}%",
                            "Fill price": _fmt_money(float(ex.get('fill_price') or 0.0)),
                        },
                    },
                    "dedup_key": f"oversized:{key}:{ticker}:{ts}",
                })
    return specs


def detect_news_impact(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """A headline with |sentiment| above the threshold affecting a stock held
    by news_min_holders+ models."""
    from ..data.sentiment import score_headline, sentiment_label

    alerts_cfg = settings.get("alerts", {}) or {}
    sent_threshold = float(alerts_cfg.get("news_sentiment_threshold", 0.7))
    min_holders = int(alerts_cfg.get("news_min_holders", 4))

    cache = _read_news_cache()
    stocks = (cache.get("stocks") or {}) if cache else {}
    if not stocks:
        return []

    # Holder count per ticker, plus display names of holders.
    from ..portfolio import load_portfolio
    holders_by_ticker: dict[str, list[str]] = {}
    for key in _enabled_model_keys(settings):
        try:
            p = load_portfolio(key)
        except Exception:
            continue
        for ticker in p.holdings:
            holders_by_ticker.setdefault(ticker, []).append(_display_name(settings, key))

    specs: list[dict[str, Any]] = []
    for ticker, items in stocks.items():
        holders = holders_by_ticker.get(ticker, [])
        if len(holders) < min_holders:
            continue
        # Pick the single highest-magnitude headline for this ticker.
        best_score = 0.0
        best_item: dict[str, Any] | None = None
        for it in items or []:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            s = score_headline(title)
            if abs(s) > abs(best_score):
                best_score = s
                best_item = it
        if best_item is None or abs(best_score) <= sent_threshold:
            continue
        title = best_item.get("title", "")
        src = best_item.get("source", "?")
        label = sentiment_label(best_score)
        specs.append({
            "kind": "news_impact",
            "severity": "WARN",
            "title": f"{label.title()} news on {ticker} (held by {len(holders)})",
            "body": f"A <b>{label}</b> headline (sentiment {best_score:+.2f}) is affecting "
                    f"<b>{ticker}</b>, held by {len(holders)} models "
                    f"({', '.join(sorted(holders))}):<br><i>&ldquo;{title}&rdquo;</i> — {src}.",
            "context": {
                "models": sorted(holders),
                "numbers": {
                    "Ticker": ticker,
                    "Sentiment": f"{best_score:+.2f} ({label})",
                    "Models holding": f"{len(holders)}",
                    "Source": src,
                },
            },
            "dedup_key": f"news:{ticker}:{hashlib.sha1(title.encode('utf-8')).hexdigest()[:10]}",
        })
    return specs


def detect_macro_market_events(
    settings: dict[str, Any],
    news_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Trigger #11 — genuinely market-wide events (war, an emergency rate
    decision, a crash/circuit-breaker, a pandemic, a recession call, …).

    Distinct from trigger #9 (detect_news_impact), which keys off a single
    stock held by 4+ models. This one ignores who holds what: a market-wide
    shock matters regardless of the current book.

    Gate: a macro headline must (a) match a high-severity keyword in one of
    the five MACRO_EVENT_PATTERNS categories AND (b) carry a sentiment
    magnitude above `alerts.macro_event_sentiment_threshold` (default 0.6),
    so routine business news ("S&P slips on light volume") never fires.

    De-dup is per category per ET day (the dispatch dedup_key), so the same
    event reported by a dozen outlets — or re-seen across the day's 13 ticks —
    fires exactly once. The single highest-magnitude headline in each matched
    category becomes that category's alert.
    """
    from ..data.sentiment import score_macro_headline, sentiment_label

    alerts_cfg = settings.get("alerts", {}) or {}
    threshold = float(alerts_cfg.get("macro_event_sentiment_threshold", 0.6))

    headlines = _collect_macro_headlines(news_data)
    if not headlines:
        return []

    # category -> (signed_score, item, title) for the strongest match so far.
    best_by_cat: dict[str, tuple[float, dict[str, Any], str]] = {}
    for it in headlines:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        category = _match_macro_category(f"{title} {it.get('summary') or ''}")
        if not category:
            continue
        # Crisis-augmented VADER (see score_macro_headline) — the plain lexicon
        # scores "crash"/"recession"/"default" near zero, so the gate would be
        # dead. Scored on title + summary for a stabler magnitude on terse heads.
        score = score_macro_headline(f"{title}. {it.get('summary') or ''}")
        if abs(score) <= threshold:
            continue
        prev = best_by_cat.get(category)
        if prev is None or abs(score) > abs(prev[0]):
            best_by_cat[category] = (score, it, title)

    if not best_by_cat:
        return []

    pos = _models_positioning(settings)
    n_models = pos["n_models"]
    cash_pct = pos["total_cash_pct"]
    cash_str = f"{cash_pct * 100:.1f}%" if cash_pct is not None else "—"
    most_held = pos["most_held"]
    most_held_str = (
        ", ".join(f"{t} ({c}/{n_models})" for t, c in most_held) if most_held else "none"
    )

    specs: list[dict[str, Any]] = []
    for category, (score, it, title) in best_by_cat.items():
        src = it.get("source", "?")
        ts = it.get("datetime") or "—"
        label = sentiment_label(score)
        specs.append({
            "kind": "macro_event",
            "severity": "CRITICAL",
            # Title is fixed so the subject is exactly the spec'd string
            # ("[ALERT] Major Market Event Detected"); the matched category
            # is carried in the body + the numbers table below.
            "title": "Major Market Event Detected",
            "body": (
                f"<b>{category}.</b> A macro headline matched a high-severity "
                f"market-wide signal (sentiment <b>{score:+.2f}</b>, {label}):<br>"
                f"<i>&ldquo;{title}&rdquo;</i><br>"
                f"&mdash; {src}, {ts}.<br><br>"
                f"This is a market-wide event and may affect <b>all model portfolios</b>, "
                f"not just a single stock.<br><br>"
                f"Positioning right now across {n_models} models: <b>{cash_str}</b> total cash; "
                f"most-held — <b>{most_held_str}</b>."
            ),
            "context": {
                "models": ["All models"],
                "numbers": {
                    "Category": category,
                    "Sentiment": f"{score:+.2f} ({label})",
                    "Source": src,
                    "Headline time": ts,
                    "Total cash across models": cash_str,
                    "Most-held": most_held_str,
                },
            },
            "dedup_key": f"macro_event:{category}",
        })
    return specs


def scan_macro_events(
    news_data: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Run the market-wide event detector and dispatch immediately.

    This is the "fire as it happens" path the pipeline calls every tick, right
    after the news fetch — so a war or a crash alerts within the tick it
    surfaces rather than waiting for the EOD sweep. Dedup (once per category
    per ET day) makes it safe to call on all 13 daily ticks. Returns a small
    disposition tally for logging. Never raises.
    """
    if settings is None:
        settings = load_settings()
    tally: dict[str, int] = {}
    try:
        specs = detect_macro_market_events(settings, news_data=news_data)
    except Exception:
        logger.exception("Macro market-event detection failed")
        return tally
    for spec in specs:
        try:
            disp = dispatch_event(
                kind=spec["kind"],
                severity=spec["severity"],
                title=spec["title"],
                body=spec["body"],
                context=spec.get("context"),
                dedup_key=spec.get("dedup_key"),
                settings=settings,
            )
        except Exception:
            logger.exception("Macro market-event dispatch failed")
            continue
        tally[disp] = tally.get(disp, 0) + 1
    if tally:
        logger.info("Macro market-event scan dispositions: %s", tally)
    return tally


def detect_state_anomalies(settings: dict[str, Any], date_str: str | None = None) -> list[dict[str, Any]]:
    """Data-integrity sweep: ghost positions, position-cap violations,
    duplicate state across models, negative cash, and duplicate perf rows."""
    if date_str is None:
        date_str = session_date()
    from ..portfolio import load_portfolio
    from ..portfolio.portfolio import Portfolio

    rules = settings.get("portfolio_rules", {}) or {}
    max_positions = int(rules.get("max_positions", 50))
    max_pos_pct = float(rules.get("max_position_pct", 0.20))
    epsilon = float(getattr(Portfolio, "GHOST_SHARES_EPSILON", 0.01))

    specs: list[dict[str, Any]] = []
    keys = _enabled_model_keys(settings)

    portfolios: dict[str, Any] = {}
    signatures: dict[str, list[str]] = {}  # signature -> [model_keys]
    for key in keys:
        try:
            p = load_portfolio(key)
        except Exception:
            continue
        portfolios[key] = p
        name = _display_name(settings, key)

        # Ghost positions
        ghosts = [t for t, h in p.holdings.items() if h.shares < epsilon]
        if ghosts:
            specs.append(_state_spec(
                "state_ghost", "WARN", key, name,
                f"{name} is carrying ghost position(s) under {epsilon} shares: "
                f"<b>{', '.join(ghosts)}</b>. These should have been swept after a fractional sell.",
                {"Ghost tickers": ", ".join(ghosts)},
            ))

        # Position count cap
        if len(p.holdings) > max_positions:
            specs.append(_state_spec(
                "state_poscount", "WARN", key, name,
                f"{name} holds <b>{len(p.holdings)}</b> positions, over the "
                f"{max_positions}-position cap.",
                {"Positions": str(len(p.holdings)), "Cap": str(max_positions)},
            ))

        # Negative cash / non-positive value (data integrity)
        if p.cash < -1e-6:
            specs.append(_state_spec(
                "state_cash", "CRITICAL", key, name,
                f"{name} has <b>negative cash</b> of {_fmt_money(p.cash)} — a balance-integrity failure.",
                {"Cash": _fmt_money(p.cash)},
            ))

        # Duplicate-state signature (non-trivial: must hold something)
        if p.holdings:
            sig_parts = sorted(f"{t}:{round(h.shares, 4)}" for t, h in p.holdings.items())
            sig = f"{round(p.cash, 2)}|" + ",".join(sig_parts)
            signatures.setdefault(sig, []).append(key)

    # Weight-cap violations need mark-to-market weights — read from dashboard.json.
    # The 20% cap is enforced on *target* weight at trade time; a position is
    # allowed to drift above it on price appreciation. So we only flag a
    # genuine anomaly: a position at 2x the cap, which the enforcement path
    # should make impossible and almost always means an integrity bug.
    weight_anomaly_pct = max_pos_pct * 2.0
    for snap in _read_dashboard_portfolios():
        key = snap.get("model_key")
        if key not in keys:
            continue
        name = _display_name(settings, key)
        over = [
            (h.get("ticker", "?"), float(h.get("weight") or 0.0))
            for h in snap.get("holdings", [])
            if float(h.get("weight") or 0.0) > weight_anomaly_pct
        ]
        if over:
            worst = max(over, key=lambda x: x[1])
            specs.append(_state_spec(
                "state_weightcap", "WARN", key, name,
                f"{name} has a position at over 2x the {max_pos_pct*100:.0f}% cap "
                f"(enforcement should make this impossible): "
                + ", ".join(f"<b>{t}</b> at {w*100:.1f}%" for t, w in over) + ".",
                {"Worst": f"{worst[0]} {worst[1]*100:.1f}%", "Cap": f"{max_pos_pct*100:.0f}%"},
            ))

    # Duplicate state across models
    for sig, models in signatures.items():
        if len(models) >= 2:
            names = [_display_name(settings, m) for m in models]
            specs.append({
                "kind": "state_anomaly",
                "severity": "CRITICAL",
                "title": f"Duplicate portfolio state: {', '.join(names)}",
                "body": f"Models <b>{', '.join(names)}</b> have byte-identical holdings and cash — "
                        f"a strong sign of cross-model state contamination (a mis-seeded state file).",
                "context": {"models": names, "numbers": {"Models": ", ".join(names)}},
                "dedup_key": f"state_dup:{':'.join(sorted(models))}",
            })

    # Duplicate perf-log rows for TODAY only. One row per date is the
    # invariant log_daily_snapshot enforces; a duplicate for the current
    # session means that idempotency guard just failed (a live bug worth an
    # alert). Legacy duplicate rows from early build/test runs are known and
    # not actionable, so we deliberately don't re-litigate them here.
    for key in keys:
        df = _perf_history(key)
        if df.empty or "date" not in df.columns:
            continue
        dates = df["date"].astype(str).str[:10].tolist()
        if dates.count(date_str) > 1:
            name = _display_name(settings, key)
            specs.append(_state_spec(
                "state_perfdup", "CRITICAL", key, name,
                f"{name}'s performance log has <b>{dates.count(date_str)} rows for {date_str}</b> — "
                f"the once-per-day EOD idempotency guard appears to have failed today.",
                {"Date": date_str, "Rows": str(dates.count(date_str))},
            ))

    return specs


def _state_spec(subkind: str, severity: str, key: str, name: str,
                body: str, numbers: dict[str, str]) -> dict[str, Any]:
    return {
        "kind": "state_anomaly",
        "severity": severity,
        "title": f"State anomaly ({subkind.replace('state_', '')}): {name}",
        "body": body,
        "context": {"model": key, "models": [name], "numbers": numbers},
        "dedup_key": f"{subkind}:{key}",
    }


def detect_missed_runs(settings: dict[str, Any], date_str: str) -> list[dict[str, Any]]:
    """Detect trading days with no EOD performance row between the previous
    logged day and today. Uses the model with the longest history so a
    mid-experiment model addition can't false-positive its early gap.
    """
    keys = _enabled_model_keys(settings)
    longest = None
    best_len = 0
    for key in keys:
        df = _eod_history(key)
        if not df.empty and len(df) > best_len:
            best_len = len(df)
            longest = df
    if longest is None or "date" not in longest.columns or len(longest) < 2:
        return []

    dates = sorted({str(d)[:10] for d in longest["date"].astype(str)})
    if date_str not in dates:
        # Today's row not written yet (shouldn't happen at EOD) — bail rather
        # than guess.
        return []
    idx = dates.index(date_str)
    if idx == 0:
        return []
    prev_date = dates[idx - 1]

    missing = _trading_days_between(prev_date, date_str)
    if not missing:
        return []
    return [{
        "kind": "missed_run",
        "severity": "CRITICAL",
        "title": f"Missed scheduled run(s): {len(missing)} day(s)",
        "body": f"No end-of-day data was recorded for {len(missing)} trading day(s) between "
                f"{prev_date} and {date_str}: <b>{', '.join(missing)}</b>. "
                f"The pipeline likely failed to run on those sessions.",
        "context": {"numbers": {"Missing days": ", ".join(missing),
                                "Last good day": prev_date}},
        "dedup_key": f"missed_run:{prev_date}:{date_str}",
    }]


def _trading_days_between(start_exclusive: str, end_exclusive: str) -> list[str]:
    """NYSE trading days strictly between two YYYY-MM-DD dates."""
    try:
        import pandas_market_calendars as mcal
        nyse = mcal.get_calendar("NYSE")
        sched = nyse.schedule(start_date=start_exclusive, end_date=end_exclusive)
        days = [d.strftime("%Y-%m-%d") for d in sched.index]
    except Exception:
        # Fallback: business days (Mon–Fri), holiday-blind.
        import pandas as pd
        days = [d.strftime("%Y-%m-%d")
                for d in pd.bdate_range(start=start_exclusive, end=end_exclusive)]
    return [d for d in days if start_exclusive < d < end_exclusive]


# ===========================================================================
# EOD SWEEP
# ===========================================================================

def run_eod_alert_sweep(summary: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, int]:
    """Run every EOD detector in priority order and dispatch the results.

    Returns a small disposition tally for logging. Each detector is isolated
    in try/except so one failure can't sink the rest of the sweep or the
    digest that follows.
    """
    if settings is None:
        settings = load_settings()
    date_str = summary.get("date") or session_date()

    # Critical → informational, so the per-day cap is spent on the events that
    # matter most; lower-priority detections overflow into the digest. The
    # macro-event detector runs first (and again here as an EOD backstop —
    # the intraday scan_macro_events path is the primary trigger; dedup means
    # this re-run is a no-op if it already fired today).
    detectors: list[tuple[str, Any]] = [
        ("macro_market_events", lambda: detect_macro_market_events(settings)),
        ("state_anomalies", lambda: detect_state_anomalies(settings, date_str)),
        ("missed_runs", lambda: detect_missed_runs(settings, date_str)),
        ("oversized_trades", lambda: detect_oversized_trades(settings, date_str)),
        ("negative_crossings", lambda: detect_negative_crossings(settings)),
        ("new_ath", lambda: detect_new_ath(settings)),
        ("milestones", lambda: detect_milestones(settings)),
        ("news_impact", lambda: detect_news_impact(settings)),
    ]

    tally: dict[str, int] = {}
    for det_name, fn in detectors:
        try:
            specs = fn() or []
        except Exception:
            logger.exception("Detector %s failed — skipping", det_name)
            continue
        for spec in specs:
            try:
                disp = dispatch_event(
                    kind=spec["kind"],
                    severity=spec["severity"],
                    title=spec["title"],
                    body=spec["body"],
                    context=spec.get("context"),
                    dedup_key=spec.get("dedup_key"),
                    email=spec.get("email"),
                    mark_milestones=spec.get("mark_milestones"),
                    settings=settings,
                )
            except Exception:
                logger.exception("Dispatch failed for %s event", det_name)
                continue
            tally[disp] = tally.get(disp, 0) + 1

    if tally:
        logger.info("EOD alert sweep dispositions: %s", tally)
    return tally
