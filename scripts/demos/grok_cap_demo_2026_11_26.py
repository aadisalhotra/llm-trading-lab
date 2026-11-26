"""Grok cap demonstration (calibration_outcomes_rulings_2026-11-26, hub H-OPS-39 item 1).

Induced test, off-cycle: three calls to grok-4.7 on calibration inputs with
max_tokens = 1024 in place of the production 16,384, through the production
adapter and the calibration tool's own call path (no parse retry, finish
classified), with the reasoning level sent (as production will send it).
Shown if every call ends normally with reasoning above 1,024 tokens: then the
cap binds only the answer.

Inputs: the first three trading inputs of the common set in its own order
(tick 0, snapshots in SNAPSHOTS order), chosen before any result is seen.

usage: python scripts/demos/grok_cap_demo_2026_11_26.py <inputs_dir> <env_file> <out_dir>
Nothing secret is printed or written; error text goes through the tool's redaction.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CAP = 1024


def main() -> int:
    inputs_dir, env_file, out_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    from dotenv import dotenv_values
    for k, v in dotenv_values(env_file).items():
        if v is not None:
            os.environ.setdefault(k, v)
    from scripts.calibration import calibrate_refreshed_arms as cal
    from src.adapters import xai_adapter
    xai_adapter.REFRESHED_MAX_TOKENS = CAP           # the induced cap, read at call time
    cal.TOOL_COMMIT = cal.tool_commit()["commit"]
    inputs = cal.load_common_set(inputs_dir, ["2026-11-23", "2026-11-24", "2026-11-25"])[:3]
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = []
    for x in inputs:
        r = cal.run_call("cb_grok", "trade", "send", x, cal.call_bound(840.0))
        r["max_tokens_sent"] = CAP
        r["demo"] = "grok_cap_1024"
        recs.append(r)
        print(json.dumps({k: r.get(k) for k in ("input_id", "cls", "finish_reason", "finish_class",
                                                "reasoning_tokens", "visible_tokens", "latency_s")}))
    with open(out_dir / "records.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    shown = all(r["cls"] == "valid" and r.get("finish_class") == "normal"
                and (r.get("reasoning_tokens") or 0) > CAP for r in recs)
    manifest = {"demo": "grok cap demonstration, hub H-OPS-39 item 1",
                "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": "grok-4.7", "max_tokens": CAP, "level_sent": True,
                "tool_commit": cal.TOOL_COMMIT, "inputs_dir": str(inputs_dir),
                "inputs": [x["input_id"] for x in inputs], "calls": len(recs),
                "rule": "shown if every call ends normally with reasoning above 1,024",
                "shown": shown}
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"shown": shown}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
