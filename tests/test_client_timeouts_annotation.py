"""The two further client-side timeouts (api_call_timeout.md section 2, added
2026-11-17): DeepSeek 2026-04-22T14:05:49Z at the 120 s ceiling and xAI
2026-11-05T18:38:53Z at the registered 300 s constant. Annotated separately
from the four-row xAI file; together the six are the full record."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / "data" / "annotations" / "client_timeouts_retro_2026-11-19.jsonl"
OLD = ROOT / "data" / "annotations" / "xai_client_timeouts_retro_2026-11-15.jsonl"


def _rows(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_exactly_the_two_registered_events():
    rows = _rows(NEW)
    assert [(r["provider"], r["timestamp"][:19], r["client_timeout_ceiling_seconds"]) for r in rows] == [
        ("deepseek", "2026-04-22T14:05:49", 120), ("xai", "2026-11-05T18:38:53", 300)]
    assert {r["api_error_class"] for r in rows} == {"TIMEOUT"}
    assert {r["completeness_denominator_change"] for r in rows} == {0}


def test_each_binds_to_its_unchanged_source_line():
    for a in _rows(NEW):
        raw = (ROOT / a["source_file"]).read_bytes().splitlines()[a["source_line"] - 1].rstrip(b"\r")
        assert hashlib.sha256(raw).hexdigest() == a["source_line_sha256"]
        rec = json.loads(raw)
        assert rec["api_error"] == a["api_error_verbatim"] and rec["api_success"] is False
        assert f"(read timeout={a['client_timeout_ceiling_seconds']})" in rec["api_error"]
        assert "api_error_class" not in rec        # the log is not rewritten


def test_the_six_are_the_full_record():
    """Every stored client read timeout from before the class field existed
    (api_error_class, 2026-11-15) is annotated exactly once. A later timeout
    carries TIMEOUT natively and needs no annotation."""
    annotated = {(r["source_file"], r["source_line"]) for r in _rows(NEW) + _rows(OLD)}
    found = set()
    for p in sorted((ROOT / "data" / "trades").glob("*_2026-*.jsonl")):
        for i, line in enumerate(p.read_bytes().splitlines(), 1):
            if line.strip() and b"Read timed out" in line:
                rec = json.loads(line)
                if ("api_error_class" not in rec and
                        re.search(r"Read timed out\. \(read timeout=\d+\)", rec.get("api_error") or "")):
                    found.add(("data/trades/" + p.name, i))
    assert found == annotated and len(found) == 6
