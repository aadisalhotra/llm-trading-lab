"""Regression tests for the intraday pipeline's checkout freshness.

Guards the defect class that lost the 2026-08-17 14:34Z tick: a queued run
trading against a stale book, then losing its data to an unresolvable rebase
conflict.

The mechanism, because it is not obvious from any single line of the workflow:

  * `concurrency: {group: intraday-pipeline, cancel-in-progress: false}` makes
    an overlapping tick QUEUE rather than cancel. Queue waits of ~30 minutes
    are normal, because the `chain` job sleeps to the next boundary before the
    run completes.
  * `actions/checkout` defaults to the event SHA — main's tip at the moment the
    run was CREATED, not started. For a queued run those are ~30 minutes apart.
  * Every intraday tick rewrites the same paths wholesale (data/dashboard.json,
    all six data/state/*.json, the day's leaderboard) and appends to the same
    jsonl files. So a stale run's commit conflicts with the winner on every
    path it touches.
  * A content conflict is deterministic. The rebase-and-retry loop in
    "Commit + push updated data" retries it five times with backoff and fails
    identically each time, and the ephemeral runner then discards the data.

Discarding stale data is CORRECT — landing it would erase the executed fills
the winning tick recorded (on 2026-08-17 that was six fills across three
books). So the fix is not a better retry or a persist-to-next-tick queue;
both would publish a stale book. The fix is `ref: main`, so the tick reads the
current book and never becomes the loser of a content race in the first place.

These tests pin the three pieces that have to stay true together. Removing any
one of them silently restores the hole.

Run with: python -m pytest tests/test_intraday_checkout_freshness.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "intraday.yml"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _job_block(text: str, name: str) -> str:
    """Return the YAML text of one top-level job, without parsing YAML.

    pyyaml is not in requirements.txt and the pipeline must not grow a test-only
    dependency, so this slices on indentation: a job starts at two-space indent
    and ends where the next two-space key begins.
    """
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln == f"  {name}:")
    for end in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z_][\w-]*:", lines[end]):
            break
    else:
        end = len(lines)
    return "\n".join(lines[start:end])


def _checkout_with_block(job_text: str) -> str:
    """The `with:` mapping of the job's actions/checkout step."""
    lines = job_text.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "actions/checkout@" in ln)
    with_at = next(i for i in range(idx, len(lines)) if lines[i].strip() == "with:")
    indent = len(lines[with_at]) - len(lines[with_at].lstrip())
    out = []
    for ln in lines[with_at + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        out.append(ln)
    return "\n".join(out)


# ------------------------------------------------------------------ the fix

def test_run_job_checks_out_branch_tip_not_event_sha(workflow_text):
    """The trading job must read the CURRENT book, not the book at queue time.

    Without `ref:`, actions/checkout uses the event SHA. A run queued behind
    another tick then trades on holdings that predate the tick ahead of it.
    """
    with_block = _checkout_with_block(_job_block(workflow_text, "run"))
    assert re.search(r"^\s*ref:\s*main\s*$", with_block, re.M), (
        "the `run` job's checkout must pin `ref: main`; without it a queued "
        "tick trades against a stale book and loses its commit to a "
        "deterministic rebase conflict"
    )


def test_run_job_keeps_full_history(workflow_text):
    """`fetch-depth: 0` must survive — the push step rebases onto origin/main."""
    with_block = _checkout_with_block(_job_block(workflow_text, "run"))
    assert re.search(r"^\s*fetch-depth:\s*0\s*$", with_block, re.M)


# ------------------------------------------- the conditions the fix depends on

def test_overlapping_ticks_queue_rather_than_cancel(workflow_text):
    """The queue delay is what makes the event SHA stale.

    `cancel-in-progress: true` would remove the delay but is far worse: it can
    kill a tick after it has executed trades and before it has committed them.
    Pinned here so the coupling is explicit — if this ever flips, the reasoning
    on the checkout `ref` has to be revisited, not silently invalidated.
    """
    assert re.search(
        r"^concurrency:\n\s+group:\s*intraday-pipeline\n\s+cancel-in-progress:\s*false\s*$",
        workflow_text,
        re.M,
    )


def test_push_step_still_retries_with_rebase(workflow_text):
    """`ref: main` narrows the race; it does not replace the retry loop.

    Non-conflicting concurrent pushes (competitor monitor, keepalive, a human
    lane) still land mid-run and still need the rebase-and-retry.
    """
    run_job = _job_block(workflow_text, "run")
    assert "git pull --rebase origin main && git push" in run_job
    assert re.search(r"for attempt in 1 2 3 4 5; do", run_job)


def test_push_failure_is_loud(workflow_text):
    """Exhausting the retries must fail the job, not swallow the loss.

    A tick that cannot push has lost its data (the runner is ephemeral). That
    has to red the run so notify-failure fires — a silent `exit 0` here would
    turn data loss into a green run.
    """
    run_job = _job_block(workflow_text, "run")
    tail = run_job[run_job.index("for attempt in 1 2 3 4 5; do"):]
    assert "::error::" in tail
    assert re.search(r"^\s*exit 1\s*$", tail, re.M)
