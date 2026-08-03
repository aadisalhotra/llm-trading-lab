"""Cost-rates reconciliation (2026-08): reprice all logged calls, old table vs corrected history.

Companion evidence for the 2026-08 cost_rates.py restructure (flat COST_PER_MTOK
-> dated RATE_HISTORY). Reprices every decision and screening call in
data/trades/*_2026-0*.jsonl under both tables and reports per-month, per-model
totals — the source of the July cost-overstatement figure.

Old-table replication: the flat COST_PER_MTOK exactly as committed before the
restructure, with the same prefix-fallback resolution the adapters used. This
is what priced every logged cost_usd / screening_cost_usd at call time, so
`logged == old-table pricing of the record's tokens` is asserted per record
(tolerance 5e-4 for the 6dp rounding of stored costs); any miss is reported as
an anomaly. Zero anomalies == the old table is a complete account of how every
stored cost arose, and the delta to the corrected table is the full error.

Screening calls: the log stores only output tokens (screening_tokens) plus the
old-rate cost, so input tokens are back-solved from the old cost — exact modulo
the 6dp rounding (±0.2 tokens at the rates involved). A screening input-token
field in the decision log would make this back-solve unnecessary; flagged in
the reconciliation report.

Read-only: writes nothing, prints the comparison table.
"""
import json
from collections import defaultdict
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.analytics.cost_rates import compute_call_cost_usd  # noqa: E402

# The pre-2026-08 flat table, verbatim (USD per MTok: input, output).
OLD_TABLE = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-6": (15.00, 75.00),
    "gpt-5.4": (10.00, 30.00),
    "gemini-3.1-pro-preview": (3.50, 14.00),
    "grok-4": (5.00, 15.00),
    "grok-4.20-0309-reasoning": (5.00, 25.00),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v4-pro": (1.74, 3.48),
    "deepseek-v4-flash": (0.14, 0.28),
}


def old_rates(model_id):
    """Old-table lookup with the adapters' prefix-fallback resolution."""
    if not model_id:
        return None
    if model_id in OLD_TABLE:
        return OLD_TABLE[model_id]
    parts = model_id.split("-")
    while len(parts) > 1:
        parts.pop()
        cand = "-".join(parts)
        if cand in OLD_TABLE:
            return OLD_TABLE[cand]
    return None


def old_cost(model_id, tin, tout):
    r = old_rates(model_id)
    if r is None:
        return None
    return (tin / 1e6) * r[0] + (tout / 1e6) * r[1]


def main():
    acc = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(lambda: defaultdict(int))
    anomalies = []

    for f in sorted(REPO.glob("data/trades/*_2026-0*.jsonl")):
        model_key = f.stem.rsplit("_", 1)[0]
        month = f.stem.rsplit("_", 1)[1]
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                date = rec.get("date") or f"{month}-15"
                mid = rec.get("model_id_returned") or rec.get("model_id_configured") or ""
                tin = int(rec.get("input_tokens") or 0)
                tout = int(rec.get("output_tokens") or 0)
                a = acc[(month, model_key)]
                c = counts[(month, model_key)]

                # ---- decision call ----
                logged = rec.get("cost_usd")
                if logged is not None:
                    a["old_decision"] += float(logged)
                    c["decision_logged"] += 1
                    chk = old_cost(mid, tin, tout)
                    if chk is None or abs(chk - float(logged)) > 5e-4:
                        anomalies.append(
                            f"{model_key} {date}: logged {logged} != old-table {chk} "
                            f"(mid={mid}, in={tin}, out={tout})")
                elif tin > 0 or tout > 0:
                    # Null cost with tokens present: the report pipeline's
                    # backfill priced these at the (old) table when summing.
                    bf = old_cost(mid, tin, tout)
                    if bf is not None:
                        a["old_decision"] += bf
                        c["decision_backfilled"] += 1
                else:
                    c["decision_no_cost"] += 1

                if tin > 0 or tout > 0:
                    new = compute_call_cost_usd(mid, tin, tout, on_date=date)
                    if new is None:
                        anomalies.append(f"{model_key} {date}: corrected rate MISSING (mid={mid})")
                    else:
                        a["new_decision"] += new

                # ---- screening call (back-solve input from old cost) ----
                sc = rec.get("screening_cost_usd")
                so = rec.get("screening_tokens")
                if sc is not None and so is not None:
                    r_old = old_rates(mid)
                    if r_old is None:
                        anomalies.append(f"{model_key} {date}: no old rate for screening (mid={mid})")
                        continue
                    si = (float(sc) * 1e6 - float(so) * r_old[1]) / r_old[0]
                    if si < -1:
                        anomalies.append(f"{model_key} {date}: screening back-solve negative ({si:.0f})")
                        continue
                    si = max(0, round(si))
                    a["old_screen"] += float(sc)
                    c["screen_logged"] += 1
                    new_s = compute_call_cost_usd(mid, si, int(so), on_date=date)
                    if new_s is None:
                        anomalies.append(f"{model_key} {date}: corrected screening rate MISSING (mid={mid})")
                    else:
                        a["new_screen"] += new_s

    models = ["claude", "claude_opus", "gpt", "gemini", "grok", "deepseek"]
    months = sorted({m for (m, _) in acc})

    print("=== Per-month totals (decision + screening), old vs corrected ===")
    for month in months:
        mo_old = mo_new = 0.0
        print(f"\n-- {month} --")
        print(f"{'model':<12} {'old $':>10} {'new $':>10} {'delta $':>10} {'over %':>8}")
        for mk in models:
            a = acc.get((month, mk))
            if not a:
                continue
            o = a["old_decision"] + a["old_screen"]
            n = a["new_decision"] + a["new_screen"]
            mo_old += o
            mo_new += n
            pct = (o - n) / n * 100 if n else float("nan")
            print(f"{mk:<12} {o:>10.2f} {n:>10.2f} {o - n:>10.2f} {pct:>7.0f}%")
        pct = (mo_old - mo_new) / mo_new * 100 if mo_new else float("nan")
        print(f"{'TOTAL':<12} {mo_old:>10.2f} {mo_new:>10.2f} {mo_old - mo_new:>10.2f} {pct:>7.0f}%")

    print("\n=== Per-record cross-check ===")
    print(f"anomalies: {len(anomalies)}")
    for x in anomalies[:20]:
        print("  " + x)
    if not anomalies:
        print("  (old table reproduces every stored cost; corrected table resolves every call)")


if __name__ == "__main__":
    main()
