"""Workflow fail-stop — did a failed or cancelled intraday run submit any order?

Registered requirement (tick_admission_guards_ruling.md section 3, restated
per hub amendment 2):

    On failure or cancellation of an intraday run in a broker mode, if the run
    submitted any order, the intraday workflow is disabled and stays disabled
    until a person has reconciled the books to the venue and the re-enable is
    ledgered. Whether the run submitted an order is determined from the venue,
    by order ids in the run's window — the dead runner cannot be asked. If that
    cannot be determined, the workflow is disabled.

This script DETERMINES; it never disables. It prints one verdict and writes it
to $GITHUB_OUTPUT as `verdict=clear` or `verdict=disable`. The workflow step
after it disables unless the verdict is exactly `clear` — so a crash here, a
missing dependency, a timeout or a typo all disable. Fail-closed by
construction, not by this file's error handling alone.

The GitHub check before the venue (hub ruling 2026-11-17). The Actions jobs
API may answer "clear" WITHOUT the venue only when the run's jobs and steps
POSITIVELY show that the "Run pipeline" step never started: exactly one `run`
job, exactly one such step, its `started_at` present and null, and its state
one of pending / queued / waiting / requested, or completed as skipped or
cancelled. Anything else — an API error, a missing field, more than one
match, a null start beside a state that implies it ran — is not an answer,
and the venue is asked.

The venue window opens at the `run` job's start (or the pipeline step's) less
a skew margin. When the jobs API cannot give either, it opens LOOKBACK before
now — longer than GitHub's maximum job time — so every order the run could
have placed is inside it. That wide window also holds earlier ticks' orders,
which then count against this run: under ambiguity the outcome on a trading
day is a disable, which is the safe side.

How an order is attributed. Every pipeline order id is
`{book}-{cycle_id}-{seq:02d}-a{attempt}` with `cycle_id` the run's UTC start
stamp (`src/execution/broker.build_order_id`). Runs are serialized by the
intraday-pipeline concurrency group, so an id whose book segment is a model
key and whose cycle_id falls in the window is this run's. Barrier-probe orders
carry the administrative FORCED_PROBE_ segment and a separate workflow; they
are excluded. ANY other order in the window — an unknown segment, an
unparseable cycle_id, a book order whose cycle predates the window — cannot be
attributed and therefore cannot be ruled out: `disable`.

Out of scope by the ruling's own limit: a run that SUCCEEDS is never examined
here (the fourth route of ruling section 1).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("llmlab.fail_stop")

VERDICT_CLEAR = "clear"
VERDICT_DISABLE = "disable"

RUN_JOB_NAME = "run"
PIPELINE_STEP_NAME = "Run pipeline"
# Clock-skew margin between GitHub's job timestamps and the runner's cycle_id
# stamp. Errs toward attributing MORE orders to this run (a spurious disable),
# never fewer. The previous run in the group finished its pipeline step long
# before this job started (its commit, push and chain jobs run in between).
SKEW_MARGIN = timedelta(minutes=2)
# Venue window when the jobs API cannot date the run: past GitHub's maximum
# job execution time (6 h), plus an hour of queueing.
LOOKBACK = timedelta(hours=7)
# The only step states that positively mean "never started".
NEVER_STARTED_STATUSES = frozenset({"pending", "queued", "waiting", "requested"})
NEVER_STARTED_CONCLUSIONS = frozenset({"skipped", "cancelled"})
ORDER_PAGE_LIMIT = 500
MAX_ORDER_PAGES = 20
CYCLE_ID_FORMAT = "%Y%m%dT%H%M%SZ"


class Undetermined(Exception):
    """Whether the run submitted an order cannot be established."""


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ pure logic

def _run_job_and_step(jobs_payload: Any) -> tuple[dict | None, dict | None, str]:
    """The single `run` job and its single "Run pipeline" step, or why not."""
    jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
    if not isinstance(jobs, list):
        return None, None, "jobs API payload has no job list"
    runs = [j for j in jobs if isinstance(j, dict) and j.get("name") == RUN_JOB_NAME]
    if len(runs) != 1:
        return None, None, f"{len(runs)} jobs named {RUN_JOB_NAME!r}"
    job = runs[0]
    steps = job.get("steps")
    if not isinstance(steps, list):
        return job, None, "run job has no step list"
    matches = [s for s in steps if isinstance(s, dict) and s.get("name") == PIPELINE_STEP_NAME]
    if len(matches) != 1:
        return job, None, f"{len(matches)} steps named {PIPELINE_STEP_NAME!r}"
    return job, matches[0], "found"


def pipeline_never_started(jobs_payload: Any) -> tuple[bool, str]:
    """True ONLY when the payload positively shows the pipeline step never
    started. Never raises; every doubt returns False (ask the venue)."""
    _job, step, why = _run_job_and_step(jobs_payload)
    if step is None:
        return False, why
    if "started_at" not in step:
        return False, "pipeline step has no started_at field"
    if step["started_at"] is not None:
        return False, "pipeline step has a start time"
    status, conclusion = step.get("status"), step.get("conclusion")
    if status in NEVER_STARTED_STATUSES:
        return True, f"pipeline step {status}, never started"
    if status == "completed" and conclusion in NEVER_STARTED_CONCLUSIONS:
        return True, f"pipeline step {conclusion} before it started"
    return False, f"ambiguous: no start time but status={status!r} conclusion={conclusion!r}"


def window_start_from(jobs_payload: Any) -> datetime | None:
    """The run job's start (or the pipeline step's), if the payload has one."""
    job, step, _ = _run_job_and_step(jobs_payload)
    for source in (job, step):
        start = _parse_iso((source or {}).get("started_at"))
        if start is not None:
            return start
    return None


def _cycle_of(client_order_id: str) -> datetime | None:
    parts = client_order_id.split("-")
    if len(parts) < 4:
        return None
    try:
        return datetime.strptime(parts[1], CYCLE_ID_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def attribute_orders(
    orders: Iterable[dict[str, Any]],
    *,
    book_keys: Iterable[str],
    window_start: datetime,
    probe_prefix: str,
) -> tuple[list[str], list[str]]:
    """Split the window's venue orders into (this_run, unattributable).

    Probe orders are dropped. Both returned lists mean "disable".
    """
    books = set(book_keys)
    floor = window_start - SKEW_MARGIN
    this_run: list[str] = []
    unknown: list[str] = []
    for o in orders:
        coid = str((o or {}).get("client_order_id") or "")
        segment = coid.split("-", 1)[0] if "-" in coid else ""
        if segment.startswith(probe_prefix):
            continue
        if segment not in books:
            unknown.append(coid or f"<no client_order_id: {o.get('id')}>")
            continue
        cycle = _cycle_of(coid)
        if cycle is None or cycle < floor:
            # A book order submitted inside the window whose cycle is not this
            # run's: nothing explains it, so it cannot be ruled out.
            unknown.append(coid)
            continue
        this_run.append(coid)
    return this_run, unknown


def decide(
    *,
    mode: str,
    broker_modes: Iterable[str],
    jobs_payload_fn: Callable[[], dict[str, Any]],
    orders_fn: Callable[[datetime], list[dict[str, Any]]],
    book_keys: Iterable[str],
    probe_prefix: str,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """The verdict. Never raises: anything undetermined is `disable`."""
    out: dict[str, Any] = {"mode": mode, "this_run_orders": [], "unattributable_orders": []}
    try:
        if mode not in set(broker_modes):
            out.update(verdict=VERDICT_CLEAR, reason=f"mode {mode!r} reaches no venue")
            return out
        # The GitHub check. Its failure is not an answer: fall through.
        try:
            payload: Any = jobs_payload_fn()
        except Exception as e:  # noqa: BLE001 — an API error falls through to the venue
            payload = None
            out["jobs_api_error"] = f"{type(e).__name__}: {e}"
        never, detail = pipeline_never_started(payload)
        out["github_check"] = detail
        if never:
            out.update(verdict=VERDICT_CLEAR, reason="the pipeline never started: no order possible")
            return out
        window_start = window_start_from(payload)
        if window_start is None:
            window_start = now_fn() - LOOKBACK
            out["window_basis"] = "lookback (jobs API gave no start time)"
        else:
            out["window_basis"] = "run job start"
        out["window_start"] = window_start.isoformat()
        orders = orders_fn(window_start - SKEW_MARGIN)
        out["orders_in_window"] = len(orders)
        mine, unknown = attribute_orders(orders, book_keys=book_keys,
                                         window_start=window_start, probe_prefix=probe_prefix)
        out["this_run_orders"], out["unattributable_orders"] = mine, unknown
        if mine or unknown:
            out.update(verdict=VERDICT_DISABLE,
                       reason=f"{len(mine)} order(s) from this run, {len(unknown)} unattributable")
        else:
            out.update(verdict=VERDICT_CLEAR, reason="the venue holds no order from this run")
        return out
    except Exception as e:  # noqa: BLE001 — undetermined is a disable, by rule
        out.update(verdict=VERDICT_DISABLE, reason=f"undetermined: {type(e).__name__}: {e}")
        return out


# ------------------------------------------------------------------ I/O

def fetch_jobs(repo: str, run_id: str, token: str) -> dict[str, Any]:
    import requests
    if not (repo and run_id and token):
        raise Undetermined("GITHUB_REPOSITORY, run id and GITHUB_TOKEN are required")
    r = requests.get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs",
        params={"filter": "latest", "per_page": 100},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_orders(client: Any, after: datetime) -> list[dict[str, Any]]:
    """Every order submitted after `after`, all statuses, paginated ascending."""
    collected: list[dict[str, Any]] = []
    cursor = after
    for _ in range(MAX_ORDER_PAGES):
        page = client._request("GET", "/v2/orders", params={
            "status": "all", "after": cursor.isoformat(), "limit": ORDER_PAGE_LIMIT,
            "direction": "asc", "nested": "false"})
        if not isinstance(page, list):
            raise Undetermined(f"venue order listing returned {type(page).__name__}")
        collected.extend(page)
        if len(page) < ORDER_PAGE_LIMIT:
            return collected
        last = _parse_iso(page[-1].get("submitted_at"))
        if last is None or last <= cursor:
            raise Undetermined("cannot paginate the venue order listing")
        cursor = last
    raise Undetermined(f"more than {MAX_ORDER_PAGES * ORDER_PAGE_LIMIT} orders in the window")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", ""))
    ap.add_argument("--out", default="fail_stop_verdict.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from src.config_loader import load_env, load_settings
    from src.execution.administrative import PROBE_BOOK_PREFIX
    from src.execution.broker import BROKER_MODES, BrokerClient

    load_env()
    settings = load_settings()
    mode = str(settings.get("mode", ""))
    repo, token = os.getenv("GITHUB_REPOSITORY", ""), os.getenv("GITHUB_TOKEN", "")

    result = decide(
        mode=mode,
        broker_modes=BROKER_MODES,
        jobs_payload_fn=lambda: fetch_jobs(repo, args.run_id, token),
        orders_fn=lambda after: fetch_orders(BrokerClient(mode), after),
        # Every model key, not only the live cohort: a superset can only
        # attribute more orders to this run, never fewer.
        book_keys=list((settings.get("models") or {}).keys()),
        probe_prefix=PROBE_BOOK_PREFIX,
    )
    result["run_id"] = args.run_id
    result["run_result"] = os.getenv("RUN_RESULT", "")
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("fail-stop verdict: %s (%s)", result["verdict"], result["reason"])
    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"verdict={result['verdict']}\n")
    logger.info("fail-stop result: %s", json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
