"""Generate requirements.txt -- the exact production lock -- from requirements.in.

Hub ruling 2026-10-24: production does not float. Before this lock the cron
installed from minimum-version requirements, so every release reached
production the day it shipped (pandas 3.1.0 on 2026-10-22, python-dateutil
3.0.0 on 2026-10-21, both unannounced to the lab). requirements.txt now pins
every package the production install contains, direct and transitive, and the
workflows install it with --no-deps followed by `pip check`: nothing outside
the lock can be pulled in, and a lock that is missing a dependency fails the
install loudly instead of silently fetching the newest release.

How the lock is resolved. pip resolves requirements.in for the PRODUCTION
target (ubuntu-latest x86_64, CPython 3.11, wheels only) with --dry-run, so
nothing is installed on this machine. pip picks Linux wheels for the target
but evaluates sys_platform / platform_system environment markers against the
HOST, so its report can contain Windows-only packages (colorama, via tqdm and
click). The closure is therefore re-walked from requirements.in under a Linux
CPython 3.11 marker environment, keeping exactly what the runner installs.

TARGET_PYTHON carries a micro version on purpose: Requires-Python can bound a
micro release (soupsieve 3.x requires >=3.11.5), and a bare "3.11" is read as
3.11.0, which silently resolves an older release than the runner installs.

Run:  python scripts/lock_requirements.py            # rewrite requirements.txt
      python scripts/lock_requirements.py --stdout   # print, write nothing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS_IN = os.path.join(ROOT, "requirements.in")
REQUIREMENTS_TXT = os.path.join(ROOT, "requirements.txt")

# The production runner (.github/workflows/*.yml: ubuntu-latest, setup-python
# "3.11"). Keep in step with the workflows.
TARGET_PYTHON = "3.11.14"
TARGET_PLATFORMS = ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64",
                    "manylinux2014_x86_64", "linux_x86_64", "any")
MARKER_ENV = {
    "python_version": "3.11",
    "python_full_version": TARGET_PYTHON,
    "implementation_version": TARGET_PYTHON,
    "os_name": "posix",
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "implementation_name": "cpython",
    "platform_release": "",
    "platform_version": "",
}

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(levelname)s %(message)s")
log = logging.getLogger("lock_requirements")


def read_direct() -> list[Requirement]:
    """The direct requirements declared in requirements.in."""
    with open(REQUIREMENTS_IN, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f]
    return [Requirement(ln) for ln in lines if ln and not ln.startswith("#")]


def resolve_report() -> dict:
    """pip's --dry-run resolution report for the production target."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = os.path.join(tmp, "report.json")
        cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
               "--quiet", "--disable-pip-version-check", "--report", report_path,
               "--python-version", TARGET_PYTHON, "--implementation", "cp",
               "--only-binary=:all:", "-r", REQUIREMENTS_IN]
        for plat in TARGET_PLATFORMS:
            cmd += ["--platform", plat]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"pip resolution failed (exit {proc.returncode}):\n{proc.stderr}")
        with open(report_path, encoding="utf-8") as f:
            return json.load(f)


def linux_closure(report: dict, direct: list[Requirement]) -> dict[str, str]:
    """{canonical name: version} for everything the Linux target installs,
    walked from the direct requirements under MARKER_ENV."""
    pkgs = {canonicalize_name(i["metadata"]["name"]): i["metadata"] for i in report["install"]}
    seen: set[tuple[str, frozenset]] = set()
    missing: set[str] = set()
    stack = [(canonicalize_name(r.name), frozenset(r.extras)) for r in direct]
    while stack:
        name, extras = stack.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        if name not in pkgs:
            missing.add(name)
            continue
        for spec in pkgs[name].get("requires_dist") or []:
            req = Requirement(spec)
            # A requirement applies if its marker holds for any requested extra
            # (or for no extra at all).
            if req.marker and not any(req.marker.evaluate({**MARKER_ENV, "extra": e})
                                      for e in (extras or {""})):
                continue
            stack.append((canonicalize_name(req.name), frozenset(req.extras)))
    if missing:
        # pip resolved under host markers; a Linux-only dependency it skipped
        # would be absent here. Refuse rather than emit an incomplete lock.
        raise RuntimeError(f"needed on the Linux target but absent from pip's report: {sorted(missing)}")
    names = {n for n, _ in seen}
    dropped = sorted(set(pkgs) - names)
    if dropped:
        log.info("dropped (host-marker only, not installed on the target): %s", dropped)
    return {n: pkgs[n]["version"] for n in sorted(names)}


def render(pins: dict[str, str], direct: list[Requirement], report: dict) -> str:
    display = {canonicalize_name(i["metadata"]["name"]): i["metadata"]["name"]
               for i in report["install"]}
    direct_names = {canonicalize_name(r.name) for r in direct}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = [
        "# PRODUCTION LOCK -- generated by scripts/lock_requirements.py; do not edit by hand.",
        "# Edit requirements.in (the direct dependencies), then regenerate.",
        f"# Resolved {stamp} for linux x86_64, CPython {TARGET_PYTHON}, wheels only.",
        "# Install with:  pip install --no-deps -r requirements.txt && pip check",
        "",
        "# direct (requirements.in)",
    ]
    out += [f"{display[n]}=={v}" for n, v in pins.items() if n in direct_names]
    out += ["", "# transitive"]
    out += [f"{display[n]}=={v}" for n, v in pins.items() if n not in direct_names]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdout", action="store_true", help="print the lock, write nothing")
    args = parser.parse_args()

    direct = read_direct()
    report = resolve_report()
    pins = linux_closure(report, direct)
    text = render(pins, direct, report)
    if args.stdout:
        sys.stdout.write(text)
    else:
        with open(REQUIREMENTS_TXT, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        log.info("wrote %s: %d pins (%d direct)", REQUIREMENTS_TXT, len(pins), len(direct))
    return 0


if __name__ == "__main__":
    sys.exit(main())
