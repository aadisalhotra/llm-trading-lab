"""The install-nothing CI entry points must import with the standard library only.

Three workflow steps deliberately run with no pip install, so an email or
dependency fault can never stop them reporting it:

  * alerting-preflight      scripts/check_alerting_config.py --channel daily
                            (tests.yml, intraday.yml)
  * notify-failure          scripts/notify_failure.py (tests.yml, intraday.yml)
  * the staleness monitor   scripts/check_dashboard_staleness.py (intraday.yml chain)

2026-11-12: the price-validity guard imported its predicates into
src/alerts/events.py from src/data/price_validity.py. Importing anything under
src.data runs src/data/__init__.py, which imports pandas, so all three died
with ModuleNotFoundError on the review branch (Tests #45, alerting-preflight
red). The local suite missed it because pandas is always installed here.

Each script runs here in a subprocess with an import hook that refuses every
non-standard-library module, from a scratch copy of src/, scripts/ and config/
(so nothing is written into the repo), with outbound sockets refused and no
mail credentials (so nothing is sent).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# tzdata is the IANA database as a data-only package. Linux runners use the
# system zoneinfo and never import it; Windows has no system database, so
# zoneinfo needs it to resolve America/New_York. It carries no code path.
RUNNER = r'''
import runpy, socket, sys

ALLOWED_NON_STDLIB = {"src", "scripts", "tzdata"}

class StdlibOnly:
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top in sys.stdlib_module_names or top in ALLOWED_NON_STDLIB or top.startswith("_"):
            return None
        raise ModuleNotFoundError(f"No module named {top!r} (stdlib-only CI job)", name=top)

sys.meta_path.insert(0, StdlibOnly())

def _refuse(*a, **k):
    raise OSError("network refused in the stdlib-only entry-point test")
socket.socket.connect = _refuse
socket.create_connection = _refuse

script, sys.argv = sys.argv[1], sys.argv[1:]
runpy.run_path(script, run_name="__main__")
'''


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    root = tmp_path_factory.mktemp("stdlib_only")
    for d in ("src", "scripts", "config"):
        shutil.copytree(ROOT / d, root / d,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / "stdlib_runner.py").write_text(RUNNER, encoding="utf-8")
    return root


def _run(sandbox: Path, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GMAIL", "ALERT_", "COMPETITOR_", "PYTHON"))}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-I", str(sandbox / "stdlib_runner.py"), *args],
        cwd=sandbox, env=env, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace")


def _assert_no_import_failure(proc):
    out = proc.stdout + proc.stderr
    assert "ModuleNotFoundError" not in out and "ImportError" not in out, out[-2000:]


def test_alerting_preflight_runs_stdlib_only(sandbox):
    proc = _run(sandbox, "scripts/check_alerting_config.py", "--channel", "daily",
                GMAIL_ADDRESS="preflight@example.com", GMAIL_APP_PASSWORD="x",
                ALERT_RECIPIENTS="ops@example.com")
    _assert_no_import_failure(proc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "can deliver" in proc.stdout + proc.stderr


def test_failure_notifier_runs_stdlib_only(sandbox):
    # No credentials: send_email takes its logged not-sent path, nothing leaves.
    proc = _run(sandbox, "scripts/notify_failure.py", FAILURE_SUMMARY="stdlib-only test")
    _assert_no_import_failure(proc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CI failure notification NOT sent" in proc.stdout


def test_staleness_monitor_runs_stdlib_only(sandbox):
    # --self-test: the same imports as the live monitor, with no fetch.
    proc = _run(sandbox, "scripts/check_dashboard_staleness.py", "--self-test")
    _assert_no_import_failure(proc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL PASS" in proc.stdout


def test_the_hook_actually_blocks_a_third_party_import(sandbox, tmp_path):
    # Control: without this the three passes could mean the hook never armed.
    probe = sandbox / "scripts" / "_probe_pandas.py"
    probe.write_text("import pandas\n", encoding="utf-8")
    try:
        proc = _run(sandbox, "scripts/_probe_pandas.py")
    finally:
        probe.unlink()
    assert proc.returncode != 0
    assert "No module named 'pandas' (stdlib-only CI job)" in proc.stderr
