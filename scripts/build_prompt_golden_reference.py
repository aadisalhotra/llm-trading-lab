"""Build the reference for the rendered-prompt hash check from the frozen commit.

Renders src/logging/prompt_golden_fixture.py (from the CURRENT tree) against a
temporary detached git worktree of the frozen commit, so the prompt builder,
templates, config and universe are the frozen ones, not HEAD's. Writes
data/pre_registration/prompt_golden_reference_v4.json with the four
model-facing texts, their sha256s, the combined hash, the fixture's own hash
and the rendering environment.

It also renders the same fixture against the current tree and refuses to write
if the two differ: at build time the current tree must render the frozen
prompt, or the reference would be born mismatched.

The render runs in a child interpreter whose working directory is the
worktree, so `import src` resolves there. The fixture file is loaded by path,
because it does not exist at the frozen commit.

Usage:
  python scripts/build_prompt_golden_reference.py            # build + write
  python scripts/build_prompt_golden_reference.py --check    # compare only
"""
from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.logging.prompt_golden_fixture import COMPONENTS  # noqa: E402
from src.logging.prompt_integrity import (  # noqa: E402
    REFERENCE_PATH, combined_sha256, sha256_lf, text_sha256)

FROZEN_COMMIT = "00557e557dd3cf9bbd911ac6b362ff8370d1e6fb"
FIXTURE_PATH = ROOT / "src" / "logging" / "prompt_golden_fixture.py"

logger = logging.getLogger("build_prompt_golden_reference")

# Runs in the child: cwd (the tree under test) is sys.path[0] for `-c`.
_CHILD = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("prompt_golden_fixture", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
import src.prompt_builder as pb
texts = mod.render_golden()
sys.stdout.buffer.write(json.dumps({"texts": texts, "prompt_builder": pb.__file__}).encode("utf-8"))
"""


def render_in(tree: Path) -> dict:
    proc = subprocess.run([sys.executable, "-c", _CHILD, str(FIXTURE_PATH)],
                          cwd=tree, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"render in {tree} failed:\n{proc.stderr.decode('utf-8', 'replace')}")
    out = json.loads(proc.stdout.decode("utf-8"))
    # Prove the child rendered the tree we meant, not an import from elsewhere.
    if not Path(out["prompt_builder"]).resolve().is_relative_to(tree.resolve()):
        raise RuntimeError(f"child imported {out['prompt_builder']}, outside {tree}")
    return out["texts"]


def render_at_commit(commit: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="golden_ref_") as tmp:
        wt = Path(tmp) / "frozen"
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), commit],
                       check=True, capture_output=True)
        try:
            return render_in(wt)
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)],
                           check=False, capture_output=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", default=FROZEN_COMMIT)
    ap.add_argument("--check", action="store_true", help="compare only; write nothing")
    args = ap.parse_args()

    frozen = render_at_commit(args.commit)
    current = render_in(ROOT)
    hashes = {k: text_sha256(frozen[k]) for k in COMPONENTS}
    current_hashes = {k: text_sha256(current[k]) for k in COMPONENTS}
    for k in COMPONENTS:
        logger.info("%-17s frozen %s  current %s  %s", k, hashes[k][:16], current_hashes[k][:16],
                    "same" if hashes[k] == current_hashes[k] else "DIFFERENT")
    if hashes != current_hashes:
        logger.error("Current tree does not render the frozen prompt; refusing to write.")
        return 1

    reference = {
        "frozen_commit": args.commit,
        "built_from": "git worktree of frozen_commit; fixture loaded from the current tree by path",
        "fixture_path": "src/logging/prompt_golden_fixture.py",
        "fixture_sha256_lf": sha256_lf(FIXTURE_PATH.read_bytes()),
        "component_sha256": hashes,
        "combined_sha256": combined_sha256(hashes),
        "current_tree_renders_identically": True,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "pandas": __import__("pandas").__version__,
                        "numpy": __import__("numpy").__version__},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rendered_text": frozen,
    }
    if args.check:
        existing = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
        same = (existing["component_sha256"] == hashes
                and existing["fixture_sha256_lf"] == reference["fixture_sha256_lf"])
        logger.info("Reference on disk %s the frozen render.", "MATCHES" if same else "DIFFERS FROM")
        return 0 if same else 1
    REFERENCE_PATH.write_text(json.dumps(reference, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
    logger.info("Wrote %s (combined %s)", REFERENCE_PATH, reference["combined_sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
