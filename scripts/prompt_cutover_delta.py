"""Pre-compute a config change's effect on the frozen prompt, before it lands.

Renders the rendered-prompt hash check's golden fixture
(src/logging/prompt_golden_fixture.py) against CANDIDATE settings and universe
files -- e.g. the post-cutover config/settings.json, staged but not yet
committed -- and compares the four hashes to the frozen reference
(data/pre_registration/prompt_golden_reference_v4.json). Hub ruling
2026-10-30: run before the venue-cutover mode flip, and report the four hashes
either way. A match means the change is prompt-neutral and the per-cycle check
will not fire; a difference is a prompt change inside the config change, and
goes to the hub with the differing lines named before it lands.

Two controls run first, every time, so the verdict is about the candidate and
not about this script (CLAUDE.md rule 9):
  positive  the CURRENT config files through the same override path must
            reproduce the reference exactly;
  negative  the current settings with max_positions changed must differ, in
            decision_user only.
If either control fails, no verdict is given.

It also lists every settings key the render actually reads, so a reviewer can
see which cutover edits could matter at all.

Nothing is written to config/. Usage:
  python scripts/prompt_cutover_delta.py --settings <candidate settings.json>
      [--universe <candidate universe.json>] [--out report.json]
Exit: 0 match, 1 differs, 2 a control failed.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.config_loader as cl  # noqa: E402
import src.prompt_builder as pb  # noqa: E402
from src.logging import prompt_golden_fixture as fixture  # noqa: E402
from src.logging.prompt_integrity import (  # noqa: E402
    REFERENCE_PATH, combined_sha256, first_difference, sha256_lf, text_sha256)

logger = logging.getLogger("prompt_cutover_delta")


@contextmanager
def config_override(settings: dict[str, Any], universe: dict[str, Any]):
    """Point every loader the render uses at the given dicts, then restore.
    prompt_builder imported the loaders by name, so both modules are patched."""
    saved = {(m, n): getattr(m, n) for m in (cl, pb) for n in ("load_settings", "load_universe")}
    try:
        for m in (cl, pb):
            m.load_settings = lambda: copy.deepcopy(settings)
            m.load_universe = lambda: copy.deepcopy(universe)
        yield
    finally:
        for (m, n), f in saved.items():
            setattr(m, n, f)


def render_hashes(settings: dict, universe: dict) -> tuple[dict[str, str], dict[str, str]]:
    with config_override(settings, universe):
        texts = fixture.render_golden()
    return texts, {k: text_sha256(texts[k]) for k in fixture.COMPONENTS}


class _Tracking(dict):
    """A dict that records every key path read from it."""

    def __init__(self, data: dict, seen: set, prefix: str = ""):
        super().__init__(data)
        self._seen, self._prefix = seen, prefix

    def _wrap(self, key, value):
        path = f"{self._prefix}{key}"
        self._seen.add(path)
        return _Tracking(value, self._seen, path + ".") if isinstance(value, dict) else value

    def __getitem__(self, key):
        return self._wrap(key, super().__getitem__(key))

    def get(self, key, default=None):
        return self._wrap(key, super().get(key, default)) if key in self else (
            self._seen.add(f"{self._prefix}{key}") or default)


def settings_keys_read(settings: dict, universe: dict) -> list[str]:
    """Separate render whose only purpose is to record which keys are read; its
    texts are discarded, so tracking cannot touch a hashed render."""
    seen: set[str] = set()
    saved = (pb.load_settings, cl.load_settings, pb.load_universe, cl.load_universe)
    try:
        pb.load_settings = cl.load_settings = lambda: _Tracking(copy.deepcopy(settings), seen)
        pb.load_universe = cl.load_universe = lambda: copy.deepcopy(universe)
        fixture.render_golden()
    finally:
        pb.load_settings, cl.load_settings, pb.load_universe, cl.load_universe = saved
    return sorted(seen)


def compare(reference: dict, texts: dict, hashes: dict) -> dict[str, Any]:
    ref = reference["component_sha256"]
    changed = [k for k in fixture.COMPONENTS if hashes[k] != ref[k]]
    return {
        "components": {k: {"candidate": hashes[k], "frozen": ref[k], "match": hashes[k] == ref[k]}
                       for k in fixture.COMPONENTS},
        "combined": {"candidate": combined_sha256(hashes), "frozen": reference["combined_sha256"]},
        "differing": changed,
        "first_difference": {k: first_difference(reference["rendered_text"][k], texts[k])
                             for k in changed},
    }


def run(settings_path: Path, universe_path: Path) -> tuple[int, dict[str, Any]]:
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    current_s = json.loads((cl.CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    current_u = json.loads((cl.CONFIG_DIR / "universe.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {"reference_commit": reference["frozen_commit"]}

    if reference["fixture_sha256_lf"] != sha256_lf(Path(fixture.__file__).read_bytes()):
        report["control_failure"] = "fixture differs from the one the reference was built from"
        return 2, report

    texts, hashes = render_hashes(current_s, current_u)
    pos = compare(reference, texts, hashes)
    neg_s = copy.deepcopy(current_s)
    neg_s["portfolio_rules"]["max_positions"] = int(neg_s["portfolio_rules"]["max_positions"]) + 1
    neg = compare(reference, *render_hashes(neg_s, current_u))
    report["controls"] = {"positive_current_config_matches": not pos["differing"],
                          "negative_max_positions_detected": neg["differing"] == ["decision_user"]}
    if not all(report["controls"].values()):
        report["control_failure"] = {"positive": pos, "negative": neg}
        return 2, report

    cand_s = json.loads(settings_path.read_text(encoding="utf-8"))
    cand_u = json.loads(universe_path.read_text(encoding="utf-8"))
    report["candidate"] = {"settings": str(settings_path), "universe": str(universe_path),
                           "settings_sha256_lf": sha256_lf(settings_path.read_bytes()),
                           "universe_sha256_lf": sha256_lf(universe_path.read_bytes())}
    report["settings_keys_read_by_render"] = settings_keys_read(cand_s, cand_u)
    report.update(compare(reference, *render_hashes(cand_s, cand_u)))
    report["verdict"] = "match" if not report["differing"] else "differs"
    return (0 if report["verdict"] == "match" else 1), report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", type=Path, required=True)
    ap.add_argument("--universe", type=Path, default=cl.CONFIG_DIR / "universe.json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    code, report = run(args.settings, args.universe)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if code == 2:
        logger.error("Control failed, no verdict: %s", json.dumps(report.get("control_failure"))[:2000])
        return code
    for k, c in report["components"].items():
        logger.info("%-17s candidate %s  frozen %s  %s", k, c["candidate"], c["frozen"],
                    "match" if c["match"] else "DIFFERS")
    logger.info("settings keys read by the render: %s", ", ".join(report["settings_keys_read_by_render"]))
    for k, d in report["first_difference"].items():
        logger.info("%s first difference at line %s:\n  frozen:    %r\n  candidate: %r",
                    k, d["line"], d.get("expected"), d.get("actual"))
    logger.info("VERDICT: %s", report["verdict"].upper())
    return code


if __name__ == "__main__":
    sys.exit(main())
