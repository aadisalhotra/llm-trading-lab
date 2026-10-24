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
through scripts/backfill_may_data_layer.verify — the committed, read-only
definition-of-done (RQ1 herding consistency, SPY single-source, RQ2/RQ3
supersession, performance integrity). Its RQ-consistency cross-check (the RQ1
herding matrix-wmean==scalar reconciliation) is unchanged; only the equity-curve
shakedown-fabrication flag literal was re-anchored (true -> false) per the Phase-A
equity-curve re-anchor ruling. The mechanized build must pass it.

EXACT LOCALLY, 1e-12 RELATIVE IN CI (hub ruling 2026-10-24). The strictness of
this gate differs by environment, and a green CI run is NOT proof of exactness:
  * locally (GITHUB_ACTIONS unset) every float must be bit-identical, as before;
  * in GitHub Actions a float pair passes if |a - b| <= 1e-12 * |b|, and every
    tolerated field is reported as a warning, so a green run states how many
    fields were not exact and by how much.
Why: on its first run (2026-10-24) the CI runner (Linux, CPython 3.11) differed
from the committed layer at the last ULP on 38 fields, max relative 2.9e-14.
The measured cause is the platform, not the package versions: production's
numpy 2.4.6 / pandas 3.1.0 reproduce May exactly on the local machine
(CPython 3.14), and Python 3.11's left-to-right built-in sum() (3.12 made float
sum() compensated), patched in-process, produces 6 last-ULP diffs of the same
magnitude (max relative 2.7e-14). That the 6 are among the CI's 38 is not
verified; the rest is attributed to platform floating point, not proven. The real
regression this gate exists for (2026-10-21) was ~2e-2 relative, ten orders of
magnitude above the tolerance. Integers, strings, booleans, list lengths and
keys stay exact everywhere. Registered: ledger reproduce_may_ci_tolerance_1e12.

Run:  python -m pytest tests/test_reproduce_may_data_layer.py -q
      python -m tests.test_reproduce_may_data_layer        # human-readable report
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys
import warnings

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


# Relative tolerance for float leaves, in CI only; exact (None) everywhere else.
CI_REL_TOL = 1e-12
REL_TOL = CI_REL_TOL if os.environ.get("GITHUB_ACTIONS") == "true" else None


def _tolerable(a, b) -> bool:
    """True if a differing pair is a float pair within REL_TOL (CI only). bool is
    an int subclass, so it is excluded explicitly; two ints are never tolerated."""
    if REL_TOL is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return False
    if not (isinstance(a, float) or isinstance(b, float)):
        return False
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=0.0)


def _excluded(path: str) -> bool:
    return path in EXCLUDE_EXACT or any(path.startswith(p) for p in EXCLUDE_PREFIX)


def _diff(a, b, path, out, tolerated=None):
    """Recursive structural diff (order-independent for dicts). Records every path
    where candidate `a` and committed `b` disagree, skipping excluded paths. Float
    leaves within REL_TOL (CI only) go to `tolerated` instead of `out`."""
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
                _diff(a[k], b[k], path + "." + str(k), out, tolerated)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(("LISTLEN", path, len(a), len(b)))
        else:
            for i in range(len(a)):
                _diff(a[i], b[i], f"{path}[{i}]", out, tolerated)
    else:
        if a != b:
            if _tolerable(a, b):
                if tolerated is not None:
                    tolerated.append((path, a, b, abs(a - b) / abs(b)))
            else:
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


def reproduce(tolerated=None):
    """Build May, overlay authored content, return (candidate, committed, diffs).
    In CI, float fields within REL_TOL are appended to `tolerated`."""
    candidate = build(MONTH)
    with open(COMMITTED, encoding="utf-8") as f:
        committed = json.load(f)
    with open(PRE_BACKFILL, encoding="utf-8") as f:
        prebf = json.load(f)
    _overlay_authored(candidate, prebf)
    diffs: list = []
    _diff(candidate, committed, "", diffs, tolerated)
    return candidate, committed, diffs


def run_definition_of_done(candidate: dict):
    """Run the UNCHANGED committed cross-check on the candidate. Returns
    (ok, issues, weighted, scalar)."""
    perf_before = {mk: dict(candidate["performance"][mk]) for mk in BACKFILL_MODELS
                   if candidate["performance"].get(mk) is not None}
    return backfill_verify(candidate, copy.deepcopy(perf_before))


def test_reproduce_may_data_layer_exact():
    """The mechanized build reproduces the committed May layer exactly (modulo the
    documented build-time / authored exclusions). In CI only, float fields within
    1e-12 relative pass and are reported as a warning."""
    tolerated: list = []
    _candidate, _committed, diffs = reproduce(tolerated)
    assert not diffs, "reproduce-May diffs:\n" + "\n".join(str(d) for d in diffs)
    if tolerated:
        worst = max(t[3] for t in tolerated)
        warnings.warn(f"reproduce-May NOT EXACT: {len(tolerated)} float field(s) passed "
                      f"under the CI tolerance (rel {REL_TOL:g}), max relative {worst:.3g}; "
                      f"first: {tolerated[0][0]}")


def test_reproduce_may_passes_definition_of_done():
    """The mechanized build passes the committed read-only cross-check verbatim."""
    candidate, _committed, _diffs = reproduce()
    ok, issues, _w, _s = run_definition_of_done(candidate)
    assert ok, "definition-of-done (backfill.verify) issues:\n" + "\n".join(issues)


def test_ci_tolerance_scope(monkeypatch):
    """The CI tolerance absorbs last-ULP float differences and nothing else; with
    it off (the local default) the same ULP difference is a failure."""
    mod = sys.modules[__name__]
    ulp = 0.08314833686418835          # a real CI-vs-local pair (2026-10-24)
    ulp_b = 0.08314833686418836
    cases = {"ulp": (ulp, ulp_b), "big": (1.0, 1.0 + 1e-10), "int": (3, 4),
             "bool": (True, False), "zero": (0.0, 1e-300), "str": ("a", "b")}
    a = {k: v[0] for k, v in cases.items()}
    b = {k: v[1] for k, v in cases.items()}

    monkeypatch.setattr(mod, "REL_TOL", None)
    out, tol = [], []
    _diff(a, b, "", out, tol)
    assert {d[1] for d in out} == {".ulp", ".big", ".int", ".bool", ".zero", ".str"}
    assert tol == []

    monkeypatch.setattr(mod, "REL_TOL", CI_REL_TOL)
    out, tol = [], []
    _diff(a, b, "", out, tol)
    assert {d[1] for d in out} == {".big", ".int", ".bool", ".zero", ".str"}
    assert [t[0] for t in tol] == [".ulp"]


if __name__ == "__main__":
    tolerated: list = []
    candidate, committed, diffs = reproduce(tolerated)
    print("REPRODUCE-MAY DIFF (modulo build-time + authored overlay)")
    print("-" * 70)
    if not diffs and not tolerated:
        print("  EXACT MATCH — 0 differences.")
    else:
        for d in diffs:
            print("  ", d)
        for t in tolerated:
            print("   TOLERATED (CI rel 1e-12):", t)
    ok, issues, w, s = run_definition_of_done(candidate)
    print("\nDEFINITION-OF-DONE (scripts/backfill_may_data_layer.verify; RQ-consistency cross-check unedited)")
    print("-" * 70)
    print(f"  RQ1 herding consistency: weighted={w} scalar={s}")
    print(f"  OVERALL: {'PASS' if ok else 'FAIL'}")
    if issues:
        for i in issues:
            print("   -", i)
    sys.exit(0 if (not diffs and ok) else 1)
