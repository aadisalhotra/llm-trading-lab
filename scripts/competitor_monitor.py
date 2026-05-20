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
import html
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

KEYWORDS = [
    "LLM trading",
    "frontier model portfolio",
    "GPT trading agent",
    "AI investment decisions",
    "LLM portfolio management",
    "language model trading",
]

ARXIV_API = "http://export.arxiv.org/api/query"
SSRN_SEARCH_URL = "https://www.ssrn.com/index.cfm/en/search/?term={}"
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


# --------------------------------------------------------------------------
# arXiv
# --------------------------------------------------------------------------

def _build_arxiv_query() -> str:
    """Raw (unencoded) Atom search query — urlencode handles escaping."""
    terms = " OR ".join(f'all:"{kw}"' for kw in KEYWORDS)
    return f"({terms})"


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
        seen.add(pid)
        papers.append({
            "id": pid,
            "title": html.unescape(title),
            "authors": authors,
            "published": published.strftime("%Y-%m-%d") if published else published_raw[:10],
            "categories": cats,
            "summary": html.unescape(summary),
            "matched_keywords": matched,
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
    lines.append("_Tracking who else is putting frontier LLMs in charge of real "
                 "portfolios, ahead of our own publication._")
    lines.append("")
    lines.append("**Search terms:** " + ", ".join(f"`{kw}`" for kw in KEYWORDS))
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
                         f"**Categories:** {', '.join(p['categories']) or '—'}")
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


def run(days: int = 7, max_results: int = 60, output_dir: str | None = None) -> str:
    generated_at = datetime.now(timezone.utc)
    week_tag = iso_week_tag(generated_at)

    arxiv_papers = fetch_arxiv(days=days, max_results=max_results)
    ssrn_results, ssrn_links = fetch_ssrn(KEYWORDS)

    digest = build_digest(week_tag, arxiv_papers, ssrn_results, ssrn_links,
                          days, generated_at)

    out_dir = output_dir or str(REPORTS_DIR)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"competitor_digest_{week_tag}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(digest)
    logger.info("Competitor digest written: %s (arXiv=%d, SSRN=%d)",
                out_path, len(arxiv_papers), len(ssrn_results))
    return out_path


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Weekly arXiv/SSRN competitor scan")
    parser.add_argument("--days", type=int, default=7, help="Look-back window in days (default 7)")
    parser.add_argument("--max-results", type=int, default=60, help="Max arXiv results to pull")
    parser.add_argument("--output-dir", default=None, help="Override output dir (default: reports/)")
    args = parser.parse_args()

    path = run(days=args.days, max_results=args.max_results, output_dir=args.output_dir)
    logger.info("Done -> %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
