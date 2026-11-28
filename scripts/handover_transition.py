"""The venue-path handover of 2026-12-05/06: every state change, scripted.

Governing text: cohort_refresh_ruling.md rev. 2 section 4 (items 2-4) and
section 3 item 8; paper_epoch_retirement.md section 1 (the registered
epoch-boundary procedure, applied as at the 2026-11-06 cutover). Batch 4 item
(e). Run on Saturday 12-05 or Sunday 12-06, outside market hours, after the
venue-history export is committed and the PI has put the new paper account's
keys in place.

The state changes, in order (26):

  1-5    series close, each snapshot's venue book: terminal equity at the
         endpoint session's closing marks, read from its EOD row
  6-10   series close, each refreshed arm's shakedown book, the same way
  11-15  read-only archive, each snapshot's state file ->
         data/state_archive/venue_epoch_<endpoint>/, byte-identical to the
         blob at HEAD (pinned by commit)
  16-20  read-only archive, each shakedown state file ->
         data/state_archive/shakedown_<endpoint>/
  21     the cohort switch: capital_structure.live_cohort_keys in
         config/settings.json from the five snapshots to the five refreshed
         arms, a surgical text edit (CLAUDE.md rule 8 proofs)
  22-26  fresh internal-paper book for each snapshot, $2,000
  27-31  fresh venue book for each refreshed arm, $2,000

31 steps: 30 book-state changes plus the switch. The 11-21 estimate said 20
(five closes, five archives, five paper inceptions, five venue inceptions).
It left out the shakedown books' five closes and five archives, which the
epoch guard needs as much as the snapshots' archives.

DeepSeek is not touched: its paper book continues.

Nothing here places, cancels or changes anything at the venue. The venue is
read once, before any change: the account the broker_paper keys resolve to
must hold no position and no open order, and its cash must agree with the
registered unallocated baseline plus the five fresh books.

Modes:
  --plan                print the steps for this checkout; change nothing
  --dry-run --work DIR  copy this checkout's config/ and --source's
                        data/state, data/performance and data/state_archive
                        (default: this checkout) into DIR and run every step
                        there; no checkout is ever written. --venue test reads the
                        TEST paper account. A refreshed arm with no shakedown
                        state (any dry run before 12-01) gets a seeded one,
                        marked as seeded in the record.
  --execute             run on this checkout. Refuses unless every
                        precondition holds. Every write is backed up in
                        memory first and restored if any later step fails.

Each run writes a record (data/state_archive/handover_<endpoint>.json in the
working root): every step, terminal equities, sha256 and git blob of each
archived file, the commit they are pinned to, and the paths to commit. The
integrity-ledger entry is drafted from that record and inserted by hand
(rule 8: the ledger is never written by a script that reserializes it).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config_loader as cl  # noqa: E402

logger = logging.getLogger("llmlab.handover")

EASTERN = cl.EASTERN
DEFAULT_ENDPOINT = "2026-12-04"
DATA_FOR_DRY_RUN = ("data/state", "data/performance", "data/state_archive")


class HandoverRefused(RuntimeError):
    """A precondition failed; nothing was changed."""


@dataclass
class Step:
    n: int
    kind: str
    book: str | None
    detail: str
    done: bool = False
    result: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ the plan

def books(settings: dict) -> tuple[list[str], list[str]]:
    """(snapshots, refreshed arms): the venue cohort before the switch, and
    the registered refreshed keys."""
    snapshots = list(settings["capital_structure"]["live_cohort_keys"])
    refreshed = list(cl.REFRESHED_BOOK_KEYS)
    return snapshots, refreshed


def plan(settings: dict, endpoint: str) -> list[Step]:
    snaps, arms = books(settings)
    steps: list[Step] = []

    def add(kind, book, detail):
        steps.append(Step(len(steps) + 1, kind, book, detail))
    for b in snaps:
        add("series_close", b, f"terminal equity of {b}'s venue book at the {endpoint} close")
    for b in arms:
        add("series_close", b, f"terminal equity of {b}'s shakedown book at the {endpoint} close")
    for b in snaps:
        add("archive", b, f"data/state/{b}.json -> data/state_archive/venue_epoch_{endpoint}/ (read-only)")
    for b in arms:
        add("archive", b, f"data/state/{b}.json -> data/state_archive/shakedown_{endpoint}/ (read-only)")
    add("cohort_switch", None, f"live_cohort_keys {sorted(snaps)} -> {sorted(arms)}")
    for b in snaps:
        add("inception", b, f"fresh internal-paper book for {b} at the registered book capital")
    for b in arms:
        add("inception", b, f"fresh venue book for {b} at the registered book capital")
    return steps


# ---------------------------------------------------------- reading the root

def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def eod_row(root: Path, book: str, day: str) -> dict | None:
    """The book's EOD row for `day` under EOD_DEDUP_RULE (the last row in file
    order for that date)."""
    path = root / "data" / "performance" / f"{book}.jsonl"
    if not path.exists():
        return None
    row = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if str(r.get("date", ""))[:10] == day:
                row = r
    return row


def git_blob(repo: Path, rel: str, rev: str = "HEAD") -> str | None:
    p = subprocess.run(["git", "rev-parse", f"{rev}:{rel}"], cwd=repo,
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def hash_object(repo: Path, rel: str, data: bytes) -> str:
    """The blob id git would store for `data` at `rel`, line-ending
    conversion included (`git hash-object --path`). A raw sha1 of the
    worktree bytes never matches on a CRLF checkout of an LF blob."""
    p = subprocess.run(["git", "hash-object", f"--path={rel}", "--stdin"], cwd=repo,
                       input=data, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"git hash-object failed for {rel}")
    return p.stdout.decode().strip()


def git_head(repo: Path) -> str | None:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def git_dirty(repo: Path, paths: list[str]) -> list[str]:
    p = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=repo,
                       capture_output=True, text=True)
    return [line for line in p.stdout.splitlines() if line.strip()]


# --------------------------------------------------------- the settings edit

def switch_cohort_text(raw: bytes, old: list[str], new: list[str]) -> bytes:
    """config/settings.json with live_cohort_keys replaced, as a text edit.

    Three proofs before the bytes are returned (CLAUDE.md rule 8, applied to
    settings): the result reparses; it deep-equals the original everywhere
    but that one key; and the bytes outside the replaced array are the
    original bytes. The array must appear exactly once, written as
    `"live_cohort_keys": [...]` on one line."""
    text = raw.decode("utf-8")
    old_frag = '"live_cohort_keys": ' + json.dumps(old)
    new_frag = '"live_cohort_keys": ' + json.dumps(new)
    if text.count(old_frag) != 1:
        raise HandoverRefused(f"settings.json does not contain {old_frag!r} exactly once")
    i = text.index(old_frag)
    out = (text[:i] + new_frag + text[i + len(old_frag):]).encode("utf-8")
    # Proof 1: reparse.
    before, after = json.loads(raw), json.loads(out)
    # Proof 2: deep-equal outside the key.
    assert after["capital_structure"]["live_cohort_keys"] == new
    probe = copy.deepcopy(after)
    probe["capital_structure"]["live_cohort_keys"] = old
    if probe != before:
        raise HandoverRefused("the settings edit changed something besides live_cohort_keys")
    # Proof 3: byte prefix and suffix survive.
    j = len(text[:i].encode("utf-8"))
    tail = raw[j + len(old_frag.encode("utf-8")):]
    if not (out.startswith(raw[:j]) and out.endswith(tail)):
        raise HandoverRefused("the settings edit moved bytes outside the array")
    return out


# -------------------------------------------------------------- the venue

def venue_reading(client, settings_after: dict) -> dict[str, Any]:
    """Read the venue once (GET only): positions, open orders, cash."""
    positions = [p for p in client._request("GET", "/v2/positions") if abs(float(p.get("qty") or 0)) > 1e-9]
    opens = client._request("GET", "/v2/orders", params={"status": "open", "limit": 500})
    acct = client._request("GET", "/v2/account")
    cash = float(acct.get("cash"))
    capital = cl.registered_book_capital(settings_after)
    books_cash = capital * len(settings_after["capital_structure"]["live_cohort_keys"])
    baseline = cl.reconciliation_params(settings_after, "broker_paper")["unallocated_cash_usd"]
    tol = cl.reconciliation_params(settings_after, "broker_paper")["cash_tolerance_usd"]
    return {"positions": len(positions), "open_orders": len(opens), "cash": round(cash, 2),
            "five_fresh_books_cash": books_cash, "registered_unallocated": baseline,
            "implied_unallocated": round(cash - books_cash, 2),
            "cash_agrees": abs((cash - books_cash) - baseline) <= tol,
            "flat": not positions and not opens}


# ---------------------------------------------------------- preconditions

def preconditions(root: Path, settings: dict, endpoint: str, now: datetime, *,
                  first_session: str, strict: bool) -> tuple[list[str], list[str]]:
    """(blocking, notes). `strict` is --execute: everything blocks. A dry run
    turns the calendar/time checks and missing shakedown books into notes."""
    from src.data.market_data import session_bounds

    def session(when):
        # session_bounds reads a datetime; a date is read at its ET noon.
        if not isinstance(when, datetime):
            when = datetime(when.year, when.month, when.day, 12, tzinfo=EASTERN)
        return session_bounds(when)
    blocking, notes = [], []
    soft = notes if not strict else blocking
    snaps, arms = books(settings)

    if sorted(snaps) != sorted(["claude", "claude_opus", "gpt", "gemini", "grok"]):
        blocking.append(f"the venue cohort is {snaps}, not the five snapshots: already switched?")
    for b in arms:
        if b not in settings["models"]:
            blocking.append(f"{b} is not configured (plumbing (a)/(c) not landed?)")
    et_now = now.astimezone(EASTERN)
    today = session(et_now)
    if today is not None and today[0] <= et_now.time() < today[1]:
        blocking.append(f"the market is open ({et_now:%Y-%m-%d %H:%M} ET)")
    ep = session(date.fromisoformat(endpoint))
    if ep is None:
        blocking.append(f"{endpoint} is not a session")
    elif et_now.date() < date.fromisoformat(endpoint) or (
            et_now.date() == date.fromisoformat(endpoint) and et_now.time() < ep[1]):
        soft.append(f"the endpoint session {endpoint} has not closed")
    if session(date.fromisoformat(first_session)) is None:
        blocking.append(f"{first_session} is not a session")
    if strict and first_session <= endpoint:
        blocking.append(f"first session {first_session} is not after the endpoint {endpoint}")

    for b in snaps + arms:
        sp = root / "data" / "state" / f"{b}.json"
        if not sp.exists():
            (soft if b in arms else blocking).append(f"no state file for {b}")
            continue
        epoch = _read_json(sp).get("inception_epoch") or "paper"
        want = "broker_paper" if b in snaps else "paper"
        if epoch != want:
            blocking.append(f"{b}'s state is epoch {epoch!r}, expected {want!r}")
        if eod_row(root, b, endpoint) is None:
            (soft if b in arms else blocking).append(f"{b} has no EOD row for {endpoint}")
    for d in (f"venue_epoch_{endpoint}", f"shakedown_{endpoint}"):
        if (root / "data" / "state_archive" / d).exists():
            blocking.append(f"data/state_archive/{d} already exists")
    return blocking, notes


# ------------------------------------------------------------ the execution

class Journal:
    """In-memory backup of every path before it is written; restore() puts
    each back (or removes a path that did not exist)."""

    def __init__(self):
        self.saved: dict[Path, bytes | None] = {}
        self.dirs: list[Path] = []

    def before_write(self, p: Path) -> None:
        if p not in self.saved:
            self.saved[p] = p.read_bytes() if p.exists() else None

    def mkdir(self, d: Path) -> None:
        """Create `d` and record every directory created, ancestors included,
        so restore() leaves no empty parent behind."""
        missing = []
        p = d
        while not p.exists():
            missing.append(p)
            p = p.parent
        for p in reversed(missing):
            p.mkdir()
            self.dirs.append(p)

    def restore(self) -> None:
        for p, data in self.saved.items():
            if p.exists():
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            if data is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(data)
        for d in reversed(self.dirs):
            shutil.rmtree(d, ignore_errors=True)


def run(root: Path, repo: Path, settings: dict, endpoint: str, first_session: str, *,
        venue: Callable[[dict], dict] | None, strict: bool, seed_missing: bool,
        now: datetime) -> dict[str, Any]:
    """Execute the plan against `root`. Returns the record. Raises
    HandoverRefused (nothing changed) or re-raises after restoring."""
    from src.portfolio import portfolio as pf

    blocking, notes = preconditions(root, settings, endpoint, now,
                                    first_session=first_session, strict=strict)
    snaps, arms = books(settings)
    settings_path = root / "config" / "settings.json"
    raw_settings = settings_path.read_bytes()
    after_bytes = switch_cohort_text(raw_settings, settings["capital_structure"]["live_cohort_keys"],
                                     arms)
    settings_after = json.loads(after_bytes)
    reading = None
    if venue:
        try:
            reading = venue(settings_after)
        except Exception as e:  # noqa: BLE001 - an unreadable venue is a refusal, never a guess
            (blocking if strict else notes).append(f"the venue could not be read: {type(e).__name__}")
    if reading is not None:
        msg = (f"venue: {reading['positions']} positions, {reading['open_orders']} open orders, "
               f"implied unallocated {reading['implied_unallocated']} vs registered "
               f"{reading['registered_unallocated']}")
        if not (reading["flat"] and reading["cash_agrees"]):
            (blocking if strict else notes).append(msg)
        else:
            notes.append(msg)
    elif strict and not any("could not be read" in b for b in blocking):
        blocking.append("the venue was not read")
    head = git_head(repo)
    if strict:
        dirty = git_dirty(repo, ["config/settings.json", "data/state", "data/state_archive"])
        if dirty:
            blocking.append(f"uncommitted changes under the handover paths: {dirty}")
        manifest = f"data/venue_history/{endpoint}_close/MANIFEST.json"
        if git_blob(repo, manifest) is None:
            blocking.append(f"the venue-history export {manifest} is not committed at HEAD")
    if blocking:
        raise HandoverRefused("; ".join(blocking))

    steps = plan(settings, endpoint)
    journal = Journal()
    state_dir = root / "data" / "state"
    seeded: list[str] = []
    saved_state_dir, saved_loader = pf.STATE_DIR, pf.load_settings
    try:
        # Seed a dry run's missing shakedown books (before 12-01 there are none).
        for b in arms:
            if not (state_dir / f"{b}.json").exists():
                if not seed_missing:
                    raise HandoverRefused(f"no shakedown state for {b}")
                pf.STATE_DIR = state_dir
                pf.load_settings = lambda: settings
                journal.before_write(state_dir / f"{b}.json")
                pf.save_portfolio(pf.Portfolio(
                    model_key=b, cash=cl.registered_book_capital(settings), holdings={},
                    halted=False, inception_value=cl.registered_book_capital(settings),
                    inception_date="2026-12-01", last_updated="", inception_epoch="paper"))
                seeded.append(b)

        by_kind: dict[str, list[Step]] = {}
        for s in steps:
            by_kind.setdefault(s.kind, []).append(s)

        for s in by_kind["series_close"]:
            row = eod_row(root, s.book, endpoint)
            state = _read_json(state_dir / f"{s.book}.json")
            s.result = {"endpoint": endpoint,
                        "terminal_equity": None if row is None else row.get("total_value"),
                        "terminal_cash": None if row is None else row.get("cash"),
                        "eod_row_present": row is not None,
                        "inception_date": state.get("inception_date"),
                        "inception_value": state.get("inception_value"),
                        "seeded_for_dry_run": s.book in seeded}
            s.done = True

        for s in by_kind["archive"]:
            label = f"venue_epoch_{endpoint}" if s.book in snaps else f"shakedown_{endpoint}"
            dest_dir = root / "data" / "state_archive" / label
            journal.mkdir(dest_dir)
            src = state_dir / f"{s.book}.json"
            data = src.read_bytes()
            rel = f"data/state/{s.book}.json"
            pinned = git_blob(repo, rel)
            blob = hash_object(repo, rel, data)
            if strict and pinned != blob:
                raise HandoverRefused(f"{rel} differs from its blob at HEAD; commit the endpoint state first")
            dest = dest_dir / f"{s.book}.json"
            journal.before_write(dest)
            dest.write_bytes(data)
            if dest.read_bytes() != data:
                raise RuntimeError(f"archive copy of {s.book} is not byte-identical")
            os.chmod(dest, stat.S_IREAD)
            journal.before_write(src)
            src.unlink()
            s.result = {"archived_to": f"data/state_archive/{label}/{s.book}.json",
                        "sha256": hashlib.sha256(data).hexdigest(), "git_blob": blob,
                        "pinned_to_commit": head if pinned == blob else None,
                        "blob_at_head": pinned}
            s.done = True

        (sw,) = by_kind["cohort_switch"]
        journal.before_write(settings_path)
        settings_path.write_bytes(after_bytes)
        sw.result = {"before": settings["capital_structure"]["live_cohort_keys"], "after": arms,
                     "settings_sha256_before": hashlib.sha256(raw_settings).hexdigest(),
                     "settings_sha256_after": hashlib.sha256(after_bytes).hexdigest()}
        sw.done = True

        pf.STATE_DIR = state_dir
        pf.load_settings = lambda: settings_after
        for s in by_kind["inception"]:
            mode = cl.effective_mode(s.book, settings_after)
            capital = cl.book_inception_capital(s.book, settings_after, mode)
            journal.before_write(state_dir / f"{s.book}.json")
            pf.save_portfolio(pf.Portfolio(
                model_key=s.book, cash=capital, holdings={}, halted=False,
                inception_value=capital, inception_date=first_session, last_updated="",
                inception_epoch=mode))
            s.result = {"epoch": mode, "capital": capital, "inception_date": first_session}
            s.done = True

        # Verify as the pipeline will read it: every book loads under the new
        # settings (the epoch guard passes), and the venue set is the five arms.
        loaded = {b: pf.load_portfolio(b) for b in snaps + arms}
        venue_books = cl.venue_book_keys(cl.active_model_keys(settings_after, date.fromisoformat(first_session)),
                                         settings_after)
        verify = {
            "venue_books": sorted(venue_books),
            "venue_books_are_the_refreshed_arms": sorted(venue_books) == sorted(arms),
            "registered_capital_base": cl.registered_capital_base(settings_after, "broker_paper"),
            "venue_books_cash": sum(loaded[b].cash for b in arms),
            "epochs": {b: loaded[b].inception_epoch for b in snaps + arms},
        }
        if not verify["venue_books_are_the_refreshed_arms"] or verify["venue_books_cash"] != \
                verify["registered_capital_base"]:
            raise RuntimeError(f"post-switch verification failed: {verify}")
    except BaseException:
        journal.restore()
        raise
    finally:
        pf.STATE_DIR, pf.load_settings = saved_state_dir, saved_loader

    record = {
        "kind": "venue_path_handover",
        "governing": ["docs/registrations/cohort_refresh_ruling.md rev. 2 section 4",
                      "docs/registrations/paper_epoch_retirement.md section 1"],
        "endpoint": endpoint, "first_session": first_session,
        "run_at": now.isoformat(), "mode": "execute" if strict else "dry_run",
        "head": head, "notes": notes, "seeded_for_dry_run": seeded,
        "venue": reading, "verify": verify,
        "steps": [asdict(s) for s in steps],
        "paths_to_commit": sorted(
            ["config/settings.json"]
            + [f"data/state/{b}.json" for b in snaps + arms]
            + [s.result["archived_to"] for s in steps if s.kind == "archive"]
            + [f"data/state_archive/handover_{endpoint}.json"]),
    }
    rec_path = root / "data" / "state_archive" / f"handover_{endpoint}.json"
    rec_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def _dry_root(work: Path, source: Path) -> Path:
    if work.exists() and any(work.iterdir()):
        raise HandoverRefused(f"{work} is not empty")
    # The configuration is this checkout's (the code under test); the data is
    # --source's (a checkout at the endpoint's EOD commit).
    shutil.copytree(ROOT / "config", work / "config")
    for rel in DATA_FOR_DRY_RUN:
        src = source / rel
        if src.exists():
            shutil.copytree(src, work / rel)
    return work


def _venue_reader(which: str):
    from src.execution.broker import BrokerClient
    cl.load_env()
    if which == "test":
        saved = {k: os.environ.get(k) for k in ("ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET")}
        os.environ["ALPACA_PAPER_KEY"] = os.environ["ALPACA_TEST_API_KEY_ID"]
        os.environ["ALPACA_PAPER_SECRET"] = os.environ["ALPACA_TEST_API_SECRET_KEY"]
        try:
            client = BrokerClient("broker_paper")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    else:
        client = BrokerClient("broker_paper")
    return lambda settings_after: venue_reading(client, settings_after)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cl.force_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="the snapshots' final venue session")
    ap.add_argument("--first-session", help="the fresh books' first session (default: the next after now)")
    ap.add_argument("--work", help="--dry-run: an empty folder to copy the state into")
    ap.add_argument("--source", help="--dry-run: the checkout to copy from and read blobs in "
                    "(default: this one)")
    ap.add_argument("--venue", choices=("validation", "test", "none"), default="validation")
    args = ap.parse_args(argv)

    source = Path(args.source) if (args.dry_run and args.source) else ROOT
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    if args.plan:
        for s in plan(settings, args.endpoint):
            print(f"{s.n:2d}  {s.kind:<14} {s.book or '':<12} {s.detail}")
        return 0
    now = datetime.now(timezone.utc)
    if args.first_session:
        first = args.first_session
    else:
        from src.data.market_data import inception_session_date
        first = inception_session_date(now)
    if args.execute and args.venue != "validation":
        logger.error("--execute reads the validation account; --venue %s is for dry runs", args.venue)
        return 2
    try:
        if args.dry_run and not args.work:
            raise HandoverRefused("--dry-run needs --work")
        root = _dry_root(Path(args.work), source) if args.dry_run else ROOT
        # The settings the plan reads are the root's (a dry run's copy of
        # --source); the code is this checkout's.
        settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
        venue = None if args.venue == "none" else _venue_reader(args.venue)
        record = run(root, source, settings, args.endpoint, first, venue=venue,
                     strict=args.execute, seed_missing=args.dry_run, now=now)
    except HandoverRefused as e:
        logger.error("REFUSED, nothing changed: %s", e)
        return 2
    done = sum(1 for s in record["steps"] if s["done"])
    logger.info("%s: %d/%d steps done; venue books %s; record %s", record["mode"], done,
                len(record["steps"]), record["verify"]["venue_books"],
                root / "data" / "state_archive" / f"handover_{args.endpoint}.json")
    for n in record["notes"]:
        logger.info("note: %s", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
