"""xAI redirect probe (lifecycle_and_identity_ruling_2026-11-22.md section 2.3;
hub H-OPS-12). Off-cycle induced test: no orders, no series.

xAI retired several slugs at 12:00 PT on 2026-05-15 and redirects them to
grok-4.3 ("Model Retirement (May 15, 2026)", docs.x.ai, saved beside the
records): reasoning slugs to grok-4.3 at low reasoning effort, non-reasoning
ones at none. The probe calls two retired slugs and grok-4.3 directly at the
matching effort, several times each, and records the model named in each
response and its system_fingerprint. The ruling's decision rule, fixed before
the probe:
  A  the redirected response names the served model (grok-4.3);
  B  it names the requested slug and carries the served model's fingerprint;
  C  it is indistinguishable in both fields.

Keys are read from the environment and never printed or written.

Usage: python scripts/demos/xai_redirect_probe.py --repeats 3 --out docs/ops/xai_redirect_probe_2026-11-22
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config_loader import load_env  # noqa: E402

URL = "https://api.x.ai/v1/chat/completions"
SYSTEM = 'Respond with one JSON object only: {"answer": <integer>}.'
USER = "What is 17 * 23? Output json only."
# (requested slug, reasoning_effort sent or None) -- paired by what the
# redirect serves: grok-4-0709 -> grok-4.3 low; grok-3 -> grok-4.3 none.
ARMS = [("grok-4-0709", None), ("grok-4.3", "low"), ("grok-3", None), ("grok-4.3", "none")]


def call(model: str, effort: str | None) -> dict:
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                         {"role": "user", "content": USER}],
            "response_format": {"type": "json_object"}, "max_tokens": 256}
    if effort is not None:
        body["reasoning_effort"] = effort
    t0 = time.monotonic()
    rec = {"requested": model, "reasoning_effort_sent": effort,
           "utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        r = requests.post(URL, json=body, timeout=120,
                          headers={"Authorization": f"Bearer {os.environ.get('XAI_API_KEY', '')}"})
        rec["http_status"] = r.status_code
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        rec["raw"] = data if r.ok else {"error": r.text[:2000]}
        u = data.get("usage") or {}
        rec.update(model_named=data.get("model"), system_fingerprint=data.get("system_fingerprint"),
                   reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                   completion_tokens=u.get("completion_tokens"), prompt_tokens=u.get("prompt_tokens"),
                   cost_in_usd_ticks=u.get("cost_in_usd_ticks"))
    except Exception as e:  # noqa: BLE001 -- recorded
        rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    rec["latency_s"] = round(time.monotonic() - t0, 2)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load_env()
    recs = []
    for i in range(a.repeats):
        for model, effort in ARMS:
            recs.append({"round": i, **call(model, effort)})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "records.json"
    p.write_text(json.dumps(recs, indent=2) + "\n", encoding="utf-8", newline="\n")
    summary = [{k: r.get(k) for k in ("round", "requested", "reasoning_effort_sent", "http_status",
                                       "model_named", "system_fingerprint", "reasoning_tokens",
                                       "latency_s", "error")} for r in recs]
    (out / "summary.json").write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "lifecycle_and_identity_ruling_2026-11-22.md section 2.3: xAI redirect probe. "
                   "Induced test; no orders; enters no series.",
        "records_sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "calls": summary},
        indent=2) + "\n", encoding="utf-8", newline="\n")
    for s in summary:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
