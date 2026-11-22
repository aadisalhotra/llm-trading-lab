"""Check saved third-party pages for our own API keys, in process (hub H-OPS-02).

Before a saved page is committed: every value in .env that is long enough to be
a credential (16+ characters) is searched for in every given file. Nothing
secret is printed or written: the output is counts and a verdict, plus the
names of .env variables compared (names only). Key-shaped strings that belong
to the page (for example a site's own front-end keys) are counted, not shown.

Usage: python scripts/check_saved_pages_for_keys.py <file> [<file> ...]
Exit code 1 if any .env value occurs in any file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_SHAPES = re.compile(rb"AIza[0-9A-Za-z_\-]{35}|sk-[A-Za-z0-9_\-]{20,}|xai-[A-Za-z0-9]{20,}")


def env_values(path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if len(value) >= 16:
            out[name.strip()] = value.encode()
    return out


def check(files: list[Path], env_path: Path) -> dict:
    values = env_values(env_path)
    shaped, matches = 0, 0
    for f in files:
        b = f.read_bytes()
        shaped += len(KEY_SHAPES.findall(b))
        matches += sum(1 for v in values.values() if v in b)
    return {"files": len(files), "env_values_compared": len(values),
            "env_names": sorted(values), "key_shaped_strings_in_pages": shaped,
            "verdict": "match" if matches else "no match"}


def main() -> int:
    files = [Path(a) for a in sys.argv[1:]]
    res = check(files, ROOT / ".env")
    print(f"files {res['files']}; .env values compared {res['env_values_compared']} "
          f"({', '.join(res['env_names'])}); key-shaped strings in pages "
          f"{res['key_shaped_strings_in_pages']}; verdict: {res['verdict']}")
    return 1 if res["verdict"] == "match" else 0


if __name__ == "__main__":
    raise SystemExit(main())
