"""Render the finish-reason mapping table for the five refreshed arms from the
adapters' own constants (hub H-OPS-15; deadline_overhead_ruling_2026-11-22.md
section 4 item 3: every value mapped to REFUSAL listed by name, none by
pattern; unlisted values are ABNORMAL_FINISH).

Usage: python scripts/finish_mapping_table.py --write docs/ops/finish_reason_mapping.md
       python scripts/finish_mapping_table.py --check docs/ops/finish_reason_mapping.md
tests/test_finish_mapping_table.py fails if the committed table and the code differ.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def render() -> str:
    from src.adapters import anthropic_adapter as aa
    from src.adapters import gemini_adapter as ga
    from src.adapters import openai_adapter as oa
    from src.adapters import xai_adapter as xa
    from src.adapters.base import FINISH_NORMAL, FINISH_REFUSAL, FINISH_TRUNCATED

    def by(table: dict[str, str], cls: str) -> str:
        names = sorted(k for k, v in table.items() if v == cls)
        return ", ".join(f"`{n}`" for n in names) or "–"

    rows = []
    for arm, model, mod, extra in (
            ("cb_sonnet", "claude-sonnet-5-5", aa, ""), ("cb_opus", "claude-opus-5-5", aa, ""),
            ("cb_astra", "gpt-6-astra", oa, "; a non-null `message.refusal`"),
            ("cb_grok", "grok-4.7", xa, "; a non-null `message.refusal`")):
        t = mod.FINISH_CLASSES
        rows.append((arm, model, f"`{'stop_reason' if mod is aa else 'finish_reason'}`",
                     by(t, FINISH_NORMAL), by(t, FINISH_TRUNCATED), by(t, FINISH_REFUSAL) + extra))
    rows.append(("cb_argon", "gemini-4-argon", "`finish_reason` (enum name); `prompt_feedback.block_reason`",
                 "`STOP`", "`MAX_TOKENS`",
                 ", ".join(f"`{n}`" for n in sorted(ga.REFUSAL_FINISH_REASONS))
                 + "; any non-null `prompt_feedback.block_reason` (a blocked prompt)"))
    lines = [
        "# Finish-reason mapping — the five refreshed arms",
        "",
        "Generated from the adapters' constants by `scripts/finish_mapping_table.py`;",
        "`tests/test_finish_mapping_table.py` fails if this file and the code differ.",
        "",
        "Governing texts: `send_omit_measure_and_finish_reason_ruling_2026-11-21.md` §3;",
        "`calibration_contingencies_ruling_2026-11-21.md` §1; `deadline_overhead_ruling_2026-11-22.md`",
        "§4; hub H-OPS-14 and H-OPS-15.",
        "",
        "| Arm | Identifier | Field read | Normal (parsed) | Budget stop → `DECISION_TRUNCATED` | `REFUSAL` (listed by name) |",
        "|---|---|---|---|---|---|",
    ]
    lines += [f"| `{a}` | `{m}` | {f} | {n} | {t} | {r} |" for a, m, f, n, t, r in rows]
    lines += [
        "",
        "**Every other value is `ABNORMAL_FINISH`**, unknown and absent values included. No value is",
        "mapped by pattern. The raw value is recorded on every response (`api_finish_reason`), and",
        "`finish_class` records the class. `REFUSAL`, `ABNORMAL_FINISH` and `DECISION_TRUNCATED` are",
        "failed decisions: classified before the parser runs, their text discarded, never retried.",
        "",
        "Scope: the refreshed arms from their first session (shakedown included); the five Phase A",
        "snapshots and DeepSeek from the 2026-12-05/06 handover, with plumbing (a).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write")
    g.add_argument("--check")
    a = ap.parse_args()
    text = render()
    if a.write:
        Path(a.write).write_text(text, encoding="utf-8", newline="\n")
        return 0
    same = Path(a.check).read_text(encoding="utf-8").replace("\r\n", "\n") == text
    print("table matches the code" if same else "table DIFFERS from the code")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
