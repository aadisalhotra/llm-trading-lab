"""Read probe H's three answers from the public GitHub API (no token needed).

Probe H = .github/workflows/failstop_probe.yml acting on the dummy
failstop_target.yml (ledger gate_a_and_fail_stop_2026_11_18, known_limits;
Thursday package item H). Run this after the probe run has been cancelled and
left alone for 10 minutes. It prints, and writes to --out as JSON:

  Q1  did the cancelled run's `fail-stop` job run (always() after cancel)?
  Q2  is failstop_target.yml now disabled_manually (GITHUB_TOKEN can disable)?
  Q3  what became of the target's two dispatched runs after the disable?
  and intraday.yml's state, which must be "active" throughout.

Usage: python scripts/read_failstop_probe.py [--run-id ID] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone

import requests

API = "https://api.github.com/repos/aadisalhotra/llm-trading-lab/actions"
logger = logging.getLogger("read_failstop_probe")


def get(url: str, **params):
    """GET with retries: this machine's connection to GitHub drops often."""
    for attempt in range(6):
        try:
            r = requests.get(url, params=params, timeout=30,
                             headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == 5:
                raise
            logger.warning("GET %s failed (%s); retrying", url, e)
            time.sleep(10)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", type=int, help="the probe run (default: the latest)")
    ap.add_argument("--out", help="write the answers here as JSON")
    a = ap.parse_args()

    runs = get(f"{API}/workflows/failstop_probe.yml/runs", per_page=5)["workflow_runs"]
    if not runs:
        sys.exit("No failstop_probe.yml run exists yet.")
    run = next((r for r in runs if r["id"] == a.run_id), None) if a.run_id else runs[0]
    if run is None:
        sys.exit(f"Run {a.run_id} not found among the latest probe runs.")
    jobs = get(f"{API}/runs/{run['id']}/jobs")["jobs"]
    by_name = {j["name"]: j for j in jobs}
    fs = by_name.get("fail-stop", {})
    target = get(f"{API}/workflows/failstop_target.yml")
    t_runs = get(f"{API}/workflows/failstop_target.yml/runs", per_page=10)["workflow_runs"]
    t_runs = [r for r in t_runs if r["created_at"] >= run["created_at"]]
    intraday = get(f"{API}/workflows/intraday.yml")

    started = fs.get("started_at") is not None and fs.get("conclusion") not in (None, "skipped")
    answers = {
        "read_at_utc": datetime.now(timezone.utc).isoformat(),
        "probe_run": {k: run.get(k) for k in ("id", "status", "conclusion", "created_at",
                                               "run_started_at", "updated_at", "html_url")},
        "jobs": [{k: j.get(k) for k in ("name", "status", "conclusion", "started_at",
                                        "completed_at")} for j in jobs],
        "Q1_always_job_runs_after_cancel": {
            "answer": "yes" if run["conclusion"] == "cancelled" and started else
                      ("no" if run["conclusion"] == "cancelled" else
                       f"not answerable: the probe run concluded {run['conclusion']!r}"),
            "fail_stop_job": {k: fs.get(k) for k in ("status", "conclusion", "started_at",
                                                     "completed_at")},
        },
        "Q2_github_token_can_disable": {
            "answer": "yes" if target.get("state") == "disabled_manually" else "no",
            "target_state": target.get("state"),
            "disable_step": next(({k: s.get(k) for k in ("name", "conclusion")}
                                  for s in fs.get("steps", [])
                                  if s["name"].startswith("Disable the target")), None),
        },
        "Q3_queued_runs_of_disabled_workflow": [
            {k: r.get(k) for k in ("id", "status", "conclusion", "created_at",
                                   "run_started_at", "updated_at")} for r in reversed(t_runs)],
        "intraday_yml_state": intraday.get("state"),
    }
    text = json.dumps(answers, indent=2)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
