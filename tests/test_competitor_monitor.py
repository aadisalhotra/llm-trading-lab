"""Competitor monitor — area tagging and the structured index.

The weekly arXiv/SSRN scan already existed and has run since 2026-W22. These
tests cover the extension: keyword groups per research area, the structured
triage fields (date/title/venue/relevance), and the append-only index that
makes twelve weeks of digests queryable.

Everything here is offline — no network, synthetic papers only.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import competitor_monitor as CM  # noqa: E402

TS = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)


def _paper(pid, title, summary, cats=("cs.AI",), published="2026-08-08"):
    matched = CM._matched_keywords(f"{title} {summary}")
    areas = CM._matched_areas(matched)
    return {
        "id": f"http://arxiv.org/abs/{pid}", "title": title, "authors": ["X"],
        "published": published, "categories": list(cats), "summary": summary,
        "matched_keywords": matched, "areas": areas,
        "venue": f"arXiv ({cats[0]})" if cats else "arXiv",
        "relevance": CM._relevance_line(title, matched, areas),
    }


# ------------------------------------------------------------------ areas

def test_three_areas_are_covered():
    assert set(CM.KEYWORD_AREAS) == {
        "trading_agents", "cross_model_behavior", "decision_uncertainty"}


def test_flat_keyword_list_matches_the_groups():
    flat = [kw for kws in CM.KEYWORD_AREAS.values() for kw in kws]
    assert CM.KEYWORDS == flat
    assert len(set(CM.KEYWORDS)) == len(CM.KEYWORDS), "no duplicate keywords"


def test_original_trading_keywords_are_preserved():
    """The monitor has run since 2026-W22 on these; the extension must not
    narrow the original scope."""
    for kw in ("LLM trading", "frontier model portfolio", "GPT trading agent",
               "AI investment decisions", "LLM portfolio management",
               "language model trading"):
        assert kw in CM.KEYWORD_AREAS["trading_agents"]


@pytest.mark.parametrize("title,summary,area", [
    ("An LLM trading agent", "we build an LLM trading system", "trading_agents"),
    ("Model agreement study", "a cross-model comparison of decisions", "cross_model_behavior"),
    ("Judgment", "LLM decision making under uncertainty is studied", "decision_uncertainty"),
])
def test_paper_is_tagged_to_its_area(title, summary, area):
    p = _paper("1", title, summary)
    assert area in p["areas"]
    assert p["relevance"]


def test_unmatched_paper_gets_a_check_manually_relevance_line():
    assert "manually" in CM._relevance_line("Unrelated", [], [])


# ------------------------------------------------------- structured entries

def test_structured_entry_has_the_required_fields():
    e = CM.structured_entries("2026-33", TS, [_paper("1", "An LLM trading agent", "LLM trading")])[0]
    for field in ("week", "scanned_at", "date", "title", "venue", "url",
                  "areas", "matched_keywords", "relevance"):
        assert field in e, f"missing {field}"
    assert e["date"] == "2026-08-08"
    assert e["venue"].startswith("arXiv")
    assert e["url"].startswith("https://"), "http is upgraded for the committed record"


# ------------------------------------------------------------------ index

def test_index_is_idempotent_per_week(tmp_path):
    papers = [_paper("1", "An LLM trading agent", "LLM trading"),
              _paper("2", "Model agreement", "cross-model comparison")]
    e = CM.structured_entries("2026-33", TS, papers)
    CM.append_structured_index(e, "2026-33", output_dir=str(tmp_path))
    CM.append_structured_index(e, "2026-33", output_dir=str(tmp_path))
    rows = [json.loads(l) for l in (tmp_path / "competitor_index.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2, "re-running a week must replace, not duplicate"


def test_index_accumulates_across_weeks(tmp_path):
    e33 = CM.structured_entries("2026-33", TS, [_paper("1", "An LLM trading agent", "LLM trading")])
    e34 = CM.structured_entries("2026-34", TS, [_paper("2", "Model agreement", "cross-model comparison")])
    CM.append_structured_index(e33, "2026-33", output_dir=str(tmp_path))
    CM.append_structured_index(e34, "2026-34", output_dir=str(tmp_path))
    rows = [json.loads(l) for l in (tmp_path / "competitor_index.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert sorted({r["week"] for r in rows}) == ["2026-33", "2026-34"]
    assert len(rows) == 2


def test_zero_hit_week_purges_that_weeks_prior_rows(tmp_path):
    e = CM.structured_entries("2026-33", TS, [_paper("1", "An LLM trading agent", "LLM trading")])
    CM.append_structured_index(e, "2026-33", output_dir=str(tmp_path))
    CM.append_structured_index([], "2026-33", output_dir=str(tmp_path))
    rows = [l for l in (tmp_path / "competitor_index.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows == []


def test_mismatched_week_tag_is_refused(tmp_path):
    """Guards the purge-one-week-while-writing-another footgun."""
    e = CM.structured_entries("2026-33", TS, [_paper("1", "An LLM trading agent", "LLM trading")])
    with pytest.raises(ValueError, match="refusing to purge"):
        CM.append_structured_index(e, "2026-34", output_dir=str(tmp_path))


def test_corrupt_index_line_is_skipped_not_fatal(tmp_path):
    (tmp_path / "competitor_index.jsonl").write_text(
        '{"week":"2026-32","title":"ok"}\nnot json at all\n', encoding="utf-8")
    e = CM.structured_entries("2026-33", TS, [_paper("1", "An LLM trading agent", "LLM trading")])
    CM.append_structured_index(e, "2026-33", output_dir=str(tmp_path))
    rows = [json.loads(l) for l in (tmp_path / "competitor_index.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2, "the good prior row survives, the corrupt one is dropped"


# ------------------------------------------------------------------ digest

def test_digest_renders_venue_and_relevance():
    papers = [_paper("1", "An LLM trading agent", "LLM trading", cats=("q-fin.TR",))]
    md = CM.build_digest("2026-33", papers, [], {}, 7, TS)
    assert "**Venue:**" in md
    assert "**Relevance:**" in md
    assert "q-fin.TR" in md
