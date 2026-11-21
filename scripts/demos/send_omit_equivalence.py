"""Send/omit equivalence for a refreshed arm (cohort_refresh_ruling.md rev. 2
section 5 item 2), through the production adapter.

Per arm, N pairs on one synthetic prompt (not the calibration set):
  send = the production adapter's generate_decision (level sent explicitly);
  omit = the same request through the provider's API with the level field
         left out, same cap and transport.
Records reasoning-token counts, the model echoed and the finish reason.
Induced tests: off-cycle, no orders, no series. Keys are read from the
environment and never printed. Preliminary evidence for Research.

Usage: python scripts/demos/send_omit_equivalence.py --arm gpt-6-astra --pairs 5 \
           --out docs/ops/adapter_probes_2026-11-21/gpt-6-astra_equivalence.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.adapters import get_adapter  # noqa: E402
from src.config_loader import load_env  # noqa: E402

SYSTEM = ('Respond with one JSON object only: {"decisions": [], "overall_reasoning": "<the answer as '
          'an integer>"}. No prose, no code fences.')
PROMPT = ("Let N be the number of ordered pairs of integers (x, y) with x^2 + y^2 = 5^10 * 13^4 * 17^2. "
          "Also let M be the number of those pairs with x > 0 and y > 0 and gcd(x, y) = 1. "
          "Work it out and verify both counts. Put N + M in overall_reasoning.")
PROVIDER = {"gpt-6-astra": "openai", "grok-4.7": "xai", "gemini-4-argon": "google"}


def send(model: str) -> dict:
    t = time.monotonic()
    r = get_adapter(PROVIDER[model], model).generate_decision(SYSTEM, PROMPT)
    md = r.metadata or {}
    return {"arm": "send", "ok": r.success, "model": r.model_id_returned,
            "finish": md.get("finish_reason"), "reasoning_tokens": md.get("thoughts_tokens"),
            "output_tokens": md.get("output_tokens"), "level_sent": md.get("reasoning_effort_sent"),
            "error_class": r.error_class, "error": (r.error or "")[:200] or None,
            "latency_s": round(time.monotonic() - t, 2)}


def omit(model: str) -> dict:
    t = time.monotonic()
    try:
        if model == "gpt-6-astra":
            from openai import OpenAI

            from src.adapters import openai_adapter as oa
            c = OpenAI(timeout=600, max_retries=0)
            r = c.chat.completions.create(
                model=model, messages=[{"role": "system", "content": SYSTEM},
                                       {"role": "user", "content": PROMPT}],
                response_format={"type": "json_object"},
                max_completion_tokens=oa.REFRESHED_MAX_COMPLETION_TOKENS)
            det = getattr(r.usage, "completion_tokens_details", None)
            out = {"model": r.model, "finish": r.choices[0].finish_reason,
                   "reasoning_tokens": getattr(det, "reasoning_tokens", None),
                   "output_tokens": r.usage.completion_tokens}
        elif model == "grok-4.7":
            import requests

            from src.adapters import xai_adapter as xa
            body = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                                 {"role": "user", "content": PROMPT}],
                    "response_format": {"type": "json_object"}, "max_tokens": xa.REFRESHED_MAX_TOKENS}
            resp = requests.post("https://api.x.ai/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {os.environ.get('XAI_API_KEY', '')}"},
                                 json=body, timeout=600)
            resp.raise_for_status()
            j = resp.json()
            u = j.get("usage") or {}
            out = {"model": j.get("model"), "finish": j["choices"][0].get("finish_reason"),
                   "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                   "output_tokens": u.get("completion_tokens")}
        else:
            from google import genai
            from google.genai import types as gt

            from src.adapters import gemini_adapter as ga
            c = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
            r = c.models.generate_content(model=model, contents=PROMPT, config=gt.GenerateContentConfig(
                system_instruction=SYSTEM, response_mime_type="application/json",
                max_output_tokens=ga.REFRESHED_MAX_OUTPUT_TOKENS,
                http_options=gt.HttpOptions(timeout=600_000)))
            u = r.usage_metadata
            out = {"model": getattr(r, "model_version", None),
                   "finish": str(r.candidates[0].finish_reason) if r.candidates else None,
                   "reasoning_tokens": getattr(u, "thoughts_token_count", None),
                   "output_tokens": getattr(u, "candidates_token_count", None)}
    except Exception as e:  # noqa: BLE001 -- recorded, not raised
        return {"arm": "omit", "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}",
                "latency_s": round(time.monotonic() - t, 2)}
    return {"arm": "omit", "ok": True, **out, "latency_s": round(time.monotonic() - t, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=sorted(PROVIDER))
    ap.add_argument("--pairs", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load_env()
    jobs = [f for _ in range(a.pairs) for f in (send, omit)]
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        recs = list(pool.map(lambda f: f(a.arm), jobs))
    summary = {}
    for arm in ("send", "omit"):
        v = [r["reasoning_tokens"] for r in recs if r["arm"] == arm and r.get("ok")
             and r.get("reasoning_tokens") is not None]
        summary[arm] = {"n": len(v), "median": statistics.median(v) if v else None,
                        "min": min(v) if v else None, "max": max(v) if v else None, "values": v}
    summary["models_echoed"] = sorted({str(r.get("model")) for r in recs if r.get("ok")})
    summary["finishes"] = sorted({str(r.get("finish")) for r in recs if r.get("ok")})
    summary["failures"] = [r for r in recs if not r.get("ok")]
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "purpose": "cohort_refresh_ruling.md rev. 2 section 5 item 2: send/omit equivalence by "
                      "reasoning-token counts. Preliminary; induced tests; enter no series.",
           "arm": a.arm, "system": SYSTEM, "prompt": PROMPT, "pairs": a.pairs,
           "summary": summary, "records": recs}
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: (v if k != "failures" else len(v)) for k, v in summary.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
