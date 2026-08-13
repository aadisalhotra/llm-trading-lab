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


# ==========================================================================
# 2026-08-13 extension — category scoping, two term sets with stemming,
# three-tier triage, dedup/re-triage, and the guaranteed heartbeat.
# ==========================================================================

def test_arxiv_query_is_category_scoped_and_keeps_both_term_sets():
    q = CM._build_arxiv_query()
    for cat in ("q-fin.TR", "q-fin.PM", "q-fin.CP", "cs.AI", "cs.CL", "cs.CE", "econ.GN"):
        assert f"cat:{cat}" in q, f"category {cat} missing from query"
    assert q.startswith("(") and ") AND (" in q, "query must be (cats) AND (terms)"
    assert 'all:"LLM trading"' in q
    assert 'all:"LLM herding"' in q
    assert 'all:"prompt-identical agents"' in q


def test_term_sets_are_the_research_spec():
    assert len(CM.DIRECT_CLAIM_TERMS) == 9
    assert len(CM.ADJACENT_TERMS) == 11
    assert "autonomous LLM portfolio" in CM.DIRECT_CLAIM_TERMS
    assert "LLM agent benchmark trading" in CM.ADJACENT_TERMS


@pytest.mark.parametrize("text,term", [
    ("We document LLM herd behaviour in markets", "LLM herding"),
    ("LLM herding among agents", "LLM herding"),
    ("a study of LLM trading agents", "LLM trading agent(s)"),
    ("one LLM trading agent", "LLM trading agent(s)"),
    ("Claude trading performance", "GPT/Claude/Gemini trading"),
    ("Gemini trading desk", "GPT/Claude/Gemini trading"),
    ("AI agent equity trading at scale", "AI agent stock/equity trading"),
])
def test_stemming_and_alternation_match(text, term):
    assert term in CM.matched_spec_terms(text, "")


def test_stemming_does_not_overmatch():
    assert "LLM herding" not in CM.matched_spec_terms("LLM herbal remedies", "")


def _tri(title, abstract=""):
    return CM.triage(title, abstract)["tier"]


def test_escalate_criterion_a_cross_model_herding():
    t = CM.triage(
        "Do LLMs herd?",
        "We give five LLMs identical inputs and measure cross-model agreement in "
        "sequential portfolio decisions, finding convergence.")
    assert t["tier"] == CM.ESCALATE
    assert any(c.startswith("(a)") for c in t["criteria"])
    assert any("RQ1" in r for r in t["threatened_rqs"])


def test_escalate_criterion_b_behavioral_over_a_series():
    t = CM.triage(
        "Disposition effects in GPT trading agents",
        "We track the disposition effect of an LLM trading agent over a daily time series "
        "of equity trades.")
    assert t["tier"] == CM.ESCALATE
    assert any("RQ2" in r for r in t["threatened_rqs"])


def test_escalate_criterion_c_preregistered():
    t = CM.triage("A pre-registered study of LLM portfolio choice",
                  "This pre-registered study evaluates language model trading behaviour.")
    assert t["tier"] == CM.ESCALATE
    assert any(c.startswith("(c)") for c in t["criteria"])


def test_escalate_criterion_d_first_claim():
    t = CM.triage("LLM investors",
                  "We are the first to evaluate large language model investor behaviour "
                  "in live equity trading.")
    assert t["tier"] == CM.ESCALATE
    assert any(c.startswith("(d)") for c in t["criteria"])


def test_digest_tier_single_agent_and_benchmarks():
    assert _tri("A live hybrid trading agent with LLM speculation",
                "We build a trading agent benchmark for equities.") == CM.DIGEST


def test_silent_tier_rl_and_crypto():
    assert _tri("Deep reinforcement learning for bitcoin",
                "We apply deep RL and Q-learning to crypto signals.") == CM.SILENT


def test_unclassified_query_hit_lands_silent_not_digest():
    assert _tri("An unrelated paper", "Nothing to do with the subject.") == CM.SILENT


def test_escalation_outranks_silent_signals():
    t = CM.triage("Reinforcement learning and LLM herding",
                  "Using deep RL baselines, we give multiple LLMs identical inputs and "
                  "measure convergence in sequential portfolio trading decisions.")
    assert t["tier"] == CM.ESCALATE


def test_heartbeat_posts_on_an_empty_week():
    line = CM.heartbeat_line("2026-33", TS, CM.tier_counts([]))
    assert "week of 2026-33" in line
    assert "0 escalations, 0 digest, 0 silent" in line


def test_heartbeat_reports_actual_counts():
    papers = [{"tier": CM.ESCALATE}, {"tier": CM.DIGEST}, {"tier": CM.DIGEST},
              {"tier": CM.SILENT}]
    assert "1 escalations, 2 digest, 1 silent" in CM.heartbeat_line(
        "2026-34", TS, CM.tier_counts(papers))


def test_digest_contains_the_heartbeat_even_with_no_papers():
    md = CM.build_digest("2026-33", [], [], {}, 7, TS)
    assert "[heartbeat]" in md
    assert "0 escalations, 0 digest, 0 silent" in md


def test_dedup_suppresses_unchanged_reruns():
    p = {"id": "https://arxiv.org/abs/2608.00001v1",
         "base_id": "https://arxiv.org/abs/2608.00001",
         "abstract_hash": "aaaa", "version": 1}
    prior = [{"base_id": "https://arxiv.org/abs/2608.00001", "abstract_hash": "aaaa"}]
    fresh, supp = CM.dedup_and_retriage([p], prior)
    assert fresh == [] and len(supp) == 1


def test_v2_retriages_only_when_the_abstract_changed():
    v2 = {"id": "https://arxiv.org/abs/2608.00001v2",
          "base_id": "https://arxiv.org/abs/2608.00001",
          "abstract_hash": "bbbb", "version": 2}
    prior = [{"base_id": "https://arxiv.org/abs/2608.00001", "abstract_hash": "aaaa"}]
    fresh, supp = CM.dedup_and_retriage([v2], prior)
    assert len(fresh) == 1 and supp == []
    assert "abstract changed" in fresh[0]["retriage_reason"]


def test_version_and_base_id_parsing():
    assert CM._parse_arxiv_version("https://arxiv.org/abs/2605.28359v3") == 3
    assert CM._parse_arxiv_version("https://arxiv.org/abs/2605.28359") == 1
    assert CM._arxiv_base_id("https://arxiv.org/abs/2605.28359v3") == \
        "https://arxiv.org/abs/2605.28359"


def test_abstract_hash_is_whitespace_and_case_stable():
    assert CM._abstract_hash("Hello  World\n") == CM._abstract_hash("hello world")


def test_digest_markdown_is_parsable_for_retro_triage(tmp_path):
    md = tmp_path / "competitor_digest_2026-23.md"
    md.write_text(
        "# Competitor Digest — Week 2026-23\n\n"
        "### [A Benchmark for LLM Trading Agents](https://arxiv.org/abs/2605.28359v1)\n\n"
        "- **Authors:** A. Author\n"
        "- **Published:** 2026-05-27  ·  **Categories:** cs.AI, q-fin.TR\n"
        "- **Matched:** LLM trading\n"
        "- **Abstract:** We benchmark LLM agents on stock markets.\n",
        encoding="utf-8")
    rows = CM.parse_digest_file(str(md))
    assert len(rows) == 1
    r = rows[0]
    assert r["title"] == "A Benchmark for LLM Trading Agents"
    assert r["date"] == "2026-05-27"
    assert r["week"] == "2026-23"
    assert "q-fin.TR" in r["categories"]
    assert "stock markets" in r["summary"]


def test_retro_triage_falls_back_to_digests_when_index_absent(tmp_path):
    (tmp_path / "competitor_digest_2026-23.md").write_text(
        "# Competitor Digest — Week 2026-23\n\n"
        "### [Deep RL for bitcoin](https://arxiv.org/abs/2605.1v1)\n\n"
        "- **Published:** 2026-05-27  ·  **Categories:** cs.LG\n"
        "- **Abstract:** Deep reinforcement learning on crypto signals.\n",
        encoding="utf-8")
    res = CM.retro_triage(output_dir=str(tmp_path))
    assert res["rows"] == 1
    assert "structured index absent" in res["source"]
    assert res["counts"][CM.SILENT] == 1


def test_structured_entry_carries_the_triage_fields():
    p = _paper("https://arxiv.org/abs/2608.9v1", "LLM herding on identical inputs",
               "Multiple LLMs, identical inputs, cross-model convergence in sequential "
               "portfolio trading decisions.")
    p.update(CM.triage(p["title"], p["summary"]))
    p["base_id"] = CM._arxiv_base_id(p["id"])
    p["abstract_hash"] = CM._abstract_hash(p["summary"])
    e = CM.structured_entries("2026-33", TS, [p])[0]
    for key in ("tier", "triage_criteria", "threatened_rqs", "assessment",
                "base_id", "version", "abstract_hash", "matched_spec_terms"):
        assert key in e, f"missing {key}"


def test_digest_signals_match_plural_forms():
    """Regression: \b cannot match between "agent" and "s".

    A bare r"trading agent" alternative silently failed on "LLM trading
    agents" — the phrasing most of this literature actually uses. Caught on
    the first live run, where OpenPM (arXiv 2026-08-06, "LLM
    portfolio-management agents") was logged SILENT instead of DIGEST.
    """
    t = CM.triage(
        "OpenPM: Auditable Point-in-Time Evaluation for LLM Portfolio-Management Agents",
        "Reported results for LLM trading agents can be inflated by look-ahead leakage. "
        "We present an auditable point-in-time evaluation framework for LLM "
        "portfolio-management agents.")
    assert t["tier"] == CM.DIGEST, "plural agent forms must reach the digest tier"


@pytest.mark.parametrize("phrase", [
    "LLM trading agents", "simulated markets", "agent-based markets",
    "risk preferences", "questionnaires", "benchmarks",
])
def test_plural_digest_phrases_are_recognised(phrase):
    assert CM._SIG_DIGEST.search(f"We study {phrase} in equities.")
