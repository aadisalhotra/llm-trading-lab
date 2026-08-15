"""Weekly competitive-landscape scan for the LLM Trading Lab paper track.

Scans arXiv (and best-effort SSRN) for new work on LLMs trading / managing
portfolios, so we know who else is in this space before we publish. Writes a
dated markdown digest to ``reports/competitor_digest_YYYY-WW.md``.

arXiv exposes a clean Atom API, so that arm is fully automated and parsed
with the standard library (no extra deps). SSRN has no public API and blocks
automated access, so that arm is best-effort: it attempts a fetch and, when
that fails (the common case from CI), falls back to ready-to-click manual
search links per keyword. The digest always tells you which arm produced
what, so a silent SSRN block can't be mistaken for "no new papers."

Run:
    python -m scripts.competitor_monitor            # last 7 days
    python -m scripts.competitor_monitor --days 365 # first-run backfill

Scheduled every Monday 14:00 UTC by .github/workflows/competitor_monitor.yml.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import requests

# Allow running as a script from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config_loader import REPORTS_DIR, configure_logging  # noqa: E402

logger = logging.getLogger("llmlab.competitor_monitor")

# Keywords grouped by the research area each one is scanning for. The digest
# reports which area a hit landed in, so "who else is running LLM portfolios"
# stays separable from the two methodological areas the paper draws on.
#
#   trading_agents      — the original scope: who else has frontier models
#                         running real portfolios.
#   cross_model_behavior— our own contribution's shape: comparing several
#                         models' decisions on identical inputs.
#   decision_uncertainty— the mechanism literature the RQs lean on.
KEYWORD_AREAS: dict[str, list[str]] = {
    "trading_agents": [
        "LLM trading",
        "frontier model portfolio",
        "GPT trading agent",
        "AI investment decisions",
        "LLM portfolio management",
        "language model trading",
    ],
    "cross_model_behavior": [
        "cross-model comparison",
        "multi-model benchmark",
        "LLM behavioral comparison",
        "comparing language models decisions",
        "model agreement",
    ],
    "decision_uncertainty": [
        "LLM decision making under uncertainty",
        "language model risk preferences",
        "LLM calibration decisions",
        "LLM agent rationality",
        "language model judgment bias",
    ],
}

# Flat list — the arXiv query and the existing manual-link path both use it.
KEYWORDS = [kw for kws in KEYWORD_AREAS.values() for kw in kws]

# Reverse index: keyword -> area, for tagging a hit.
_KEYWORD_AREA = {kw: area for area, kws in KEYWORD_AREAS.items() for kw in kws}

# ==========================================================================
# Research-specified scan spec (2026-08-13). The KEYWORD_AREAS above stay as
# the original coarse net; everything below is the narrower, triage-bearing
# spec. Both feed the arXiv query — the areas keep historical continuity with
# weeks 22-33, the term sets drive escalation.
# ==========================================================================

# arXiv categories. The API matches `cat:` against an entry's primary AND
# cross-list categories, so cross-listed papers are covered by construction —
# no separate "cross-listed" arm is needed.
ARXIV_CATEGORIES = [
    "q-fin.TR", "q-fin.PM", "q-fin.CP",
    "cs.AI", "cs.CL", "cs.CE",
    "econ.GN",
]

# Set 1 — direct claim overlap. A hit here is a candidate threat to a
# registered RQ, not merely adjacent literature.
DIRECT_CLAIM_TERMS = [
    "LLM herding",
    "cross-model convergence trading",
    "language model disposition effect",
    "LLM confidence calibration trading",
    "regime-stratified LLM",
    "frontier model trading behavior",
    "same inputs different decisions",
    "LLM behavioral audit",
    "autonomous LLM portfolio",
]

# Set 2 — adjacent phrasing. Same subject matter, different vocabulary;
# catches work that would never use our terms. Slash groups and "(s)" are
# expanded by _term_pattern, not enumerated by hand.
ADJACENT_TERMS = [
    "LLM trading agent(s)",
    "GPT/Claude/Gemini trading",
    "large language model investor",
    "AI agent stock/equity trading",
    "behavioral biases language models",
    "LLM financial decision-making",
    "sequential decision LLM finance",
    "LLM paper trading",
    "multi-LLM comparison finance",
    "prompt-identical agents",
    "LLM agent benchmark trading",
]

SPEC_TERMS = DIRECT_CLAIM_TERMS + ADJACENT_TERMS
_TERM_SET = {t: "direct_claim" for t in DIRECT_CLAIM_TERMS}
_TERM_SET.update({t: "adjacent" for t in ADJACENT_TERMS})

# Stem rules, applied inside _term_pattern. Research specified herd/herding and
# calibrat*; both are the cases where the inflected form is the one that
# actually appears in abstracts.
_STEMS = {
    "herding": r"herd(?:ing|s|ed)?",
    "herd": r"herd(?:ing|s|ed)?",
    "calibration": r"calibrat\w*",
    "calibrated": r"calibrat\w*",
    "calibrate": r"calibrat\w*",
    "decisions": r"decisions?",
    "decision": r"decisions?",
    "biases": r"bias(?:es)?",
    "bias": r"bias(?:es)?",
    "agents": r"agents?",
    "agent": r"agents?",
    "models": r"models?",
    "model": r"models?",
}


def _term_pattern(term: str) -> re.Pattern:
    """Compile one spec term into a case-insensitive regex.

    Handles the three notations Research used inline:
      "agent(s)"          -> optional trailing s
      "GPT/Claude/Gemini" -> alternation over the slash group
      stemmed words       -> per _STEMS (herd/herding, calibrat*)
    Whitespace in the term becomes flexible whitespace so a line-wrapped
    abstract still matches.
    """
    parts: list[str] = []
    for word in term.split():
        optional_s = word.endswith("(s)")
        if optional_s:
            word = word[:-3]
        # slash group -> alternation, stemming each alternative
        if "/" in word:
            alts = [_STEMS.get(a.lower(), re.escape(a)) for a in word.split("/") if a]
            piece = "(?:" + "|".join(alts) + ")"
        else:
            key = word.lower().strip(".,;:")
            piece = _STEMS.get(key, re.escape(word))
        if optional_s:
            piece += "s?"
        parts.append(piece)
    return re.compile(r"\b" + r"[\s\-]+".join(parts) + r"\b", re.I)


_SPEC_PATTERNS = {t: _term_pattern(t) for t in SPEC_TERMS}


def matched_spec_terms(title: str, abstract: str) -> list[str]:
    """Spec terms present in title or abstract, case-insensitive, stemmed."""
    hay = f"{title}\n{abstract}"
    return [t for t, pat in _SPEC_PATTERNS.items() if pat.search(hay)]


# ---- triage signal vocabulary -------------------------------------------
# Deliberately separate from the search terms: search decides what we look at,
# triage decides what it means. Sharing one list would make a query widening
# silently change escalation behavior.

def _rx(*alts: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.I)


_SIG_LLM = _rx(r"llms?", r"large language models?", r"language models?", r"gpt-?\d*",
               r"claude", r"gemini", r"llama", r"deepseek", r"frontier models?",
               r"foundation models?")
_SIG_TRADING = _rx(r"trading", r"trades?", r"portfolios?", r"investing", r"investment",
                   r"investor", r"equit(?:y|ies)", r"stocks?", r"asset allocation")
_SIG_MULTI = _rx(r"cross-?model", r"multi-?model", r"multi-?llm", r"several (?:llms|models)",
                 r"multiple (?:llms|models)", r"identical inputs", r"same inputs",
                 r"prompt-?identical", r"model comparison", r"comparing (?:llms|models)")
_SIG_AGREE = _rx(r"herd\w*", r"convergen\w*", r"agreement", r"concordance", r"consensus",
                 r"correlat\w* decisions?")
_SIG_BEHAV = _rx(r"disposition effect", r"calibrat\w*", r"drawdown", r"overconfiden\w*",
                 r"loss aversion", r"risk-?taking")
_SIG_SERIES = _rx(r"time series", r"sequential", r"over time", r"longitudinal",
                  r"daily", r"panel", r"episodes?")
_SIG_PREREG = _rx(r"pre-?registered", r"pre-?registration", r"preregistr\w*")
_SIG_FIRST = _rx(r"first (?:study|work|paper|to|systematic|large-?scale)",
                 r"we are the first", r"novel(?:ty)? claim")

# digest-tier subject matter.
# NOTE: every multi-word alternative carries its own plural. `_rx` closes with
# \b, and \b will not match between "agent" and "s", so a bare "trading agent"
# silently fails on "LLM trading agents" — the exact phrasing most of this
# literature uses. Caught on the first live run: OpenPM (arXiv 2026-08-06,
# "LLM portfolio-management agents") was being logged SILENT instead of DIGEST.
_SIG_DIGEST = _rx(r"trading agents?", r"portfolio-?management agents?",
                  r"simulated markets?", r"agent-?based markets?",
                  r"finance-?tuned", r"financial(?:ly)? (?:fine-?tuned|pretrained)",
                  r"sentiments?", r"news", r"risk (?:preferences?|attitudes?)",
                  r"questionnaires?", r"benchmarks?", r"evaluation frameworks?")
# silent-tier subject matter
_SIG_SILENT = _rx(r"reinforcement learning", r"deep rl", r"\bq-?learning",
                  r"crypto\w*", r"bitcoin", r"blockchain",
                  r"human subjects?", r"participants", r"undergraduate")

# Which registered RQ each escalation signal threatens.
_RQ_OF = {
    "herding": "RQ1 (cross-model decision convergence)",
    "disposition": "RQ2 (disposition effect)",
    "calibration": "RQ3 (confidence calibration)",
    "drawdown": "RQ5 (path-dependent risk behavior)",
    "reproducibility": "RQ6 (operational reproducibility)",
}

ESCALATE, DIGEST, SILENT = "escalate", "digest", "silent"


def triage(title: str, abstract: str) -> dict:
    """Three-tier classification per the 2026-08-13 Research spec.

    Returns {tier, criteria, threatened_rqs, assessment}. Escalation criteria
    are (a)-(d) verbatim from the spec; digest and silent are subject-matter
    buckets. Anything that matched the query but fits no bucket lands silent —
    the query is deliberately wider than the triage.
    """
    hay = f"{title}\n{abstract}"
    llm, trade = bool(_SIG_LLM.search(hay)), bool(_SIG_TRADING.search(hay))
    crit: list[str] = []
    rqs: list[str] = []

    if llm and trade and _SIG_MULTI.search(hay) and _SIG_AGREE.search(hay):
        crit.append("(a) >=2 LLMs on identical inputs, cross-model agreement/convergence/herding")
        rqs.append(_RQ_OF["herding"])
    if llm and trade and _SIG_BEHAV.search(hay) and _SIG_SERIES.search(hay):
        crit.append("(b) disposition / calibration / drawdown response in LLM trading agents over a series")
        for key, pat in (("disposition", r"disposition effect"),
                         ("calibration", r"calibrat"),
                         ("drawdown", r"drawdown")):
            if re.search(pat, hay, re.I):
                rqs.append(_RQ_OF[key])
    if llm and trade and _SIG_PREREG.search(hay):
        crit.append("(c) pre-registered LLM trading-behavior study")
    if llm and trade and _SIG_FIRST.search(hay):
        crit.append('(d) abstract claims a "first" overlapping a registered RQ')

    if crit:
        return {"tier": ESCALATE, "criteria": crit,
                "threatened_rqs": sorted(set(rqs)),
                "assessment": _overlap_assessment(title, crit, sorted(set(rqs)))}

    if _SIG_SILENT.search(hay) and not (llm and trade):
        return {"tier": SILENT, "criteria": ["RL-only / crypto / human-subject"],
                "threatened_rqs": [], "assessment": ""}
    if (llm or trade) and _SIG_DIGEST.search(hay):
        return {"tier": DIGEST, "criteria": ["adjacent subject matter"],
                "threatened_rqs": [], "assessment": ""}
    return {"tier": SILENT, "criteria": ["matched query, no triage bucket"],
            "threatened_rqs": [], "assessment": ""}


def _overlap_assessment(title: str, crit: list[str], rqs: list[str]) -> str:
    """Two-line overlap assessment naming the threatened RQ(s)."""
    one = "Meets " + "; ".join(c.split(" ", 1)[0] for c in crit) + \
          " of the escalation criteria on title+abstract."
    two = ("Threatens " + ", ".join(rqs) + "."
           if rqs else "No specific RQ named by the matched criteria — read before ruling.")
    return one + " " + two


def _abstract_hash(abstract: str) -> str:
    """Stable hash of a normalized abstract — the v2+ re-triage trigger."""
    norm = " ".join((abstract or "").split()).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def dedup_and_retriage(papers: list[dict], prior_index: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split this run's hits into (new_or_changed, suppressed).

    Dedup key is the version-stripped arXiv id (or SSRN abstract id). A paper
    already in the index is suppressed UNLESS its abstract hash changed — the
    spec's "v2+ re-triage only on abstract change". A v3 that only fixed a
    typo in the PDF carries the same abstract and must not re-escalate; a v2
    that rewrote the abstract to claim cross-model herding must.
    """
    prior_hash: dict[str, str] = {}
    for row in prior_index:
        key = row.get("base_id") or row.get("dedup_key") or row.get("url") or ""
        if key:
            prior_hash.setdefault(_arxiv_base_id(key), row.get("abstract_hash") or "")

    fresh, suppressed = [], []
    for p in papers:
        key = _arxiv_base_id(p.get("base_id") or p.get("id") or "")
        if key in prior_hash:
            if prior_hash[key] and prior_hash[key] != p.get("abstract_hash"):
                p["retriage_reason"] = f"v{p.get('version', 1)} abstract changed"
                fresh.append(p)
            else:
                suppressed.append(p)
        else:
            fresh.append(p)
    if suppressed:
        logger.info("dedup: %d already-indexed paper(s) suppressed (abstract unchanged)",
                    len(suppressed))
    return fresh, suppressed


def heartbeat_line(week_tag: str, generated_at: datetime, counts: dict[str, int]) -> str:
    """The guaranteed weekly line. Posts on empty weeks — that is the point.

    A monitor that goes quiet is indistinguishable from a monitor that broke.
    """
    return (f"[heartbeat] {generated_at.strftime('%Y-%m-%dT%H:%M:%SZ')} — "
            f"week of {week_tag}: {counts.get(ESCALATE, 0)} escalations, "
            f"{counts.get(DIGEST, 0)} digest, {counts.get(SILENT, 0)} silent")


def tier_counts(papers: list[dict]) -> dict[str, int]:
    counts = {ESCALATE: 0, DIGEST: 0, SILENT: 0}
    for p in papers:
        counts[p.get("tier", SILENT)] = counts.get(p.get("tier", SILENT), 0) + 1
    return counts


# --------------------------------------------------------------------------
# Monthly arms — NBER + RePEc (best-effort, same disclosure discipline as SSRN)
# --------------------------------------------------------------------------

def fetch_monthly_sources(run_date: datetime) -> dict:
    """NBER working papers + RePEc new-papers, pulled on the first run of a month.

    Both are best-effort. Neither offers a filtered API we can rely on from
    CI, so a failure yields manual links rather than silence — the same rule
    the SSRN arm follows, for the same reason.
    """
    out = {"ran": False, "month": run_date.strftime("%Y-%m"),
           "nber": [], "repec": [], "links": {
               "NBER new working papers": NBER_NEW_RSS,
               "RePEc NEP new-papers": REPEC_NEP_URL,
           }}
    if run_date.day > 7:
        return out          # monthly cadence: first run of each month only
    out["ran"] = True
    xml_text = _http_get(NBER_NEW_RSS)
    if xml_text:
        try:
            root = ET.fromstring(xml_text)
            for item in root.iter("item"):
                title = " ".join((item.findtext("title") or "").split())
                desc = " ".join((item.findtext("description") or "").split())
                if not title:
                    continue
                if not matched_spec_terms(title, desc) and not _matched_keywords(f"{title} {desc}"):
                    continue
                tri = triage(title, desc)
                out["nber"].append({
                    "title": html.unescape(title),
                    "url": (item.findtext("link") or "").strip(),
                    "venue": "NBER working paper",
                    "published": (item.findtext("pubDate") or "")[:16],
                    "summary": html.unescape(desc),
                    "abstract_hash": _abstract_hash(desc),
                    "tier": tri["tier"], "triage_criteria": tri["criteria"],
                    "threatened_rqs": tri["threatened_rqs"], "assessment": tri["assessment"],
                })
            logger.info("NBER: %d matching item(s)", len(out["nber"]))
        except ET.ParseError as e:
            logger.warning("NBER RSS parse failed: %s", e)
    else:
        logger.info("NBER: no automated results — manual link provided")
    return out


ARXIV_API = "http://export.arxiv.org/api/query"
SSRN_SEARCH_URL = "https://www.ssrn.com/index.cfm/en/search/?term={}"
# SSRN eJournal-targeted browse pages (Research-specified arms).
SSRN_EJOURNALS = {
    "Financial Economics Network (FEN)":
        "https://www.ssrn.com/index.cfm/en/fen/",
    "Behavioral & Experimental Finance eJournal":
        "https://www.ssrn.com/index.cfm/en/behavioral-experimental-finance/",
}
# Monthly arms.
NBER_NEW_RSS = "https://back.nber.org/rss/new.xml"
REPEC_NEP_URL = "https://ideas.repec.org/n/"
HTTP_TIMEOUT = 45
USER_AGENT = "Mozilla/5.0 (compatible; llm-trading-lab-research-monitor/1.0)"

_ATOM = {"a": "http://www.w3.org/2005/Atom",
         "arxiv": "http://arxiv.org/schemas/atom"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def iso_week_tag(dt: datetime) -> str:
    """ISO year-week tag, e.g. 2026-21 (zero-padded week)."""
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def _http_get(url: str, retries: int = 3) -> str | None:
    """GET via requests (bundled certifi CAs), retrying transient failures.

    arXiv rate-limits with HTTP 429; retry with backoff (honoring Retry-After)
    so a busy moment doesn't blank the weekly digest.
    """
    backoff = 5
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (429, 500, 502, 503) and attempt < retries:
                wait = int(resp.headers.get("Retry-After", backoff))
                logger.warning("HTTP %d for %s — retry %d/%d in %ds",
                               resp.status_code, url[:80], attempt, retries, wait)
                time.sleep(wait)
                backoff *= 2
                continue
            logger.warning("HTTP %d for %s", resp.status_code, url[:90])
            return None
        except Exception as e:
            logger.warning("HTTP GET failed for %s: %s", url[:90], e)
            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2
                continue
            return None
    return None


def _matched_keywords(text: str) -> list[str]:
    low = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in low]


def _matched_areas(matched: list[str]) -> list[str]:
    """Research areas a hit's matched keywords belong to, in declaration order."""
    hit = {_KEYWORD_AREA[kw] for kw in matched if kw in _KEYWORD_AREA}
    return [a for a in KEYWORD_AREAS if a in hit]


def _relevance_line(title: str, matched: list[str], areas: list[str]) -> str:
    """One line on why this paper is in the digest.

    Deliberately mechanical — it states the matched terms and area, and makes
    no claim about the paper's quality or its bearing on our results. A human
    reads the abstract; this is triage, not assessment.
    """
    if not matched:
        return "Matched the area query but no keyword appears in the title or abstract — check manually."
    area_names = {
        "trading_agents": "another LLM-run portfolio / trading agent",
        "cross_model_behavior": "cross-model behavioral comparison",
        "decision_uncertainty": "LLM decision-making under uncertainty",
    }
    label = "; ".join(area_names.get(a, a) for a in areas) or "uncategorised"
    return f"{label} — matched {', '.join(repr(k) for k in matched[:3])}"


# --------------------------------------------------------------------------
# arXiv
# --------------------------------------------------------------------------

def _build_arxiv_query() -> str:
    """Raw (unencoded) Atom search query — urlencode handles escaping.

    Category-scoped as of 2026-08-13: (categories) AND (terms). `cat:` matches
    an entry's primary *and* cross-list categories, so cross-listed work is
    covered without a second arm. Terms are the original KEYWORD_AREAS net plus
    the Research spec sets — the areas keep weeks 22-33 comparable, the spec
    terms are what triage escalates on.
    """
    cats = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
    all_terms = list(dict.fromkeys(KEYWORDS + SPEC_TERMS))
    terms = " OR ".join(f'all:"{kw}"' for kw in all_terms)
    return f"({cats}) AND ({terms})"


def _parse_arxiv_version(pid: str) -> int:
    """Trailing version from an arXiv id URL (…/abs/2601.01234v3 -> 3)."""
    m = re.search(r"v(\d+)\s*$", pid.strip())
    return int(m.group(1)) if m else 1


def _arxiv_base_id(pid: str) -> str:
    """Version-stripped arXiv id — the dedup key."""
    return re.sub(r"v\d+\s*$", "", pid.strip())


def fetch_arxiv(days: int, max_results: int = 60) -> list[dict]:
    """Return recent arXiv papers (within `days`) matching any keyword."""
    params = urllib.parse.urlencode({
        "search_query": _build_arxiv_query(),
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"
    xml_text = _http_get(url)
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("arXiv Atom parse failed: %s", e)
        return []
    total_entries = len(root.findall("a:entry", _ATOM))
    logger.info("arXiv returned %d entries before date filtering", total_entries)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    papers: list[dict] = []
    seen: set[str] = set()
    for entry in root.findall("a:entry", _ATOM):
        pid = (entry.findtext("a:id", default="", namespaces=_ATOM) or "").strip()
        if not pid or pid in seen:
            continue
        published_raw = (entry.findtext("a:published", default="", namespaces=_ATOM) or "").strip()
        try:
            published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            published = None
        # entries are sorted newest-first; once we pass the cutoff we can stop
        if published and published < cutoff:
            break
        title = " ".join((entry.findtext("a:title", default="", namespaces=_ATOM) or "").split())
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=_ATOM) or "").split())
        authors = [a.findtext("a:name", default="", namespaces=_ATOM)
                   for a in entry.findall("a:author", _ATOM)]
        authors = [html.unescape(a) for a in authors if a]
        cats = [c.get("term", "") for c in entry.findall("a:category", _ATOM)]
        matched = _matched_keywords(f"{title} {summary}")
        areas = _matched_areas(matched)
        clean_title = html.unescape(title)
        clean_summary = html.unescape(summary)
        seen.add(pid)
        tri = triage(clean_title, clean_summary)
        papers.append({
            "id": pid,
            "base_id": _arxiv_base_id(pid),
            "version": _parse_arxiv_version(pid),
            "title": clean_title,
            "authors": authors,
            "published": published.strftime("%Y-%m-%d") if published else published_raw[:10],
            "categories": cats,
            "summary": clean_summary,
            "abstract_hash": _abstract_hash(clean_summary),
            "matched_keywords": matched,
            "matched_spec_terms": matched_spec_terms(clean_title, clean_summary),
            # Structured triage fields (date/title/venue/relevance).
            "venue": f"arXiv ({cats[0]})" if cats else "arXiv",
            "areas": areas,
            "relevance": _relevance_line(clean_title, matched, areas),
            "tier": tri["tier"],
            "triage_criteria": tri["criteria"],
            "threatened_rqs": tri["threatened_rqs"],
            "assessment": tri["assessment"],
        })
    logger.info("arXiv: %d papers within the last %d days", len(papers), days)
    return papers


# --------------------------------------------------------------------------
# SSRN (best-effort + manual links)
# --------------------------------------------------------------------------

def fetch_ssrn(keywords: list[str]) -> tuple[list[dict], dict[str, str]]:
    """Best-effort SSRN scan + per-keyword manual-search links.

    SSRN has no public API and blocks bots, so an automated hit usually
    returns nothing useful from CI. We still attempt it and parse any obvious
    result titles; either way we hand back a manual search link per keyword.
    """
    manual_links = {kw: SSRN_SEARCH_URL.format(urllib.parse.quote(kw)) for kw in keywords}
    results: list[dict] = []
    for kw in keywords:
        url = SSRN_SEARCH_URL.format(urllib.parse.quote(kw))
        body = _http_get(url)
        if not body:
            continue
        # Pull anything that looks like an abstract link + title; SSRN markup
        # shifts often, so this is intentionally loose and may legitimately
        # find nothing (then the manual link is the deliverable).
        for m in re.finditer(r'href="(https?://[^"]*abstract[_=]\d+[^"]*)"[^>]*>([^<]{8,160})</a>', body, re.I):
            link, title = m.group(1), html.unescape(m.group(2).strip())
            if title and not any(r["title"] == title for r in results):
                results.append({"title": title, "url": link, "matched_keywords": [kw]})
        time.sleep(1)  # be polite
    if results:
        logger.info("SSRN: parsed %d candidate results", len(results))
    else:
        logger.info("SSRN: no automated results (expected) — manual links provided")
    return results, manual_links


# --------------------------------------------------------------------------
# Digest
# --------------------------------------------------------------------------

def build_digest(week_tag: str, arxiv_papers: list[dict],
                 ssrn_results: list[dict], ssrn_links: dict[str, str],
                 days: int, generated_at: datetime) -> str:
    lines: list[str] = []
    lines.append(f"# Competitor Digest — Week {week_tag}")
    lines.append("")
    lines.append(f"**Generated:** {generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ·  "
                 f"**Window:** last {days} days  ·  "
                 f"**Sources:** arXiv API, SSRN (best-effort)")
    lines.append("")
    lines.append(f"**New on arXiv:** {len(arxiv_papers)}  ·  "
                 f"**SSRN automated hits:** {len(ssrn_results)}")
    lines.append("")

    # Heartbeat — emitted unconditionally, including on empty weeks. A monitor
    # that goes quiet is indistinguishable from a monitor that broke.
    counts = tier_counts(arxiv_papers)
    lines.append("```")
    lines.append(heartbeat_line(week_tag, generated_at, counts))
    lines.append("```")
    lines.append("")
    lines.append("_Tracking who else is putting frontier LLMs in charge of real "
                 "portfolios, ahead of our own publication._")
    lines.append("")
    lines.append("**Search terms:** " + ", ".join(f"`{kw}`" for kw in KEYWORDS))
    lines.append("")
    lines.append("**Direct-claim terms:** " + ", ".join(f"`{t}`" for t in DIRECT_CLAIM_TERMS))
    lines.append("")
    lines.append("**Adjacent terms:** " + ", ".join(f"`{t}`" for t in ADJACENT_TERMS))
    lines.append("")
    lines.append("**arXiv categories:** " + ", ".join(f"`{c}`" for c in ARXIV_CATEGORIES)
                 + " (new + cross-listed)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- ESCALATE ----------------------------------------------------
    esc = [p for p in arxiv_papers if p.get("tier") == ESCALATE]
    lines.append(f"## ESCALATE — same-day ({len(esc)})")
    lines.append("")
    if not esc:
        lines.append("_No escalation-class papers this window._")
    else:
        for p in esc:
            url = p["id"].replace("http://", "https://")
            lines.append(f"### {p['title']}")
            lines.append("")
            lines.append(f"- **Link:** {url}")
            lines.append(f"- **Venue:** {p.get('venue') or 'arXiv'}")
            lines.append(f"- **Date:** {p['published']}")
            if p.get("retriage_reason"):
                lines.append(f"- **Re-triage:** {p['retriage_reason']}")
            lines.append(f"- **Criteria met:** {'; '.join(p.get('triage_criteria') or [])}")
            lines.append(f"- **Overlap assessment:** {p.get('assessment') or ''}")
            lines.append("")
    lines.append("")

    # ---- WEEKLY DIGEST -----------------------------------------------
    dig = [p for p in arxiv_papers if p.get("tier") == DIGEST]
    lines.append(f"## Weekly digest ({len(dig)})")
    lines.append("")
    if not dig:
        lines.append("_Nothing in the digest tier this window._")
    else:
        for p in dig:
            url = p["id"].replace("http://", "https://")
            lines.append(f"- **{p['title']}** — {p.get('venue') or 'arXiv'}, "
                         f"{p['published']} — {url}")
    lines.append("")

    # ---- SILENT LOG ---------------------------------------------------
    sil = [p for p in arxiv_papers if p.get("tier") == SILENT]
    lines.append(f"## Silent log ({len(sil)})")
    lines.append("")
    lines.append("_Logged, not surfaced. Queryable in `competitor_index.jsonl`._")
    lines.append("")
    lines.append("---")
    lines.append("")

    # arXiv
    lines.append("## arXiv")
    lines.append("")
    if not arxiv_papers:
        lines.append("_No new matching papers on arXiv in this window._")
    else:
        for p in arxiv_papers:
            authors = ", ".join(p["authors"][:6]) + (" et al." if len(p["authors"]) > 6 else "")
            abs_url = p["id"].replace("http://", "https://")
            lines.append(f"### [{p['title']}]({abs_url})")
            lines.append("")
            lines.append(f"- **Authors:** {authors or '—'}")
            lines.append(f"- **Published:** {p['published']}  ·  "
                         f"**Venue:** {p.get('venue') or 'arXiv'}  ·  "
                         f"**Categories:** {', '.join(p['categories']) or '—'}")
            if p.get("relevance"):
                lines.append(f"- **Relevance:** {p['relevance']}")
            if p["matched_keywords"]:
                lines.append(f"- **Matched:** {', '.join(p['matched_keywords'])}")
            abstract = p["summary"]
            if len(abstract) > 600:
                abstract = abstract[:600].rstrip() + "…"
            lines.append(f"- **Abstract:** {abstract}")
            lines.append("")
    lines.append("")

    # SSRN
    lines.append("---")
    lines.append("")
    lines.append("## SSRN")
    lines.append("")
    if ssrn_results:
        lines.append("Automated candidate results (verify manually — SSRN markup is noisy):")
        lines.append("")
        for r in ssrn_results:
            lines.append(f"- [{r['title']}]({r['url']})  ·  matched: {', '.join(r['matched_keywords'])}")
        lines.append("")
    else:
        lines.append("SSRN has no public API and blocks automated queries, so no "
                     "automated results this week. Check these manually:")
        lines.append("")
    lines.append("**Manual search links:**")
    lines.append("")
    for kw, url in ssrn_links.items():
        lines.append(f"- `{kw}` → [{url}]({url})")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Auto-generated by `scripts/competitor_monitor.py`. "
                 "Part of the LLM Trading Lab paper track.*")
    lines.append("")
    return "\n".join(lines)


def structured_entries(week_tag: str, generated_at: datetime,
                       arxiv_papers: list[dict]) -> list[dict]:
    """One flat record per hit: date, title, venue, one-line relevance.

    The markdown digest is for reading; this is the queryable series. Twelve
    weeks of prose can't answer "has anything in cross-model behavioral
    comparison appeared since June" without re-reading twelve files.
    """
    return [{
        "week": week_tag,
        "scanned_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date": p["published"],
        "title": p["title"],
        "venue": p.get("venue") or "arXiv",
        "url": p["id"].replace("http://", "https://"),
        "areas": p.get("areas") or [],
        "matched_keywords": p.get("matched_keywords") or [],
        "relevance": p.get("relevance") or "",
        # --- triage fields (2026-08-13 spec) ---
        "base_id": p.get("base_id") or _arxiv_base_id(p.get("id") or ""),
        "version": p.get("version", 1),
        "abstract_hash": p.get("abstract_hash") or "",
        "matched_spec_terms": p.get("matched_spec_terms") or [],
        "tier": p.get("tier") or SILENT,
        "triage_criteria": p.get("triage_criteria") or [],
        "threatened_rqs": p.get("threatened_rqs") or [],
        "assessment": p.get("assessment") or "",
        "retriage_reason": p.get("retriage_reason") or "",
    } for p in arxiv_papers]


def load_structured_index(output_dir: str | None = None) -> list[dict]:
    """Read the append-only index. Empty list when it does not exist yet."""
    path = os.path.join(output_dir or str(REPORTS_DIR), "competitor_index.jsonl")
    rows: list[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


_DIGEST_HEAD = re.compile(r"^### \[(?P<title>.+?)\]\((?P<url>[^)]+)\)\s*$", re.M)


def parse_digest_file(path: str) -> list[dict]:
    """Recover paper records from a historical digest markdown file.

    The structured index only exists from 2026-08-10 onward (commit 0424fd8b,
    which landed AFTER that week's run), so weeks 22-33 have no index rows at
    all. Their only machine-readable record is the digest markdown, which does
    retain title, url, date, categories and a truncated abstract — enough to
    triage. Parsed rather than declared unavailable, because "no history" and
    "history in a different format" are very different answers.
    """
    try:
        text = open(path, "r", encoding="utf-8").read()
    except OSError:
        return []
    week = ""
    m = re.search(r"# Competitor Digest — Week (\S+)", text)
    if m:
        week = m.group(1)
    out: list[dict] = []
    heads = list(_DIGEST_HEAD.finditer(text))
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]

        def field(name: str) -> str:
            fm = re.search(rf"\*\*{name}:\*\*\s*(.+?)(?:\n-|\n\n|$)", body, re.S)
            return " ".join(fm.group(1).split()) if fm else ""

        published = ""
        pm = re.search(r"\*\*Published:\*\*\s*(\d{4}-\d{2}-\d{2})", body)
        if pm:
            published = pm.group(1)
        cats = field("Categories")
        out.append({
            "week": week,
            "title": h.group("title"),
            "url": h.group("url"),
            "base_id": _arxiv_base_id(h.group("url")),
            "date": published,
            "venue": f"arXiv ({cats.split(',')[0].strip()})" if cats else "arXiv",
            "categories": [c.strip() for c in cats.split(",") if c.strip()],
            "summary": field("Abstract"),
            "matched_keywords": [k.strip() for k in field("Matched").split(",") if k.strip()],
            "source": os.path.basename(path),
        })
    return out


def load_history(output_dir: str | None = None) -> tuple[list[dict], str]:
    """Historical records + which source produced them.

    Prefers the structured index; falls back to parsing the digest markdown
    when the index is absent or empty.
    """
    rows = load_structured_index(output_dir)
    if rows:
        return rows, "competitor_index.jsonl"
    out_dir = output_dir or str(REPORTS_DIR)
    import glob as _glob
    recovered: list[dict] = []
    for p in sorted(_glob.glob(os.path.join(out_dir, "competitor_digest_*.md"))):
        recovered.extend(parse_digest_file(p))
    return recovered, "digest markdown (structured index absent)"


def retro_triage(output_dir: str | None = None) -> dict:
    """Re-run every historical record through the current triage criteria.

    Abstracts in the digest markdown are truncated at ~600 chars, so a signal
    living only in a cut tail would be missed. That is a real limitation and is
    reported rather than hidden.
    """
    rows, source = load_history(output_dir)
    by_tier: dict[str, list[dict]] = {ESCALATE: [], DIGEST: [], SILENT: []}
    for r in rows:
        tri = triage(str(r.get("title") or ""), str(r.get("summary") or r.get("relevance") or ""))
        rec = dict(r)
        rec.update(tri)
        by_tier[tri["tier"]].append(rec)
    weeks = sorted({r.get("week") for r in rows if r.get("week")})
    return {
        "source": source,
        "rows": len(rows),
        "weeks": weeks,
        "counts": {k: len(v) for k, v in by_tier.items()},
        "escalations": by_tier[ESCALATE],
        "digest_tier": [{"title": r.get("title"), "url": r.get("url"), "week": r.get("week")}
                        for r in by_tier[DIGEST]],
        "silent_tier": [{"title": r.get("title"), "url": r.get("url"), "week": r.get("week")}
                        for r in by_tier[SILENT]],
        "abstract_retained": sum(1 for r in rows if r.get("summary")),
    }


def append_structured_index(entries: list[dict], week_tag: str,
                            output_dir: str | None = None) -> str:
    """Append this week's entries to the append-only JSONL index.

    Idempotent per week: re-running a week replaces that week's rows rather
    than duplicating them, so a manual re-run or a backfill can't inflate the
    series. `week_tag` is passed separately from the entries because a week
    with zero hits still has to purge any prior rows for that week — but the
    two must agree, or the purge would clear one week while writing another.
    """
    mismatched = sorted({e.get("week") for e in entries if e.get("week") != week_tag})
    if mismatched:
        raise ValueError(
            f"append_structured_index: entries carry week(s) {mismatched} but week_tag is "
            f"{week_tag!r} — refusing to purge one week while appending another")

    out_dir = output_dir or str(REPORTS_DIR)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "competitor_index.jsonl")

    kept: list[dict] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("week") != week_tag:
                    kept.append(row)
    kept.extend(entries)
    with open(path, "w", encoding="utf-8") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("Structured index: %d rows total (%d added for %s)",
                len(kept), len(entries), week_tag)
    return path


def run(days: int = 7, max_results: int = 60, output_dir: str | None = None) -> str:
    generated_at = datetime.now(timezone.utc)
    week_tag = iso_week_tag(generated_at)

    out_dir = output_dir or str(REPORTS_DIR)

    arxiv_papers = fetch_arxiv(days=days, max_results=max_results)
    # Dedup against everything already indexed; a v2+ only returns if its
    # abstract changed. Suppressed rows stay out of the digest AND out of the
    # index, so re-running a week can't inflate the series.
    prior = load_structured_index(out_dir)
    arxiv_papers, suppressed = dedup_and_retriage(arxiv_papers, prior)
    ssrn_results, ssrn_links = fetch_ssrn(KEYWORDS)
    monthly = fetch_monthly_sources(generated_at)

    digest = build_digest(week_tag, arxiv_papers, ssrn_results, ssrn_links,
                          days, generated_at)
    digest += _monthly_section(monthly) + _ejournal_section() + _relay_footer()

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"competitor_digest_{week_tag}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(digest)

    entries = structured_entries(week_tag, generated_at, arxiv_papers)
    append_structured_index(entries, week_tag, output_dir=out_dir)

    counts = tier_counts(arxiv_papers)
    # The heartbeat goes to the log as well as the digest, so an empty week is
    # visible in CI output without opening the file.
    logger.info("%s", heartbeat_line(week_tag, generated_at, counts))

    # Active alert on ESCALATE only. Deliberately not on the heartbeat and not
    # on digest-tier rows: a channel that fires weekly stops being read.
    escalations = [p for p in arxiv_papers if p.get("tier") == ESCALATE]
    if escalations:
        try:
            from src.alerts.competitor_escalation import send_escalation_alert
            send_escalation_alert(
                [{"title": p.get("title"), "url": p["id"].replace("http://", "https://"),
                  "venue": p.get("venue"), "date": p.get("published"),
                  "triage_criteria": p.get("triage_criteria"),
                  "threatened_rqs": p.get("threatened_rqs"),
                  "assessment": p.get("assessment")} for p in escalations],
                week_tag, generated_at)
        except Exception:
            # Never let the alert channel take down the scan: the digest and
            # index are the durable record, the email is the fast path.
            logger.exception("escalation alert failed — digest still written")
    logger.info("Competitor digest written: %s (arXiv=%d, suppressed=%d, SSRN=%d)",
                out_path, len(arxiv_papers), len(suppressed), len(ssrn_results))
    return out_path


def _monthly_section(monthly: dict) -> str:
    lines = ["", "## Monthly arms — NBER / RePEc", ""]
    if not monthly.get("ran"):
        lines.append(f"_Not this run — monthly arms run on the first weekly run of each "
                     f"month (current: {monthly.get('month')})._")
    else:
        if monthly.get("nber"):
            for p in monthly["nber"]:
                lines.append(f"- **[{p['tier'].upper()}] {p['title']}** — {p['url']}")
        else:
            lines.append("_No matching NBER items (or the feed was unavailable)._")
        for name, url in monthly.get("links", {}).items():
            lines.append(f"- Manual: [{name}]({url})")
    lines.append("")
    return "\n".join(lines)


def _ejournal_section() -> str:
    lines = ["", "## SSRN eJournals (targeted)", "",
             "SSRN blocks automated queries, so these are the two Research-specified "
             "eJournals as direct browse links — check them when the automated arm "
             "returns nothing.", ""]
    for name, url in SSRN_EJOURNALS.items():
        lines.append(f"- [{name}]({url})")
    lines.append("")
    return "\n".join(lines)


def _relay_footer() -> str:
    return ("\n---\n\n**Relay:** this digest and its heartbeat are surfaced to the "
            "Synthesis Hub via the PI relay every week, including empty weeks. "
            "See `docs/MONITORING.md`.\n")


def main() -> int:
    configure_logging()

    # Alerting preflight, STRICT here. A scan whose escalation channel cannot
    # deliver has no reason to run: no trading is at stake, and a silent
    # inability to escalate is exactly the defect this guards. Raises rather
    # than warns, so the workflow goes red instead of green-with-a-warning.
    from src.alerts.preflight import assert_configured, COMPETITOR
    assert_configured(channels=(COMPETITOR,), strict=True)

    parser = argparse.ArgumentParser(description="Weekly arXiv/SSRN competitor scan")
    parser.add_argument("--days", type=int, default=7, help="Look-back window in days (default 7)")
    parser.add_argument("--max-results", type=int, default=60, help="Max arXiv results to pull")
    parser.add_argument("--output-dir", default=None, help="Override output dir (default: reports/)")
    parser.add_argument("--retro-triage", action="store_true",
                        help="Re-run the whole competitor_index.jsonl history through the "
                             "current three-tier criteria and print the result; writes nothing.")
    parser.add_argument("--test-alert", action="store_true",
                        help="Fire one synthetic ESCALATE alert to prove the channel works "
                             "end to end. Writes no digest and no index rows.")
    args = parser.parse_args()

    if args.test_alert:
        from src.alerts.competitor_escalation import send_escalation_alert, test_payload
        now = datetime.now(timezone.utc)
        wk = iso_week_tag(now)
        ok = send_escalation_alert(test_payload(wk), wk, now, is_test=True)
        logger.info("test alert %s", "SENT" if ok else "NOT SENT (see log above)")
        return 0 if ok else 1

    if args.retro_triage:
        res = retro_triage(output_dir=args.output_dir)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    path = run(days=args.days, max_results=args.max_results, output_dir=args.output_dir)
    logger.info("Done -> %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
