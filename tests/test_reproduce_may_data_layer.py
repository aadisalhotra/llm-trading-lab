"""Reproduce-May regression test — the proof that the mechanized builder emits
the integrity/provenance layer natively.

Runs scripts/build_monthly_data_layer.build("2026-05") against May's raw inputs
(the committed decision logs + the Phase-A integrity ledger + the committed de-fab
overhang correction) and diffs the result against the committed, hand-walked
reports/monthly/2026-05/data_layer.json. It must match, modulo:

  * build-time fields            — report_meta.generated_at_utc (timestamp),
                                   report_meta.source_commit (the two-step field,
                                   emitted null by the builder),
                                   report_meta.provenance.* (HEAD-at-build commit +
                                   the native single-build provenance shape, which
                                   legitimately differs from May's 3-commit chain).
  * hand-authored Reports-chat   — profiles[].style_tag/risk_posture_tag/strengths/
    content (overlaid from         weaknesses and report_meta.methodology_notes.
    data_layer.pre_backfill.json)  profiles_page_frame. These are qualitative human
                                   judgments the builder cannot compute; they are
                                   overlaid from the pristine pre-backfill snapshot
                                   so the diff isolates the COMPUTED fields. For
                                   non-estimable models the builder suppresses the
                                   interpretive fields (null), so the overlay is
                                   applied only to estimable/performance-shown models.

After the structural diff, the candidate (with authored content overlaid) is run
through the UNCHANGED scripts/backfill_may_data_layer.verify — the committed,
read-only definition-of-done (RQ1 herding consistency, SPY single-source,
RQ2/RQ3 supersession, performance integrity). The mechanized build must pass it.

Run:  python -m pytest tests/test_reproduce_may_data_layer.py -q
      python -m tests.test_reproduce_may_data_layer        # human-readable report
"""
from __future__ import annotations

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.build_monthly_data_layer import build  # noqa: E402
from scripts.backfill_may_data_layer import (  # noqa: E402
    verify as backfill_verify,
    MODELS as BACKFILL_MODELS,
)

MONTH = "2026-05"
COMMITTED = os.path.join(ROOT, "reports", "monthly", MONTH, "data_layer.json")
PRE_BACKFILL = os.path.join(ROOT, "reports", "monthly", MONTH, "data_layer.pre_backfill.json")

AUTHORED_PROFILE_FIELDS = ("style_tag", "risk_posture_tag", "strengths", "weaknesses")

# Fields legitimately not reproducible by a pre-commit build (excluded from the
# strict structural diff and reported separately).
EXCLUDE_EXACT = {
    ".report_meta.generated_at_utc",   # build timestamp
    ".report_meta.source_commit",      # two-step: null at emit, reconciled post-commit
}
EXCLUDE_PREFIX = (
    ".report_meta.provenance",         # HEAD-at-build commit + native single-build shape
)


def _excluded(path: str) -> bool:
    return path in EXCLUDE_EXACT or any(path.startswith(p) for p in EXCLUDE_PREFIX)


def _diff(a, b, path, out):
    """Recursive structural diff (order-independent for dicts). Records every path
    where candidate `a` and committed `b` disagree, skipping excluded paths."""
    if _excluded(path):
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                out.append(("ONLY_CANDIDATE", path + "." + str(k)))
        for k in b:
            if k not in a:
                out.append(("ONLY_COMMITTED", path + "." + str(k)))
            else:
                _diff(a[k], b[k], path + "." + str(k), out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(("LISTLEN", path, len(a), len(b)))
        else:
            for i in range(len(a)):
                _diff(a[i], b[i], f"{path}[{i}]", out)
    else:
        if a != b:
            out.append(("CHANGED", path, a, b))


def _overlay_authored(candidate: dict, prebf: dict) -> dict:
    """Copy the Reports-chat-authored content (which the builder cannot compute)
    from the pristine pre-backfill snapshot onto the candidate, respecting the
    non-estimable interpretive suppression the builder applies."""
    # authored methodology-notes frame(s)
    mn_c = candidate["report_meta"].setdefault("methodology_notes", {})
    for k, v in prebf["report_meta"].get("methodology_notes", {}).items():
        if k not in mn_c:
            mn_c[k] = v
    # per-model interpretive fields (suppressed -> stay null for non_estimable)
    prebf_prof = {p["model"]: p for p in prebf["profiles"]}
    for prof in candidate["profiles"]:
        if prof.get("status") == "non_estimable":
            continue
        src = prebf_prof.get(prof["model"], {})
        for f in AUTHORED_PROFILE_FIELDS:
            if f in src:
                prof[f] = src[f]
    return candidate


def reproduce():
    """Build May, overlay authored content, return (candidate, committed, diffs)."""
    candidate = build(MONTH)
    with open(COMMITTED, encoding="utf-8") as f:
        committed = json.load(f)
    with open(PRE_BACKFILL, encoding="utf-8") as f:
        prebf = json.load(f)
    _overlay_authored(candidate, prebf)
    diffs: list = []
    _diff(candidate, committed, "", diffs)
    return candidate, committed, diffs


def run_definition_of_done(candidate: dict):
    """Run the UNCHANGED committed cross-check on the candidate. Returns
    (ok, issues, weighted, scalar)."""
    perf_before = {mk: dict(candidate["performance"][mk]) for mk in BACKFILL_MODELS
                   if candidate["performance"].get(mk) is not None}
    return backfill_verify(candidate, copy.deepcopy(perf_before))


def test_reproduce_may_data_layer_exact():
    """The mechanized build reproduces the committed May layer exactly (modulo the
    documented build-time / authored exclusions)."""
    _candidate, _committed, diffs = reproduce()
    assert not diffs, "reproduce-May diffs:\n" + "\n".join(str(d) for d in diffs)


def test_reproduce_may_passes_definition_of_done():
    """The mechanized build passes the committed read-only cross-check verbatim."""
    candidate, _committed, _diffs = reproduce()
    ok, issues, _w, _s = run_definition_of_done(candidate)
    assert ok, "definition-of-done (backfill.verify) issues:\n" + "\n".join(issues)


if __name__ == "__main__":
    candidate, committed, diffs = reproduce()
    print("REPRODUCE-MAY DIFF (modulo build-time + authored overlay)")
    print("-" * 70)
    if not diffs:
        print("  EXACT MATCH — 0 differences.")
    else:
        for d in diffs:
            print("  ", d)
    ok, issues, w, s = run_definition_of_done(candidate)
    print("\nDEFINITION-OF-DONE (unedited scripts/backfill_may_data_layer.verify)")
    print("-" * 70)
    print(f"  RQ1 herding consistency: weighted={w} scalar={s}")
    print(f"  OVERALL: {'PASS' if ok else 'FAIL'}")
    if issues:
        for i in issues:
            print("   -", i)
    sys.exit(0 if (not diffs and ok) else 1)
