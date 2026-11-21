"""Book keys: no key in the full set is a prefix of another (hub 2026-11-21).

A book key names data/state/<key>.json, data/trades/<key>_<YYYY-MM>.jsonl and
every per-book glob (reconciliation.py's f"{book}_20*" among them). If one key
is a prefix of another, a glob or startswith written for the shorter key can
read the longer key's files: the April 2026 commingling of the Sonnet and Opus
state files. The full set is today's configured books plus the five refreshed
arms' keys.
"""
from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path

from src.config_loader import REFRESHED_BOOK_KEYS

ROOT = Path(__file__).resolve().parents[1]

# The one pre-existing pair, from April 2026, recorded rather than hidden: the
# Phase A keys "claude" and "claude_opus" already have the shape, and renaming
# a live book's key is a state migration, not part of this change. The set is
# closed: any other prefix pair fails.
PRE_EXISTING_PREFIX_PAIRS = {("claude", "claude_opus")}


def _configured_keys() -> list[str]:
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    return list(settings["models"])


def test_the_refreshed_keys_are_the_hub_set():
    assert REFRESHED_BOOK_KEYS == {"cb_sonnet": "claude-sonnet-5-5", "cb_opus": "claude-opus-5-5",
                                   "cb_astra": "gpt-6-astra", "cb_grok": "grok-4.7",
                                   "cb_argon": "gemini-4-argon"}


def test_no_key_in_the_full_set_is_a_prefix_of_another():
    keys = set(_configured_keys()) | set(REFRESHED_BOOK_KEYS)
    pairs = {(a, b) for a, b in permutations(keys, 2) if b.startswith(a)}
    assert pairs == PRE_EXISTING_PREFIX_PAIRS, pairs - PRE_EXISTING_PREFIX_PAIRS


def test_no_refreshed_key_touches_any_other_key():
    keys = set(_configured_keys()) | set(REFRESHED_BOOK_KEYS)
    for k in REFRESHED_BOOK_KEYS:
        assert not any(o != k and (o.startswith(k) or k.startswith(o)) for o in keys), k


def test_the_check_would_catch_the_proposed_set():
    proposed = {"claude_sonnet_5_5", "claude_opus_5_5", "gpt_6_astra", "grok_4_7", "gemini_4_argon"}
    keys = set(_configured_keys()) | proposed
    assert {(a, b) for a, b in permutations(keys, 2) if b.startswith(a)} - PRE_EXISTING_PREFIX_PAIRS
