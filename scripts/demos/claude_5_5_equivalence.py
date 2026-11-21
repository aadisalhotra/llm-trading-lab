"""Send/omit equivalence for the Claude 5.5 arms (cohort_refresh_ruling.md rev. 2
section 5 item 2), through the production adapter.

Per arm, N pairs on one synthetic prompt (not the calibration set):
  send  = AnthropicAdapter.generate_decision (the production request: adaptive
          thinking, effort as ruled, cap 128,000, streamed);
  omit  = the same request through the SDK with `thinking` and `output_config`
          left out, same cap, streamed.
Records reasoning-token counts (usage.output_tokens_details.thinking_tokens),
the model echoed and the stop reason. Induced tests: off-cycle, no orders, no
series. Keys are read from the environment and never printed.

Usage: python scripts/demos/claude_5_5_equivalence.py --pairs 5 \
           --out docs/ops/adapter_probes_2026-11-21/claude_5_5_equivalence.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.adapters import get_adapter  # noqa: E402
from src.adapters import anthropic_adapter as aa  # noqa: E402
from src.config_loader import load_env  # noqa: E402

SYSTEM = ('Respond with one JSON object only: {"decisions": [], "overall_reasoning": "<the answer as '
          'an integer>"}. No prose, no code fences.')
PROMPT = ("Let N be the number of ordered pairs of integers (x, y) with x^2 + y^2 = 5^10 * 13^4 * 17^2. "
          "Also let M be the number of those pairs with x > 0 and y > 0 and gcd(x, y) = 1. "
          "Work it out and verify both counts. Put N + M in overall_reasoning.")


def send(model: str) -> dict:
    t = time.monotonic()
    r = get_adapter("anthropic", model).generate_decision(SYSTEM, PROMPT)
    md = r.metadata or {}
    return {"arm": "send", "ok": r.success, "model": r.model_id_returned,
            "finish": md.get("finish_reason"), "thinking_tokens": md.get("thoughts_tokens"),
            "output_tokens": md.get("output_tokens"), "effort_sent": md.get("reasoning_effort_sent"),
            "attempt": md.get("attempt"), "error_class": r.error_class,
            "latency_s": round(time.monotonic() - t, 2)}


def omit(model: str) -> dict:
    import anthropic
    t = time.monotonic()
    c = anthropic.Anthropic(timeout=600, max_retries=0)
    try:
        details = None
        with c.messages.stream(model=model, max_tokens=aa.REFRESHED_MAX_TOKENS, system=SYSTEM,
                               messages=[{"role": "user", "content": PROMPT}]) as s:
            for ev in s:            # the thinking count rides on message_delta, as in the adapter
                if getattr(ev, "type", None) == "message_delta":
                    details = getattr(getattr(ev, "usage", None), "output_tokens_details", None) or details
            m = s.get_final_message()
    except Exception as e:  # noqa: BLE001 -- recorded, not raised
        return {"arm": "omit", "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}",
                "latency_s": round(time.monotonic() - t, 2)}
    return {"arm": "omit", "ok": True, "model": m.model, "finish": m.stop_reason,
            "thinking_tokens": aa.thinking_tokens(getattr(m.usage, "output_tokens_details", None)
                                                  or details),
            "output_tokens": m.usage.output_tokens, "latency_s": round(time.monotonic() - t, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load_env()
    jobs = [(m, f) for m in aa.REFRESHED_ARMS for _ in range(a.pairs) for f in (send, omit)]
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        recs = list(pool.map(lambda j: {"model_requested": j[0], **j[1](j[0])}, jobs))
    summary = {}
    for m in aa.REFRESHED_ARMS:
        row = {}
        for arm in ("send", "omit"):
            v = [r["thinking_tokens"] for r in recs
                 if r["model_requested"] == m and r["arm"] == arm and r.get("ok")
                 and r.get("thinking_tokens") is not None]
            row[arm] = {"n": len(v), "median": statistics.median(v) if v else None,
                        "min": min(v) if v else None, "max": max(v) if v else None, "values": v}
        row["models_echoed"] = sorted({r.get("model") for r in recs
                                       if r["model_requested"] == m and r.get("ok")})
        row["failures"] = [r for r in recs if r["model_requested"] == m and not r.get("ok")]
        summary[m] = row
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "purpose": "cohort_refresh_ruling.md rev. 2 section 5 item 2: send/omit equivalence by "
                      "reasoning-token counts. Induced tests; enter no series.",
           "system": SYSTEM, "prompt": PROMPT, "pairs": a.pairs, "summary": summary, "records": recs}
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({m: {k: v for k, v in s.items() if k != "failures"} | {"failures": len(s["failures"])}
                      for m, s in summary.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
