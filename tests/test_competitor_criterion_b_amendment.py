"""Criterion (b) amendment, 2026-11-17 (reports/competitor_assessments/
finpanel_arxiv_2611.18150.md section 4, Research).

Drawdown counts only with response language; the series signal needs "panel
data" / "panel regression", not the bare word "panel". A paper the amendment
demotes lands in the digest with a note, never silent (rider 1). Nothing else
is tightened (rider 2): the existing tests in test_competitor_monitor.py run
unchanged.

The required fixture is the REAL FinPanel record as the scanner reads it:
tests/fixtures/finpanel_arxiv_2611.18150.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import competitor_monitor as CM

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "finpanel_arxiv_2611.18150.json"
FINPANEL = json.loads(FIXTURE.read_text(encoding="utf-8"))
TITLE = FINPANEL["title"]


def test_the_real_finpanel_record_lands_in_the_digest_with_the_metric_note():
    t = CM.triage(FINPANEL["title"], FINPANEL["summary"])
    assert t["tier"] == CM.DIGEST
    assert t["threatened_rqs"] == []
    assert "drawdown reported as a metric" in t["note"]
    assert t["criteria"][0].startswith("(b) not met after the 2026-11-17 amendment")


def test_the_real_record_did_escalate_before_the_amendment():
    """Positive control: the old (b) logic, reconstructed from the retained
    pre-amendment vocabulary, fires on the real record, so the test above is
    a demotion, not a record that never matched."""
    hay = f"{FINPANEL['title']}\n{FINPANEL['summary']}"
    assert CM._SIG_LLM.search(hay) and CM._SIG_TRADING.search(hay)
    assert CM._SIG_DRAWDOWN_ANY.search(hay) and CM._SIG_SERIES_PRE_AMENDMENT.search(hay)


@pytest.mark.parametrize("abstract", [
    "We evaluate an LLM trading agent on single stocks and report Sharpe ratio, "
    "return and maximum drawdown.",
    "We evaluate an LLM trading agent with daily decisions and report Sharpe ratio, "
    "return and maximum drawdown (MDD).",
])
def test_metric_only_drawdown_is_digest_with_note(abstract):
    t = CM.triage(TITLE, abstract)
    assert t["tier"] == CM.DIGEST
    assert "drawdown reported as a metric" in t["note"]


@pytest.mark.parametrize("abstract", [
    "We study whether LLM trading agents adjust risk-taking after drawdowns over time.",
    "A longitudinal study of position concentration following deep drawdowns in "
    "LLM portfolio agents.",
    "We measure how LLM trading agents' exposure changes as a function of drawdown depth "
    "in a daily equity series.",
    "We document a drawdown response in LLM trading agents: de-risking after losses, "
    "measured over sequential episodes.",
])
def test_behavioural_drawdown_still_escalates_on_b_and_names_rq5(abstract):
    t = CM.triage(TITLE, abstract)
    assert t["tier"] == CM.ESCALATE
    assert any(c.startswith("(b)") for c in t["criteria"])
    assert any("RQ5" in r for r in t["threatened_rqs"])


def test_disposition_across_sequential_episodes_still_escalates():
    t = CM.triage("Disposition in LLM traders",
                  "We measure the disposition effect of LLM trading agents across sequential episodes.")
    assert t["tier"] == CM.ESCALATE and any("RQ2" in r for r in t["threatened_rqs"])


def test_bare_panel_is_not_a_series_but_panel_data_is():
    persona = CM.triage("A panel of LLM trading personas",
                        "We calibrate a panel of LLM trading personas on stocks.")
    assert persona["tier"] == CM.DIGEST
    assert '"panel" is not panel data' in persona["note"]
    data = CM.triage("LLM trading agents",
                     "We calibrate LLM trading agents and test them with panel data regressions on stocks.")
    assert data["tier"] == CM.ESCALATE


def test_calibration_keeps_its_wide_match():
    t = CM.triage("LLM trading agents", "Confidence calibration of LLM trading agents over time.")
    assert t["tier"] == CM.ESCALATE and any("RQ3" in r for r in t["threatened_rqs"])


def test_digest_line_carries_the_demotion_note():
    t = CM.triage(FINPANEL["title"], FINPANEL["summary"])
    paper = {"id": "http://arxiv.org/abs/2611.18150v1", "title": TITLE, "published": "2026-11-13",
             "tier": t["tier"], "triage_criteria": t["criteria"], "threatened_rqs": [],
             "assessment": "", "venue": "arXiv", "authors": ["Yang"], "summary": FINPANEL["summary"],
             "matched_keywords": ["trading agents"], "categories": ["q-fin.TR"], "relevance": 1,
             "retriage_reason": ""}
    md = CM.build_digest("2026-47", [paper], [], {}, 7, datetime(2026, 11, 16, 23, 16, tzinfo=timezone.utc))
    digest = md[md.index("## Weekly digest"):]
    line = next(l for l in digest.splitlines() if "2611.18150" in l)
    assert line.endswith("_drawdown reported as a metric_")
