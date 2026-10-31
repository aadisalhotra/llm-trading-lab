"""Rendered-prompt hash check -- per-cycle observer of the frozen v4 prompt.

Each intraday cycle renders the golden fixture (prompt_golden_fixture.py)
with the code, config and dependencies the cycle is running, hashes each
model-facing text, and compares against the reference rendered from the
frozen commit 00557e557 (data/pre_registration/prompt_golden_reference_v4.json,
built by scripts/build_prompt_golden_reference.py). One JSON line per cycle
goes to data/prompt_integrity/YYYY-MM.jsonl, which the workflow commits with
the cycle's decision logs.

Classification (hub ruling 2026-10-30): infrastructure. Read-only, it observes
decision generation without touching it, consumes no monthly change slot and
opens no version boundary -- the same class as the 2026-08-02 SDK migration.

NON-BLOCKING, ABSOLUTELY. It logs and alerts; it never halts, retries or
alters a cycle. The pipeline calls it only after every model has decided and
the boundary is marked, through run_prompt_integrity_check_bounded(), which
runs it on a daemon thread with a hard time limit. Every failure inside it --
reference missing, render error, log write failure, alert failure -- is caught
here and reported, never raised. An instrument that can break a cycle is worse
than no instrument.

Statuses:
  match                 every component hash equals the reference
  mismatch              at least one differs: the frozen prompt is no longer
                        what the cycle would send (CRITICAL alert)
  render_error          the fixture could not be rendered (WARN)
  fixture_changed       the fixture file no longer matches the one the
                        reference was built from; the comparison would test
                        the instrument, not the prompt (WARN)
  reference_unreadable  the reference file is missing or malformed (WARN)
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..alerts.alerter import send_alert
from ..config_loader import DATA_DIR
from . import prompt_golden_fixture as fixture

logger = logging.getLogger(__name__)

# Hub dispatch 2026-10-30: the check arms 2026-10-31; first armed session
# Monday 2026-11-02. Cycles dated before this record nothing.
ARMED_FROM = date(2026, 10, 31)

REFERENCE_PATH = DATA_DIR / "pre_registration" / "prompt_golden_reference_v4.json"
LOG_DIR = DATA_DIR / "prompt_integrity"
FIXTURE_PATH = Path(fixture.__file__)

# Generous against a render that takes well under a second. Past it the
# pipeline carries on and the overrun is itself reported.
TIME_LIMIT_SECONDS = 20.0


def sha256_lf(b: bytes) -> str:
    """sha256 of EOL-normalized bytes (the repo checks out CRLF on Windows)."""
    return hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()


def text_sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def combined_sha256(component_hashes: dict[str, str]) -> str:
    """One hash over the components in fixed order, for a one-line comparison."""
    joined = "\n".join(f"{k}:{component_hashes[k]}" for k in fixture.COMPONENTS)
    return text_sha256(joined)


def first_difference(expected: str, actual: str) -> dict[str, Any]:
    """Locate the first differing line, for a diagnosable alert."""
    e, a = expected.split("\n"), actual.split("\n")
    for i in range(max(len(e), len(a))):
        el = e[i] if i < len(e) else None
        al = a[i] if i < len(a) else None
        if el != al:
            return {"line": i + 1, "expected": el, "actual": al,
                    "expected_lines": len(e), "actual_lines": len(a)}
    return {"line": None, "expected_lines": len(e), "actual_lines": len(a)}


def _environment() -> dict[str, str]:
    env = {"python": platform.python_version()}
    for mod in ("pandas", "numpy"):
        try:
            env[mod] = __import__(mod).__version__
        except Exception:  # an observer field, never a failure
            env[mod] = "unavailable"
    return env


def evaluate(reference: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render the fixture and compare it to the reference. Never raises."""
    record: dict[str, Any] = {"environment": _environment()}
    try:
        if reference is None:
            reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
        ref_hashes = reference["component_sha256"]
        record["reference_commit"] = reference["frozen_commit"]
        record["reference_combined_sha256"] = reference["combined_sha256"]
    except Exception as e:
        record.update(status="reference_unreadable", error=f"{type(e).__name__}: {e}")
        return record

    try:
        fixture_hash = sha256_lf(FIXTURE_PATH.read_bytes())
    except Exception as e:
        record.update(status="render_error", error=f"fixture unreadable: {type(e).__name__}: {e}")
        return record
    record["fixture_sha256_lf"] = fixture_hash
    if fixture_hash != reference.get("fixture_sha256_lf"):
        record.update(status="fixture_changed",
                      reference_fixture_sha256_lf=reference.get("fixture_sha256_lf"))
        return record

    try:
        texts = fixture.render_golden()
    except Exception as e:
        record.update(status="render_error", error=f"{type(e).__name__}: {e}")
        return record

    hashes = {k: text_sha256(texts[k]) for k in fixture.COMPONENTS}
    record["component_sha256"] = hashes
    record["combined_sha256"] = combined_sha256(hashes)
    changed = [k for k in fixture.COMPONENTS if hashes[k] != ref_hashes.get(k)]
    if not changed:
        record["status"] = "match"
        return record
    record["status"] = "mismatch"
    record["mismatched_components"] = changed
    ref_texts = reference.get("rendered_text") or {}
    record["first_difference"] = {
        k: first_difference(ref_texts[k], texts[k]) for k in changed if k in ref_texts}
    return record


def _append_log(record: dict[str, Any], run_date: datetime) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{run_date:%Y-%m}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _alert(record: dict[str, Any]) -> None:
    status = record["status"]
    if status == "match":
        return
    if status == "mismatch":
        severity, title = "CRITICAL", "Frozen prompt drift: rendered prompt differs from reference"
        body = (f"The golden-fixture render no longer matches the reference frozen at "
                f"{record.get('reference_commit')}. Changed: "
                f"{', '.join(record['mismatched_components'])}. First difference: "
                f"{json.dumps(record.get('first_difference'), sort_keys=True)}. "
                f"The cycle was NOT altered; this is an observation.")
    else:
        severity, title = "WARN", f"Prompt hash check could not compare ({status})"
        body = (f"The rendered-prompt hash check did not reach a verdict this cycle: "
                f"{status}. {record.get('error', '')} The cycle was NOT altered.")
    send_alert(severity, title, body,
               {k: record.get(k) for k in ("status", "boundary", "mismatched_components",
                                           "reference_commit", "environment")},
               kind="prompt_integrity", dedup_key=f"prompt_integrity:{status}")


def run_prompt_integrity_check(run_date: datetime) -> dict[str, Any] | None:
    """Evaluate, log and alert for one cycle. Returns the record; never raises."""
    try:
        if run_date.date() < ARMED_FROM:
            logger.debug("Prompt hash check not armed before %s", ARMED_FROM)
            return None
        record = evaluate()
        record["boundary"] = run_date.isoformat()
        record["checked_at"] = datetime.now(timezone.utc).isoformat()
        level = logging.INFO if record["status"] == "match" else logging.ERROR
        logger.log(level, "Prompt hash check: %s (combined %s, reference %s)",
                   record["status"], record.get("combined_sha256"),
                   record.get("reference_combined_sha256"))
        try:
            _append_log(record, run_date)
        except Exception as e:
            logger.exception("Prompt hash check: log write failed: %s", e)
        try:
            _alert(record)
        except Exception as e:
            logger.exception("Prompt hash check: alert failed: %s", e)
        return record
    except Exception as e:
        logger.exception("Prompt hash check failed unexpectedly: %s", e)
        return None


def run_prompt_integrity_check_bounded(run_date: datetime,
                                       time_limit: float = TIME_LIMIT_SECONDS) -> None:
    """The pipeline's entry point: the check on a daemon thread, joined with a
    hard limit. Past the limit the pipeline proceeds and the thread dies with
    the process. Never raises."""
    try:
        t = threading.Thread(target=run_prompt_integrity_check, args=(run_date,),
                             name="prompt-integrity", daemon=True)
        t.start()
        t.join(time_limit)
        if t.is_alive():
            logger.error("Prompt hash check exceeded %.0fs; pipeline continues without it",
                         time_limit)
            try:
                send_alert("WARN", "Prompt hash check timed out",
                           f"The rendered-prompt hash check did not finish within "
                           f"{time_limit:.0f}s. The cycle was NOT altered.",
                           {"boundary": run_date.isoformat()},
                           kind="prompt_integrity", dedup_key="prompt_integrity:timeout")
            except Exception as e:
                logger.exception("Prompt hash check: timeout alert failed: %s", e)
    except Exception as e:
        logger.exception("Prompt hash check could not start: %s", e)
