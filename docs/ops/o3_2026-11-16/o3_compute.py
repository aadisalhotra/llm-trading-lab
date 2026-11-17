"""O-3 (census #56/#57/#58) — read-only computation over the committed decision logs.

Run from the repo root:
    python <this file> > o3_results.json

Reads only data/trades/*.jsonl and reports/monthly/*/data_layer.json. Writes nothing
inside the repo. Every number in o3_report.md comes from this script's JSON output.

Estimator (named per CLAUDE.md rule 9): percentiles are numpy's default "linear"
method (Hyndman-Fan type 7) over per-record api_latency_seconds. No trimming,
no per-day aggregation: one value per decision record.
"""
from __future__ import annotations

import glob
import json
import logging
import sys
from collections import Counter
from pathlib import Path

import numpy as np

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("o3")

ROOT = Path.cwd()
TRADES = ROOT / "data" / "trades"
BOOKS = ["claude", "claude_opus", "deepseek", "gemini", "gpt", "grok"]


def load(book: str, months: list[str]) -> list[dict]:
    recs: list[dict] = []
    for m in months:
        p = TRADES / f"{book}_{m}.jsonl"
        if not p.exists():
            raise FileNotFoundError(p)
        with p.open(encoding="utf-8") as fh:
            recs += [json.loads(line) for line in fh if line.strip()]
    return recs


def pct(xs: list[float]) -> dict:
    a = np.asarray(xs, dtype=float)
    return {
        "n": int(a.size),
        "p50": round(float(np.percentile(a, 50)), 2),
        "p90": round(float(np.percentile(a, 90)), 2),
        "p99": round(float(np.percentile(a, 99)), 2),
        "max": round(float(a.max()), 2),
    }


def error_class(r: dict) -> str:
    """Classify a failed record from its stored error text (api_error_class only
    exists from 2026-11-15, so historical records are classified by text)."""
    e = (r.get("api_error") or "").lower()
    if " 429" in e or "resource-exhausted" in e:
        return "429_resource_exhausted"
    if "last exception: 503 recvmsg" in e:
        return "503_connection_reset"
    if "504" in e or "deadline" in e:
        return "504_deadline"
    if "503" in e or "high demand" in e or "currently experiencing" in e:
        return "503_high_demand"
    if "499" in e or "cancel" in e:
        return "499_cancelled"
    if " 402" in e or "insufficient balance" in e:
        return "402_insufficient_balance"
    if " 403" in e or "permission-denied" in e:
        return "403_permission_denied"
    if "server disconnected" in e:
        return "transport_disconnect"
    if "read timed out" in e or "timeout" in e:
        return "client_timeout"
    if "json" in e or "parse" in e or "decisions" in e:
        return "parse_failure"
    if not e:
        return "no_error_text"
    return "other"


# ---------------------------------------------------------------- #56 latency
def latency_table() -> dict:
    out: dict = {"window": "2026-08-01..2026-09-30", "population": "api_attempt == 1 AND api_success == true", "books": {}}
    for b in BOOKS:
        recs = load(b, ["2026-08", "2026-09"])
        keep = [r for r in recs if r.get("api_attempt") == 1 and r.get("api_success") is True]
        excl = Counter()
        for r in recs:
            if r in keep:
                continue
            if r.get("api_attempt") != 1:
                excl[f"attempt_{r.get('api_attempt')}"] += 1
            else:
                excl[f"attempt1_failed:{error_class(r)}"] += 1
        lat = [float(r["api_latency_seconds"]) for r in keep]
        by_month = {
            m: pct([float(r["api_latency_seconds"]) for r in keep if r["timestamp"][:7] == m])
            for m in ("2026-08", "2026-09")
        }
        out["books"][b] = {
            "records": len(recs),
            "model_ids": dict(Counter(r.get("model_id_configured") for r in recs)),
            "pooled": pct(lat),
            "by_month": by_month,
            "excluded": dict(excl),
            "excluded_latency_max": round(max((float(r["api_latency_seconds"]) for r in recs if r not in keep), default=0.0), 2),
        }
    return out


# ------------------------------------------------------------ #57 completeness
def completeness_check() -> dict:
    g = load("gemini", ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"])
    slow = [r for r in g if float(r.get("api_latency_seconds") or 0) >= 590]
    by_month = Counter(r["timestamp"][:7] for r in slow)
    usage_absent = [r for r in slow if not r.get("output_tokens") and not r.get("input_tokens")]
    usage_present = [r for r in slow if r not in usage_absent]
    classes = Counter(error_class(r) for r in slow)
    res: dict = {
        "slow_threshold_s": 590,
        "slow_total_apr_sep": len(slow),
        "slow_by_month": dict(sorted(by_month.items())),
        "slow_api_success_true": sum(1 for r in slow if r.get("api_success")),
        "slow_usage_absent": len(usage_absent),
        "slow_usage_present": [
            {"timestamp": r["timestamp"], "latency": round(float(r["api_latency_seconds"]), 1),
             "api_success": r.get("api_success"), "input_tokens": r.get("input_tokens"),
             "output_tokens": r.get("output_tokens"), "api_error": (r.get("api_error") or "")[:160]}
            for r in usage_present
        ],
        "slow_error_classes": dict(classes),
        "layers": {},
    }
    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        layer_path = ROOT / "reports" / "monthly" / month / "data_layer.json"
        month_recs = [r for r in g if r["timestamp"][:7] == month]
        month_slow = [r for r in slow if r["timestamp"][:7] == month]
        entry = {
            "decision_log_records": len(month_recs),
            "decision_log_api_success": sum(1 for r in month_recs if r.get("api_success")),
            "slow_records": len(month_slow),
            "slow_records_counted_as_failure": sum(1 for r in month_slow if not r.get("api_success")),
            "layer_exists": layer_path.exists(),
        }
        if layer_path.exists():
            layer = json.loads(layer_path.read_text(encoding="utf-8"))
            pm = layer["methodology_data_integrity_rq"]["data_integrity"]["per_model_failure_rate"]["gemini"]
            entry["layer_gemini"] = pm
            entry["layer_records_equal_log"] = pm["records"] == len(month_recs)
            entry["layer_success_equal_log"] = pm["api_success"] == entry["decision_log_api_success"]
            fails = len(month_recs) - entry["decision_log_api_success"]
            entry["slow_share_of_layer_failures"] = f"{len(month_slow)}/{fails}"
        res["layers"][month] = entry
    return res


# ------------------------------------------------------------- #58 thinking
def thinking() -> dict:
    g = load("gemini", ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"])
    pre = [r for r in g if r["timestamp"] < "2026-07-28"]
    pre_ok = [r for r in pre if r.get("api_success")]
    field_coverage = {
        m: {
            "records": sum(1 for r in g if r["timestamp"][:7] == m),
            "thoughts_tokens_key_present": sum(1 for r in g if r["timestamp"][:7] == m and "thoughts_tokens" in r),
            "thoughts_tokens_non_null": sum(1 for r in g if r["timestamp"][:7] == m and r.get("thoughts_tokens") is not None),
        }
        for m in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
    }
    first_thoughts = min((r["timestamp"] for r in g if r.get("thoughts_tokens") is not None), default=None)

    # Pre-fix observables that DO exist: visible output tokens on successes and failures.
    pre_fail = [r for r in pre if not r.get("api_success") and r.get("output_tokens")]
    pre_ok_out = [float(r["output_tokens"]) for r in pre_ok if r.get("output_tokens")]
    pre_fail_out = [float(r["output_tokens"]) for r in pre_fail]
    jul = [r for r in pre if r["timestamp"][:7] == "2026-07"]
    jul_fail_out = [float(r["output_tokens"]) for r in jul if not r.get("api_success") and r.get("output_tokens")]
    jul_ok_out = [float(r["output_tokens"]) for r in jul if r.get("api_success") and r.get("output_tokens")]
    # Candidate populations for the registered "median 3,923 thinking / 614 answer".
    ok_all = [r for r in g if r.get("api_success") and r.get("thoughts_tokens") is not None and r.get("output_tokens") is not None]
    windows = {
        "aug_sep_all_successes": ("2026-08-01", "2026-10-01"),
        "aug_only": ("2026-08-01", "2026-09-01"),
        "sep_only": ("2026-09-01", "2026-10-01"),
        "aug03_to_aug10": ("2026-08-03", "2026-08-11"),
        "to_2026_09_17": ("2026-08-01", "2026-09-18"),
    }
    candidates = {}
    for name, (lo, hi) in windows.items():
        rs = [r for r in ok_all if lo <= r["timestamp"] < hi]
        candidates[name] = {"n": len(rs),
                            "thinking_median": float(np.median([r["thoughts_tokens"] for r in rs])),
                            "answer_median": float(np.median([r["output_tokens"] for r in rs]))}

    # Post-fix (16384 cap, google-genai SDK reporting thoughts): attempt-1 successes, Aug-Sep.
    post = [r for r in g if "2026-08-01" <= r["timestamp"] < "2026-10-01"
            and r.get("api_attempt") == 1 and r.get("api_success")
            and r.get("thoughts_tokens") is not None and r.get("output_tokens") is not None]
    th = np.asarray([float(r["thoughts_tokens"]) for r in post])
    ans = np.asarray([float(r["output_tokens"]) for r in post])
    share = th / (th + ans)
    over_4096 = int(((th + ans) > 4096).sum())
    return {
        "field_coverage": field_coverage,
        "first_non_null_thoughts_tokens": first_thoughts,
        "pre_fix_window": "2026-04-08..2026-07-27",
        "pre_fix_records": len(pre),
        "pre_fix_successes": len(pre_ok),
        "pre_fix_successes_with_thoughts_tokens": sum(1 for r in pre_ok if r.get("thoughts_tokens") is not None),
        "pre_fix_success_output_tokens": pct(pre_ok_out) if pre_ok_out else None,
        "pre_fix_failure_output_tokens": pct(pre_fail_out) if pre_fail_out else None,
        "pre_fix_failure_output_mean": round(float(np.mean(pre_fail_out)), 1),
        "pre_fix_success_output_mean": round(float(np.mean(pre_ok_out)), 1),
        "july_prefix_failure_output_mean": round(float(np.mean(jul_fail_out)), 1),
        "july_prefix_failure_n": len(jul_fail_out),
        "july_prefix_success_output_mean": round(float(np.mean(jul_ok_out)), 1),
        "july_prefix_success_n": len(jul_ok_out),
        "registered_median_candidates": candidates,
        "post_fix_attempt1_success": {
            "window": "2026-08-01..2026-09-30",
            "n": int(th.size),
            "thinking_median": float(np.median(th)),
            "answer_median": float(np.median(ans)),
            "thinking_p10_p90": [float(np.percentile(th, 10)), float(np.percentile(th, 90))],
            "share_median": round(float(np.median(share)), 4),
            "share_ratio_of_medians": round(float(np.median(th) / (np.median(th) + np.median(ans))), 4),
            "share_pooled": round(float(th.sum() / (th.sum() + ans.sum())), 4),
            "calls_with_thinking_plus_answer_over_4096": over_4096,
            "fraction_over_4096": round(over_4096 / th.size, 4),
            "4096_minus_median_thinking": float(4096 - np.median(th)),
        },
    }


if __name__ == "__main__":
    try:
        result = {"o3_56_latency": latency_table(), "o3_57_completeness": completeness_check(), "o3_58_thinking": thinking()}
    except FileNotFoundError as exc:
        log.error("missing decision log: %s", exc)
        sys.exit(1)
    json.dump(result, sys.stdout, indent=1, default=str)
