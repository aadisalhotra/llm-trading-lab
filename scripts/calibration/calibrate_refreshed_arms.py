"""Calibration of the five refreshed arms on the common input set.

Governing texts (docs/ops/research_rulings/ and docs/registrations/):
  cohort_refresh_ruling.md rev. 2 section 5; reasoning_allowance_limit_ruling;
  tick_deadline_and_calibration_rulings section 1 (D, the timeout cap) and
  section 2 (the set); streaming_and_send_omit_ruling section 2 (the 39-input
  subset, the paired test); send_omit_measure_and_finish_reason_ruling
  (measure = total output; allowance floor 4,096; finish classes);
  calibration_contingencies_ruling section 4; calibration_bound_amendment
  (bound = max(900 s, D + 120 s)). Hub H-OPS-00, -04, -05, -10.

What it does
  1. Loads the common set: every retained trading-call input of the five
     Phase A snapshots (claude, claude_opus, gemini, gpt, grok) at every tick of
     the calibration sessions, each with its screening call, from
     data/prompts/{YYYY-MM}/{cycle_id}.jsonl and its images.
  2. Runs every refreshed arm once on each input, both legs (screening and
     trading), with the level OMITTED, the cap at the provider maximum, and the
     call bound = max(900, D + 120) s, through the production adapters (one
     call per input: no parse retry; the finish-reason rule classifies first).
  3. Runs each arm once more on the 39-input subset, trading leg, with the
     level SENT.
  4. Writes the per-call table per arm (with its sha256) and a summary: valid
     calls, positions covered, R, allowance and ladder rung, timeout, and the
     send/omit test.
Calibration calls are induced tests: off-cycle, no orders, no series.

Cost (hub H-OPS-24): every per-call record carries cost_usd, the adapter's own
call cost when it reports one, else the list rate in force on the call's UTC
date applied to input_tokens and total_output (reasoning bills as output on all
five arms); None when neither is known. The summary totals it per arm over
every record written, a superseded provider failure included, since each was
billed. Calibration cost lives only in these tables: nothing that computes a
cost-per-decision figure reads them.

Classes per call: valid (normal finish, inside the bound); bound_reached (cut at
the bound: data, not retried, timeout -> D, excluded from R); provider_failure
(API_ERROR / ACCOUNT_STATE: re-run once after the batch); invalid_finish
(REFUSAL, ABNORMAL_FINISH, STREAM_INCOMPLETE: excluded from R and the latency
maximum, its send/omit pair dropped, reported with the raw reason);
budget_stop (DECISION_TRUNCATED at the provider maximum: R is unknown for the
arm and the ladder moves on, reasoning_allowance_limit_ruling section 5.1).

Usage
  python scripts/calibration/calibrate_refreshed_arms.py run \
      --sessions 2026-11-23 2026-11-24 2026-11-25 --out-dir docs/ops/calibration_2026-11-25
  python scripts/calibration/calibrate_refreshed_arms.py summarize --out-dir ...
  (--arms, --limit, --concurrency, --inputs-dir, --label; resumable: calls
   already in an arm's table are skipped)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
SNAPSHOTS = ("claude", "claude_opus", "gemini", "gpt", "grok")   # the subset rule's order
ANSWER_BUDGET = 16384
TICKS_PER_SESSION = 13

# Per arm, stated before its runs (send_omit_measure_and_finish_reason_ruling
# section 1 item 1): the provider limit L for the request shape in use, whether
# reasoning shares the output cap, and the token fields of the measure.
ARMS: dict[str, dict[str, Any]] = {
    "cb_sonnet": {"provider": "anthropic", "model": "claude-sonnet-5-5", "L": 128000,
                  "shares_cap": True, "output_includes_reasoning": True,
                  "fields": "total = usage.output_tokens (includes thinking); reasoning = "
                            "output_tokens_details.thinking_tokens (message_delta); visible = "
                            "total - reasoning. Streamed."},
    "cb_opus": {"provider": "anthropic", "model": "claude-opus-5-5", "L": 128000,
                "shares_cap": True, "output_includes_reasoning": True,
                "fields": "as cb_sonnet. Streamed."},
    "cb_astra": {"provider": "openai", "model": "gpt-6-astra", "L": 128000,
                 "shares_cap": True, "output_includes_reasoning": True,
                 "fields": "total = usage.completion_tokens (includes reasoning); reasoning = "
                           "completion_tokens_details.reasoning_tokens; visible = total - "
                           "reasoning. Non-streaming."},
    "cb_grok": {"provider": "xai", "model": "grok-4.7", "L": None,
                "shares_cap": False, "output_includes_reasoning": False,
                "fields": "total = usage.completion_tokens + completion_tokens_details."
                          "reasoning_tokens (reasoning reported outside completion_tokens); "
                          "visible = completion_tokens. Reasoning does not count against "
                          "max_tokens (16,384, the answer budget). Non-streaming."},
    "cb_argon": {"provider": "google", "model": "gemini-4-argon", "L": 32768,
                 "shares_cap": True, "output_includes_reasoning": False,
                 "fields": "total = usage_metadata.candidates_token_count + "
                           "thoughts_token_count; reasoning = thoughts_token_count; visible = "
                           "candidates_token_count. Non-streaming (limit 32,768; 65,536 per "
                           "streaming segment)."},
}

BOUND_SLACK_S = 5.0     # a call cut at the bound ends within this of it


def _redact(s: str) -> str:
    """No key text in a written error: every configured key value is masked."""
    import os
    for k, v in os.environ.items():
        if v and len(v) >= 12 and k.endswith(("_API_KEY", "_SECRET", "_TOKEN", "_KEY")):
            s = s.replace(v, "[REDACTED]")
    return s


VALID, BOUND, PROVIDER, INVALID, BUDGET = ("valid", "bound_reached", "provider_failure",
                                          "invalid_finish", "budget_stop")


# ----------------------------------------------------------------- the set
def tick_position(cycle_id: str) -> int | None:
    from src.logging.call_inputs import tick_slot
    t = datetime.strptime(cycle_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
    return tick_slot(t)


def load_common_set(prompts_dir: Path, sessions: list[str]) -> list[dict[str, Any]]:
    """Every snapshot's retained trading input (with its screening input) at
    every tick of the sessions, in time order, with tick index and position."""
    days = {s.replace("-", "") for s in sessions}
    files = sorted(p for p in prompts_dir.glob("*/*.jsonl") if p.stem[:8] in days)
    inputs: list[dict[str, Any]] = []
    ticks = []
    for f in files:
        cycle = f.stem
        lines = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        by_model: dict[str, dict[str, Any]] = {}
        for ln in lines:
            if ln["model_key"] in SNAPSHOTS:
                by_model.setdefault(ln["model_key"], {})[ln["stage"]] = ln
        if not by_model:
            continue
        ticks.append(cycle)
        for mk, legs in by_model.items():
            if "trade" not in legs:
                continue
            inputs.append({"input_id": f"{cycle}:{mk}", "cycle_id": cycle, "model_key": mk,
                           "position": tick_position(cycle), "trade": legs["trade"],
                           "screen": legs.get("screen"), "images_dir": f.parent / "images"})
    index = {c: i for i, c in enumerate(sorted(ticks))}
    for x in inputs:
        x["tick_index"] = index[x["cycle_id"]]
    return sorted(inputs, key=lambda x: (x["tick_index"], SNAPSHOTS.index(x["model_key"])))


def send_subset(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The 39-input subset: for tick k, the snapshot at k mod 5 in SNAPSHOTS
    order, or the next one present."""
    by_tick: dict[int, dict[str, dict[str, Any]]] = {}
    for x in inputs:
        by_tick.setdefault(x["tick_index"], {})[x["model_key"]] = x
    out = []
    for k in sorted(by_tick):
        for j in range(len(SNAPSHOTS)):
            mk = SNAPSHOTS[(k + j) % len(SNAPSHOTS)]
            if mk in by_tick[k]:
                out.append(by_tick[k][mk])
                break
    return out


def job_order(inputs: list[dict[str, Any]], subset: list[dict[str, Any]],
              done: set[tuple]) -> list[tuple[str, str, dict[str, Any]]]:
    """The calls still to run, in run order (one call at a time per arm).

    calibration_increments_ruling_2026-11-23 section 1: each send call runs
    immediately before or after its omit partner in the same increment, send
    first when the tick number k is even, omit first when k is odd. A call
    already in the table is not run again, so a pair one of whose calls ran
    in an earlier increment (Monday night's, all run as recorded) completes
    on its own and its interval is reported, never re-run (section 1.3)."""
    in_subset = {x["input_id"] for x in subset}
    jobs: list[tuple[str, str, dict[str, Any]]] = []
    for x in inputs:
        legs = [("screen", "omit")] if x["screen"] else []
        if x["input_id"] in in_subset:
            legs += ([("trade", "send"), ("trade", "omit")] if x["tick_index"] % 2 == 0
                     else [("trade", "omit"), ("trade", "send")])
        else:
            legs.append(("trade", "omit"))
        jobs += [(leg, setting, x) for leg, setting in legs
                 if (x["input_id"], leg, setting) not in done]
    return jobs


def pair_intervals(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each send/omit pair's interval: seconds from the first call's start to
    the second's (section 1.3), from the calls that count."""
    def ts(r):
        return datetime.fromisoformat(r["started_utc"]).timestamp()
    by = {(r["input_id"], r["setting"]): r for r in recs if r["leg"] == "trade"}
    out = []
    for (iid, setting), s in sorted(by.items(), key=lambda kv: kv[1]["tick_index"]):
        o = by.get((iid, "omit"))
        if setting != "send" or o is None:
            continue
        out.append({"input_id": iid, "k": s["tick_index"],
                    "first": "send" if ts(s) <= ts(o) else "omit",
                    "interval_s": round(abs(ts(s) - ts(o)), 1)})
    return out


def load_images(x: dict[str, Any]) -> list[bytes]:
    imgs = []
    for im in x["trade"].get("images") or []:
        p = Path(x["images_dir"]) / f"{im['sha256']}.png"
        b = p.read_bytes()
        if hashlib.sha256(b).hexdigest() != im["sha256"]:
            raise ValueError(f"image bytes do not match their hash: {p}")
        imgs.append(b)
    return imgs


# ------------------------------------------------------------------- a call
def configured_d(value: float | None) -> float:
    """D from config/settings.json; calibration does not run without it."""
    if not value:
        raise SystemExit("tick_deadline.seconds is not configured: calibration needs D")
    return float(value)


TOOL_COMMIT: str | None = None      # set by run(); written on every call record


def tool_commit() -> dict[str, Any]:
    """The commit the tool and adapters run from (hub H-OPS-26), and whether
    the run path (scripts/calibration, src, config) differs from it."""
    import subprocess
    def git(*a):
        p = subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None
    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=no", "--",
                "scripts/calibration", "src", "config")
    return {"commit": head, "run_path_modified": bool(dirty) if dirty is not None else None}


def call_bound(d_seconds: float) -> float:
    """calibration_bound_amendment section 2."""
    return max(900.0, d_seconds + 120.0)



def run_call(arm: str, leg: str, setting: str, x: dict[str, Any], bound: float) -> dict[str, Any]:
    """One call through the production adapter: no parse retry, finish classified."""
    from src.adapters import base, get_adapter
    spec = ARMS[arm]
    a = get_adapter(spec["provider"], spec["model"])
    a.level_sent = setting == "send"
    stage = x["trade"] if leg == "trade" else x["screen"]
    images = load_images(x) if leg == "trade" else None
    a.deadline_at = time.monotonic() + bound
    t0 = time.monotonic()
    rec: dict[str, Any] = {"arm": arm, "model_configured": spec["model"], "tool_commit": TOOL_COMMIT,
                           "input_id": x["input_id"],
                           "tick_index": x["tick_index"], "position": x["position"], "leg": leg,
                           "setting": setting, "bound_s": bound,
                           "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        _text, returned, md = a._bounded_call(stage["system"], stage["user"], images)
        rec.update(cls=VALID, error_class=None, model_named=returned)
    except Exception as e:  # noqa: BLE001 -- every outcome is data
        md = getattr(e, "metadata", {}) or {}
        ec = base.finish_error_class(e) or (
            base.ERROR_CLASS_TIMEOUT if base.is_client_timeout(e)
            else base.ERROR_CLASS_ACCOUNT_STATE if base.is_account_state(spec["provider"], str(e))
            else base.ERROR_CLASS_API_ERROR)
        cls = {base.ERROR_CLASS_TICK_DEADLINE: BOUND, base.ERROR_CLASS_TIMEOUT: BOUND,
               base.ERROR_CLASS_DECISION_TRUNCATED: BUDGET,
               base.ERROR_CLASS_REFUSAL: INVALID, base.ERROR_CLASS_ABNORMAL_FINISH: INVALID,
               base.ERROR_CLASS_STREAM_INCOMPLETE: INVALID,
               base.ERROR_CLASS_IDENTITY_MISMATCH: INVALID}.get(ec, PROVIDER)
        # A "timeout" class is a bound only when the call ran to the bound.
        # urllib3's NewConnectionError subclasses ConnectTimeoutError, so an
        # unreachable network reads as a client timeout within seconds; that
        # is a provider failure (re-run), never data for R, the timeout or D.
        if cls == BOUND and time.monotonic() - t0 < bound - BOUND_SLACK_S:
            cls = PROVIDER
            rec["note"] = "timeout class before the bound (connection failure): re-run"
        rec.update(cls=cls, error_class=ec, error=_redact(str(e))[:300],
                   model_named=md.get("model_named") or md.get("model"))
    rec["latency_s"] = round(time.monotonic() - t0, 3)
    out, reasoning = md.get("output_tokens"), md.get("thoughts_tokens")
    if out is not None:
        r = int(reasoning or 0)
        total = int(out) if spec["output_includes_reasoning"] else int(out) + r
        rec.update(reasoning_tokens=reasoning, visible_tokens=total - r, total_output=total)
    else:
        rec.update(reasoning_tokens=reasoning, visible_tokens=None, total_output=None)
    rec.update(finish_reason=md.get("finish_reason"), finish_class=md.get("finish_class"),
               input_tokens=md.get("input_tokens"))
    rec.update(call_cost(spec["model"], md, rec.get("total_output"), rec["started_utc"][:10]))
    rec["provider_cost_usd"] = md.get("provider_cost_usd")    # xAI bills it back; else None
    return rec


def call_cost(model: str, md: dict[str, Any], total_output: int | None,
              on_date: str) -> dict[str, Any]:
    """The call's cost (hub H-OPS-24): the adapter's figure, else list rates."""
    if md.get("cost_usd") is not None:
        return {"cost_usd": round(float(md["cost_usd"]), 6), "cost_source": "adapter"}
    if md.get("input_tokens") is not None and total_output is not None:
        from src.analytics.cost_rates import compute_call_cost_usd
        c = compute_call_cost_usd(model, int(md["input_tokens"]), int(total_output), on_date=on_date)
        if c is not None:
            return {"cost_usd": round(c, 6), "cost_source": "list_rate"}
    return {"cost_usd": None, "cost_source": None}


def cost_totals(recs: list[dict[str, Any]]) -> dict[str, Any]:
    """Calibration spend over every record written (each one was billed)."""
    known = [r["cost_usd"] for r in recs if r.get("cost_usd") is not None]
    return {"cost_usd": round(sum(known), 4), "records_priced": len(known),
            "records_unpriced": len(recs) - len(known),
            "note": "calibration spend; never part of a cost-per-decision figure"}


# --------------------------------------------------------------- the rules
def allowance(R: int) -> int:
    """2R rounded up to the next 4,096, never below 4,096."""
    return max(4096, int(math.ceil(2 * R / 4096.0)) * 4096)


def ladder(spec: dict[str, Any], R: int | None) -> dict[str, Any]:
    """reasoning_allowance_limit_ruling section 2, with the 4,096 floor."""
    if not spec["shares_cap"]:
        return {"rung": None, "note": "reasoning does not share the output cap: no allowance; "
                                      "max_tokens stays the answer budget",
                "cap": ANSWER_BUDGET}
    L = spec["L"]
    H = L - ANSWER_BUDGET
    if R is None:
        return {"rung": 3, "note": "R unknown (a calibration call ended on the limit): rungs 1 and "
                                   "2 fail by construction (section 5.1)", "L": L, "H": H}
    al = allowance(R)
    if al <= H:
        return {"rung": 1, "R": R, "allowance": al, "cap": ANSWER_BUDGET + al, "L": L, "H": H}
    if H >= 1.4 * R:
        return {"rung": 2, "R": R, "allowance": H, "cap": L, "L": L, "H": H,
                "reduced_margin_H_over_R": round(H / R, 3)}
    return {"rung": 3, "R": R, "L": L, "H": H, "note": "rungs 1 and 2 fail: a higher-limit "
                                                       "request shape, or rung 4 by 2026-12-07"}


def derived_timeout(latencies: list[float], any_bound: bool, d_seconds: float) -> float:
    """api_call_timeout.md section 1 as amended: max valid latency x 1.4,
    rounded up to 30 s, not below 300, capped at D; D when a call reached the bound."""
    if any_bound:
        return d_seconds
    if not latencies:
        return d_seconds
    t = math.ceil(max(latencies) * 1.4 / 30.0) * 30.0
    return min(max(t, 300.0), d_seconds)


# ---- Student t quantile without scipy (regularized incomplete beta + bisection)
def _betacf(a: float, b: float, x: float) -> float:
    m_max, eps, fpmin = 300, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, m_max + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    lo, hi = -100.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def send_omit_test(pairs: list[tuple[int, int]]) -> dict[str, Any]:
    """streaming_and_send_omit_ruling section 2.3 with the total-output measure:
    log(sent/omitted) per pair; equivalence if the 90% t interval for the mean
    lies within [log 0.80, log 1.25]; at least 30 valid pairs."""
    logs = [math.log(s / o) for s, o in pairs if s > 0 and o > 0]
    n = len(logs)
    out: dict[str, Any] = {"n_pairs": n, "margins": [0.80, 1.25]}
    if n < 30:
        out.update(shown=False, reason=f"fewer than 30 valid pairs ({n})")
        if n >= 2:
            m, sd = statistics.mean(logs), statistics.stdev(logs)
            half = t_ppf(0.95, n - 1) * sd / math.sqrt(n)
            out.update(ratio_mean=round(math.exp(m), 4),
                       ci90=[round(math.exp(m - half), 4), round(math.exp(m + half), 4)])
        return out
    m, sd = statistics.mean(logs), statistics.stdev(logs)
    half = t_ppf(0.95, n - 1) * sd / math.sqrt(n)
    lo, hi = m - half, m + half
    shown = lo > math.log(0.80) and hi < math.log(1.25)
    out.update(shown=shown, ratio_mean=round(math.exp(m), 4),
               ci90=[round(math.exp(lo), 4), round(math.exp(hi), 4)])
    return out


# ---------------------------------------------------------------- summary
def summarize_arm(arm: str, recs: list[dict[str, Any]], d_seconds: float) -> dict[str, Any]:
    spec = ARMS[arm]
    omit = [r for r in recs if r["setting"] == "omit"]
    valid = [r for r in omit if r["cls"] == VALID and r.get("model_named") == spec["model"]]
    valid_trade = [r for r in valid if r["leg"] == "trade"]
    positions = sorted({r["position"] for r in valid_trade if r["position"]})
    budget = [r for r in omit if r["cls"] == BUDGET]
    bound = [r for r in omit if r["cls"] == BOUND]
    reasoning = [int(r["reasoning_tokens"] or 0) for r in valid]
    R = None if budget else (max(reasoning) if reasoning else 0)
    s = {
        "arm": arm, "model": spec["model"], "fields": spec["fields"],
        "calls": len(recs), "omit_calls": len(omit),
        "valid_trading_calls": len(valid_trade), "positions_covered": positions,
        "coverage_met": len(valid_trade) >= 100 and len(positions) == TICKS_PER_SESSION,
        "counts_by_class": {c: sum(1 for r in omit if r["cls"] == c)
                            for c in (VALID, BOUND, PROVIDER, INVALID, BUDGET)},
        "model_name_mismatches": sum(1 for r in omit if r["cls"] == VALID
                                     and r.get("model_named") != spec["model"]),
        # Every model name the arm's responses carried, with counts: the
        # evidence for the hub's ruling on any build string to accept.
        "model_names_seen": {str(n): sum(1 for r in recs if r.get("model_named") == n)
                             for n in sorted({r.get("model_named") for r in recs}, key=str)},
        "invalid_finishes": [{"input_id": r["input_id"], "leg": r["leg"],
                              "error_class": r["error_class"], "finish_reason": r["finish_reason"]}
                             for r in omit if r["cls"] == INVALID],
        "R": R, "reasoning_median": statistics.median(reasoning) if reasoning else None,
        "zero_reasoning_calls": sum(1 for v in reasoning if v == 0),
        "ladder": ladder(spec, R),
        "timeout_s": derived_timeout([r["latency_s"] for r in valid], bool(bound), d_seconds),
        "max_valid_latency_s": max((r["latency_s"] for r in valid), default=None),
    }
    sent = {r["input_id"]: r for r in recs if r["setting"] == "send" and r["leg"] == "trade"}
    om = {r["input_id"]: r for r in omit if r["leg"] == "trade"}
    ok = lambda r: r and r["cls"] == VALID and r.get("model_named") == spec["model"]  # noqa: E731
    pairs, dropped = [], 0
    for iid, sr in sent.items():
        orr = om.get(iid)
        if ok(sr) and ok(orr) and sr["total_output"] and orr["total_output"]:
            pairs.append((sr["total_output"], orr["total_output"]))
        else:
            dropped += 1
    s["send_omit"] = {**send_omit_test(pairs), "pairs_dropped": dropped,
                      "send_reasoning_median": statistics.median(
                          [int(r["reasoning_tokens"] or 0) for r in sent.values() if ok(r)] or [0]),
                      "send_zero_reasoning": sum(1 for r in sent.values()
                                                 if ok(r) and not r["reasoning_tokens"])}
    return s


# --------------------------------------------------------------------- run
def table_path(out_dir: Path, arm: str) -> Path:
    return out_dir / f"{arm}_calls.jsonl"


def done_keys(p: Path, pair_ids: set[str] | None = None) -> set[tuple]:
    if not p.exists():
        return set()
    return done_keys_from([json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()
                           if ln.strip()], pair_ids)


def done_keys_from(recs, pair_ids: set[str] | None = None) -> set[tuple]:
    """Calls already in the table; a provider failure is re-run.

    calibration_outcomes_rulings_2026-11-26 (hub H-OPS-39 item 4): from now
    on a send/omit pair with a re-run call is re-run AS A PAIR. When the
    latest record of either trade call of a pair input (`pair_ids`) is a
    provider failure, both calls of the pair leave the done set, so
    job_order runs them back to back in the pair's order. The earlier valid
    partner stays in the table and is superseded by the re-run (latest_records).
    """
    last: dict[tuple, dict] = {}
    for r in recs:
        last[(r["input_id"], r["leg"], r["setting"])] = r
    done = {k for k, r in last.items() if r["cls"] != PROVIDER}
    for iid in pair_ids or ():
        pair = [(iid, "trade", "send"), (iid, "trade", "omit")]
        if any(k in last and last[k]["cls"] == PROVIDER for k in pair):
            done.difference_update(pair)
    return done


def latest_records(recs) -> dict[tuple, dict]:
    """The record that counts for each call: the last one that is not a
    provider failure, else the last failure. A pair re-run (H-OPS-39 item 4)
    therefore supersedes both earlier calls of the pair."""
    latest: dict[tuple, dict] = {}
    for r in recs:
        k = (r["input_id"], r["leg"], r["setting"])
        if k not in latest or r["cls"] != PROVIDER or latest[k]["cls"] == PROVIDER:
            latest[k] = r
    return latest


def run(args) -> int:
    from src.adapters import base
    from src.config_loader import load_env, load_settings, tick_deadline_seconds
    load_env()
    d = configured_d(tick_deadline_seconds(load_settings()))
    bound = call_bound(d)
    base.API_CALL_TIMEOUT_SECONDS = bound      # transports and the Claude stream use the bound
    inputs = load_common_set(Path(args.inputs_dir), args.sessions)
    if args.limit:
        inputs = inputs[:args.limit]
    subset = send_subset(inputs)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"label": args.label, "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sessions": args.sessions, "inputs": len(inputs), "send_subset": len(subset),
            "D_s": d, "bound_s": bound, "calls_in_flight_per_arm": args.concurrency,
            "arms": {a: ARMS[a] for a in args.arms}}
    global TOOL_COMMIT
    tc = tool_commit()
    TOOL_COMMIT = tc["commit"] + ("+modified" if tc["run_path_modified"] else "") if tc["commit"] else None
    meta["tool_commit"] = tc
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    # Every run (one per night's increment, plus re-runs) is kept in order.
    with open(out / "run_log.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({k: v for k, v in meta.items() if k != "arms"}) + "\n")
    print(json.dumps({k: v for k, v in meta.items() if k != "arms"}))

    def run_arm(arm: str) -> str:
        p = table_path(out, arm)
        done = done_keys(p, {x["input_id"] for x in subset})
        jobs = job_order(inputs, subset, done)
        lock = threading.Lock()
        with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool, \
                open(p, "a", encoding="utf-8", newline="\n") as f:
            futs = [pool.submit(run_call, arm, leg, setting, x, bound) for leg, setting, x in jobs]
            for fut in cf.as_completed(futs):
                rec = fut.result()
                with lock:
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
        return f"{arm}: {len(jobs)} calls written to {p.name}"

    # The arms run side by side, each with `concurrency` calls in flight
    # (default 1). Latency feeds the timeout, and one call at a time per arm
    # is the load production puts on each arm: under six parallel calls
    # Argon's latency rose from about 15 s to about 75 s (dry run 2026-11-22).
    with cf.ThreadPoolExecutor(max_workers=len(args.arms)) as arms_pool:
        for line in arms_pool.map(run_arm, args.arms):
            print(line)
    return summarize(args)


def summarize(args) -> int:
    from src.config_loader import load_settings, tick_deadline_seconds
    d = configured_d(tick_deadline_seconds(load_settings()))
    out = Path(args.out_dir)
    summary = {"label": args.label, "D_s": d, "bound_s": call_bound(d), "arms": {}}
    log = out / "run_log.jsonl"
    summary["runs"] = ([json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
                       if log.exists() else [])
    summary["summarized_by"] = tool_commit()
    for arm in args.arms:
        p = table_path(out, arm)
        if not p.exists():
            continue
        recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        # A provider failure re-run later supersedes the failed record; a pair
        # re-run supersedes both calls of the pair (latest_records).
        latest = latest_records(recs)
        s = summarize_arm(arm, list(latest.values()), d)
        s["table"] = {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                      "records": len(recs)}
        s["calibration_cost"] = cost_totals(recs)
        s["send_omit"]["pair_intervals"] = pair_intervals(list(latest.values()))
        summary["arms"][arm] = s
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({a: {k: s[k] for k in ("valid_trading_calls", "coverage_met", "R", "ladder",
                                           "timeout_s", "counts_by_class")}
                      | {"send_omit": {k: s["send_omit"].get(k) for k in ("n_pairs", "shown")}}
                      for a, s in summary["arms"].items()}, indent=1, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "summarize"):
        sp = sub.add_parser(name)
        sp.add_argument("--out-dir", required=True)
        sp.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
        sp.add_argument("--label", default="calibration")
        if name == "run":
            sp.add_argument("--sessions", nargs="+", required=True)
            sp.add_argument("--inputs-dir", default=str(ROOT / "data" / "prompts"))
            sp.add_argument("--limit", type=int, default=0)
            sp.add_argument("--concurrency", type=int, default=1,
                            help="calls in flight per arm (arms run side by side); 1 = production load")
    a = ap.parse_args()
    return run(a) if a.cmd == "run" else summarize(a)


if __name__ == "__main__":
    raise SystemExit(main())
