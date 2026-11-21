"""Workflow fail-stop — demonstration (ix), tick_admission_guards_ruling.md §3.

    (ix) a failed run and a cancelled run, each having submitted an order ->
         the workflow is disabled; a failed run that submitted none -> it is
         not; a run whose orders cannot be determined -> it is.

Two layers are demonstrated, because the fail-closed property lives in both:

  * scripts/fail_stop.py decides (`decide`), against a fake jobs API payload
    and a fake venue order listing in the real id format;
  * the workflow's disable step — the actual bash extracted from
    .github/workflows/intraday.yml — acts, with a stub `curl` on PATH that
    records calls instead of reaching GitHub. That step must disable on every
    verdict except exactly "clear", including no verdict at all (the
    determine step crashed or never ran).

No venue, no GitHub API, no validation book is touched (condition 4). The
live-workflow form of (ix) — an induced failure on GitHub — needs a separate
paper account and is not run here.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fail_stop as fs  # noqa: E402

from src.execution.administrative import PROBE_BOOK_PREFIX  # noqa: E402
from src.execution.broker import BROKER_MODES, build_order_id  # noqa: E402

JOB_START = datetime(2026, 11, 18, 15, 0, 5, tzinfo=timezone.utc)
BOOKS = ["claude", "claude_opus", "gpt", "gemini", "grok", "deepseek"]
THIS_CYCLE = (JOB_START + timedelta(seconds=70)).strftime("%Y%m%dT%H%M%SZ")
PREV_CYCLE = (JOB_START - timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")


def _jobs(step_conclusion="failure", step_started=True, job_name="run"):
    step = {"name": "Run pipeline", "status": "completed", "conclusion": step_conclusion,
            "started_at": (JOB_START + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
            if step_started else None}
    return {"jobs": [{"name": job_name, "started_at": JOB_START.isoformat().replace("+00:00", "Z"),
                      "steps": [{"name": "Checkout", "conclusion": "success",
                                 "started_at": JOB_START.isoformat()}, step]}]}


def _order(coid):
    return {"id": "x", "client_order_id": coid, "submitted_at": JOB_START.isoformat()}


NOW = JOB_START + timedelta(minutes=20)


def _decide(jobs, orders, mode="broker_paper"):
    seen = {}

    def orders_fn(after):
        seen["after"] = after
        if isinstance(orders, Exception):
            raise orders
        return orders

    def jobs_fn():
        if isinstance(jobs, Exception):
            raise jobs
        return jobs
    out = fs.decide(mode=mode, broker_modes=BROKER_MODES, jobs_payload_fn=jobs_fn,
                    orders_fn=orders_fn, book_keys=BOOKS, probe_prefix=PROBE_BOOK_PREFIX,
                    now_fn=lambda: NOW)
    return out, seen


ORDER = build_order_id("gpt", THIS_CYCLE, 1)


# ------------------------------------------------------- the four (ix) cases

def test_ix_failed_run_with_an_order_disables():
    out, _ = _decide(_jobs("failure"), [_order(ORDER)])
    assert out["verdict"] == "disable" and out["this_run_orders"] == [ORDER]


def test_ix_cancelled_run_with_an_order_disables():
    out, _ = _decide(_jobs("cancelled"), [_order(ORDER)])
    assert out["verdict"] == "disable" and out["this_run_orders"] == [ORDER]


def test_ix_failed_run_with_no_order_does_not_disable():
    out, seen = _decide(_jobs("failure"), [])
    assert out["verdict"] == "clear"
    assert seen["after"] == JOB_START - fs.SKEW_MARGIN      # the venue WAS asked


@pytest.mark.parametrize("label,jobs,orders", [
    ("venue unreachable", _jobs(), ConnectionError("venue down")),
    ("jobs API AND venue unreachable", ConnectionError("api down"), ConnectionError("venue down")),
    ("venue listing not a list", _jobs(), {"message": "forbidden"}),
])
def test_ix_undeterminable_disables(label, jobs, orders):
    if isinstance(orders, dict):
        out = fs.decide(mode="broker_paper", broker_modes=BROKER_MODES,
                        jobs_payload_fn=lambda: jobs,
                        orders_fn=lambda after: fs.fetch_orders(_FakeClient(orders), after),
                        book_keys=BOOKS, probe_prefix=PROBE_BOOK_PREFIX)
    else:
        out, _ = _decide(jobs, orders)
    assert out["verdict"] == "disable", label
    assert out["reason"].startswith("undetermined")


# ------------------------- the GitHub check before the venue (ruling 2026-11-17)
# "clear" without the venue ONLY when the jobs and steps positively show that
# "Run pipeline" never started; every other shape falls through to the venue.

def _payload(step_fields=None, *, job_count=1, step_count=1, drop_steps=False,
             job_started=None):
    step = {"name": "Run pipeline", **(step_fields or {})}
    job = {"name": "run", "started_at": job_started,
           "steps": [{"name": "Checkout", "status": "completed", "conclusion": "success",
                      "started_at": JOB_START.isoformat()}] + [dict(step) for _ in range(step_count)]}
    if drop_steps:
        del job["steps"]
    return {"jobs": [dict(job) for _ in range(job_count)]}


@pytest.mark.parametrize("label,fields", [
    ("skipped", {"status": "completed", "conclusion": "skipped", "started_at": None}),
    ("pending", {"status": "pending", "conclusion": None, "started_at": None}),
    ("queued", {"status": "queued", "conclusion": None, "started_at": None}),
    ("cancelled before start", {"status": "completed", "conclusion": "cancelled", "started_at": None}),
])
def test_positive_never_started_is_clear_without_asking_the_venue(label, fields):
    out, seen = _decide(_payload(fields), ConnectionError("must not be asked"))
    assert out["verdict"] == "clear", label
    assert "after" not in seen


FALL_THROUGH = [
    ("jobs API error", ConnectionError("api down")),
    ("payload without a job list", {"nope": 1}),
    ("payload not an object", ["jobs"]),
    ("no run job", _payload({"status": "pending", "started_at": None}, job_count=0)),
    ("two run jobs", _payload({"status": "pending", "started_at": None}, job_count=2)),
    ("run job without steps", _payload(drop_steps=True)),
    ("no Run pipeline step", _payload({"status": "pending", "started_at": None}, step_count=0)),
    ("two Run pipeline steps", _payload({"status": "pending", "started_at": None}, step_count=2)),
    ("started_at field missing", _payload({"status": "completed", "conclusion": "skipped"})),
    ("null start but failed", _payload({"status": "completed", "conclusion": "failure", "started_at": None})),
    ("null start but succeeded", _payload({"status": "completed", "conclusion": "success", "started_at": None})),
    ("null start but in progress", _payload({"status": "in_progress", "conclusion": None, "started_at": None})),
    ("null start, no status", _payload({"started_at": None})),
    ("step started", _jobs("cancelled")),
]


@pytest.mark.parametrize("label,jobs", FALL_THROUGH)
def test_any_doubt_falls_through_to_the_venue_and_an_order_disables(label, jobs):
    out, seen = _decide(jobs, [_order(ORDER)])
    assert "after" in seen, label                         # the venue WAS asked
    assert out["verdict"] == "disable" and out["this_run_orders"] == [ORDER]


@pytest.mark.parametrize("label,jobs", FALL_THROUGH)
def test_any_doubt_falls_through_to_the_venue_and_no_order_is_clear(label, jobs):
    out, seen = _decide(jobs, [])
    assert "after" in seen, label
    assert out["verdict"] == "clear"


def test_without_a_start_time_the_venue_window_is_the_lookback():
    out, seen = _decide(ConnectionError("api down"), [])
    assert seen["after"] == NOW - fs.LOOKBACK - fs.SKEW_MARGIN
    assert out["window_basis"].startswith("lookback")


def test_with_a_start_time_the_venue_window_opens_at_the_run_job():
    out, seen = _decide(_jobs("failure"), [])
    assert seen["after"] == JOB_START - fs.SKEW_MARGIN and out["window_basis"] == "run job start"


# ------------------------------------------------------------ attribution


def test_probe_orders_are_not_this_runs():
    probe = build_order_id(PROBE_BOOK_PREFIX + "SELL", THIS_CYCLE, 0)
    out, _ = _decide(_jobs(), [_order(probe)])
    assert out["verdict"] == "clear"


@pytest.mark.parametrize("coid", ["manual-order-1", "", "gpt-notacycle-01-a0",
                                  build_order_id("gpt", PREV_CYCLE, 3)])
def test_unattributable_order_in_the_window_disables(coid):
    """Unknown segment, no id, unparseable cycle, or a book order from an older
    cycle submitted inside the window: nothing rules it out."""
    out, _ = _decide(_jobs(), [_order(coid)])
    assert out["verdict"] == "disable" and out["unattributable_orders"]


def test_order_inside_the_skew_margin_counts_as_this_run():
    early = (JOB_START - timedelta(seconds=30)).strftime("%Y%m%dT%H%M%SZ")
    out, _ = _decide(_jobs(), [_order(build_order_id("claude", early, 0))])
    assert out["verdict"] == "disable" and out["this_run_orders"]


def test_simulator_mode_reaches_no_venue():
    out, seen = _decide(_jobs(), ConnectionError("not asked"), mode="paper")
    assert out["verdict"] == "clear" and "after" not in seen


class _FakeClient:
    def __init__(self, pages):
        self.pages = pages if isinstance(pages, list) else [pages]
        self.calls = []

    def _request(self, method, path, params=None):
        self.calls.append(params)
        return self.pages.pop(0) if self.pages else []


def test_fetch_orders_paginates_by_submitted_at():
    first = [{"client_order_id": f"o{i}", "submitted_at": (JOB_START + timedelta(seconds=i)).isoformat()}
             for i in range(fs.ORDER_PAGE_LIMIT)]
    client = _FakeClient([first, [{"client_order_id": "last",
                                   "submitted_at": (JOB_START + timedelta(hours=1)).isoformat()}]])
    got = fs.fetch_orders(client, JOB_START)
    assert len(got) == fs.ORDER_PAGE_LIMIT + 1
    assert client.calls[0]["status"] == "all"
    assert client.calls[1]["after"] == first[-1]["submitted_at"]


# --------------------------------------------- the workflow wiring and the act

# Parsed as text: the CI test environment installs only the production lock
# plus pytest, and PyYAML is in neither. The workflow is the file GitHub reads,
# so its exact text is what these assertions pin.
WF_TEXT = (ROOT / ".github/workflows/intraday.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
JOB_TEXT = WF_TEXT[WF_TEXT.index("\n  fail-stop:\n"):]


def _step(job_text: str, marker: str) -> str:
    """The text of the step containing `marker`, up to the next step."""
    i = job_text.index(marker)
    start = job_text.rindex("\n      - ", 0, i)
    nxt = job_text.find("\n      - ", i)
    return job_text[start:nxt if nxt != -1 else len(job_text)]


def _env(step_text: str) -> dict[str, str]:
    out = {}
    for line in step_text.splitlines():
        m = re.match(r"^          ([A-Z_]+): (.+)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def test_job_runs_on_failure_and_on_cancellation():
    head = JOB_TEXT[:JOB_TEXT.index("    steps:")]
    assert "    needs: [run]\n" in head
    cond = re.search(r"^    if: (.+)$", head, re.M).group(1)
    assert "always()" in cond
    assert "needs.run.result == 'failure'" in cond and "needs.run.result == 'cancelled'" in cond


def test_determine_step_cannot_stop_the_disable_step():
    det = _step(JOB_TEXT, "name: Determine whether the run submitted orders")
    assert "        continue-on-error: true\n" in det
    dis = _step(JOB_TEXT, "name: Disable the workflow unless the run provably submitted nothing")
    assert "        if: always()\n" in dis
    assert "VERDICT: ${{ steps.determine.outputs.verdict }}" in dis


def test_run_job_alpaca_env_is_mirrored():
    run_job = WF_TEXT[WF_TEXT.index("\n  run:\n"):WF_TEXT.index("\n  alerting-preflight:\n")]
    run_env = _env(_step(run_job, "name: Run pipeline"))
    det_env = _env(_step(JOB_TEXT, "id: determine"))
    alpaca = {k: v for k, v in run_env.items() if k.startswith("ALPACA_")}
    assert alpaca and all(det_env.get(k) == v for k, v in alpaca.items())


def _disable_script() -> str:
    step = _step(JOB_TEXT, "id: disable")
    body = step[step.index("        run: |\n") + len("        run: |\n"):]
    return "\n".join(line[10:] for line in body.splitlines()) + "\n"


BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("verdict,disables", [
    ("clear", False),
    ("disable", True),
    ("", True),            # the determine step crashed or never wrote a verdict
    ("CLEAR", True),       # anything but exactly "clear"
    ("clear ", True),
])
def test_disable_step_acts_on_everything_but_exactly_clear(tmp_path, verdict, disables):
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "curl.log"
    (stub / "curl").write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log.as_posix()}"\n'
                               'case "$*" in *"/runs?status="*) echo \'{"workflow_runs":[]}\';; esac\n',
                               encoding="utf-8", newline="\n")
    (stub / "curl").chmod(0o755)
    (stub / "sleep").write_text("#!/usr/bin/env bash\n:\n", encoding="utf-8", newline="\n")
    (stub / "sleep").chmod(0o755)
    out = tmp_path / "gh_output"
    script = tmp_path / "step.sh"
    script.write_text(_disable_script(), encoding="utf-8", newline="\n")
    env = dict(os.environ, VERDICT=verdict, GH_TOKEN="t", REPO="o/r", SELF_RUN="1",
               GITHUB_OUTPUT=out.as_posix(),
               PATH=f"{stub.as_posix()}{os.pathsep}{os.environ.get('PATH', '')}")
    r = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    outputs = out.read_text(encoding="utf-8")
    if disables:
        assert "-X PUT" in calls and "/workflows/intraday.yml/disable" in calls
        assert "acted=true" in outputs and "disabled=true" in outputs
    else:
        assert calls == "" and "acted=false" in outputs


# ------------------------------------- hardening (approved 2026-11-20, probe H Q3)
# Probe H showed a run queued behind the failed one still starts after the
# disable. A failed cancel, an unreadable listing, or a run still queued at the
# re-query is therefore a failure that turns the job red and is paged, not a
# warning. The stub curl below serves a queue per pass: the first four listing
# calls are the cancel pass, the next four the re-query.

_STUB_CURL = """#!/usr/bin/env bash
echo "$@" >> "$STUB_DIR/curl.log"
case "$*" in
  *"/runs?status="*)
    n=$(( $(cat "$STUB_DIR/n" 2>/dev/null || echo 0) + 1 )); echo $n > "$STUB_DIR/n"
    [ "${STUB_LIST_FAIL:-0}" = 1 ] && exit 22
    if [ $n -le 4 ]; then ids="$STUB_FIRST"; else ids="$STUB_AFTER"; fi
    case "$*" in *"status=queued"*) ;; *) ids="";; esac
    printf '{"workflow_runs":['; sep=""
    for i in $ids; do printf '%s{"id":%s}' "$sep" "$i"; sep=","; done
    printf ']}'
    ;;
  *"/cancel"*) [ "${STUB_CANCEL_FAIL:-0}" = 1 ] && exit 22; exit 0 ;;
esac
"""


def _act(tmp_path, *, first="", after="", list_fail=False, cancel_fail=False):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(_STUB_CURL, encoding="utf-8", newline="\n")
    (stub / "sleep").write_text("#!/usr/bin/env bash\n:\n", encoding="utf-8", newline="\n")
    # jq stand-in for the one filter the step uses, failing on bad JSON as jq
    # does; it keeps the test identical on machines without jq.
    (stub / "jq_impl.py").write_text(
        "import json, sys\n"
        "assert sys.argv[1:] == ['-r', '.workflow_runs[].id'], sys.argv\n"
        "for run in json.load(sys.stdin)['workflow_runs']:\n"
        "    sys.stdout.buffer.write(f\"{run['id']}\\n\".encode())\n",
        encoding="utf-8", newline="\n")
    (stub / "jq").write_text(f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" '
                             f'"{(stub / "jq_impl.py").as_posix()}" "$@"\n',
                             encoding="utf-8", newline="\n")
    for f in ("curl", "sleep", "jq"):
        (stub / f).chmod(0o755)
    out = tmp_path / "gh_output"
    script = tmp_path / "step.sh"
    script.write_text(_disable_script(), encoding="utf-8", newline="\n")
    env = dict(os.environ, VERDICT="disable", GH_TOKEN="t", REPO="o/r", SELF_RUN="7",
               GITHUB_OUTPUT=out.as_posix(), STUB_DIR=tmp_path.as_posix(),
               STUB_FIRST=first, STUB_AFTER=after,
               STUB_LIST_FAIL="1" if list_fail else "0", STUB_CANCEL_FAIL="1" if cancel_fail else "0",
               PATH=f"{stub.as_posix()}{os.pathsep}{os.environ.get('PATH', '')}")
    r = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                       env=env, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    log = (tmp_path / "curl.log").read_text(encoding="utf-8")
    return r, outputs, log


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_queued_runs_are_cancelled_and_a_clean_requery_is_green(tmp_path):
    r, out, log = _act(tmp_path, first="41 7 42", after="")
    assert r.returncode == 0, r.stderr
    assert "/runs/41/cancel" in log and "/runs/42/cancel" in log
    assert "/runs/7/cancel" not in log                       # never this run
    assert (out["cancel_failed"], out["remaining"]) == ("false", "")
    assert log.count("/runs?status=") == 8                   # one pass, one re-query


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_a_failed_cancel_is_a_failure_not_a_warning(tmp_path):
    r, out, log = _act(tmp_path, first="41", after="41", cancel_fail=True)
    assert r.returncode == 0, r.stderr                       # the page and red steps still run
    assert out["cancel_failed"] == "true" and out["remaining"] == "41"
    assert "::error::Fail-stop could not cancel queued run 41" in r.stdout
    assert "::warning::" not in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_a_run_still_queued_at_the_requery_is_named(tmp_path):
    r, out, _ = _act(tmp_path, first="41", after="43 7")
    assert out["cancel_failed"] == "false" and out["remaining"] == "43"
    assert "still queued after the cancel loop: 43" in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_an_unlistable_queue_counts_as_not_clear(tmp_path):
    r, out, _ = _act(tmp_path, list_fail=True)
    assert r.returncode == 0, r.stderr
    assert out["cancel_failed"] == "true"
    assert out["remaining"] == "unlisted:queued unlisted:pending unlisted:waiting unlisted:requested"


def test_red_step_covers_disable_cancel_and_queue():
    red = _step(JOB_TEXT, "name: Red the job if the disable, a cancel or the queue check failed")
    cond = red[red.index("if: >-"):red.index("run: exit 1")]
    assert "always()" in cond and "steps.disable.outputs.acted == 'true'" in cond
    for clause in ("steps.disable.outputs.disabled != 'true'",
                   "steps.disable.outputs.cancel_failed != 'false'",   # unset (step died) is red too
                   "steps.disable.outputs.remaining != ''"):
        assert clause in cond
    assert "        run: exit 1" in red


def test_the_page_carries_the_cancel_and_queue_outcome():
    page = _step(JOB_TEXT, "name: Page")
    assert "        if: always() && steps.disable.outputs.acted == 'true'\n" in page
    ctx = _env(page)["FAILED_CONTEXT"]
    assert "cancel_failed=${{ steps.disable.outputs.cancel_failed }}" in ctx
    assert "remaining=${{ steps.disable.outputs.remaining || 'none' }}" in ctx
