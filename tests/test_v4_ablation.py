"""v4 prompt ablation — the verification gate, re-runnable.

v4 (landed 2026-10-23, first session 2026-10-26, Phase B long-only) is v2 plus separately-verified diff
components (docs/prereg/tier2_novel_sections.md, T2.4, amended):
  1. the shorting ablation -- produced mechanically as the literal reverse of the
     committed v2->v3 shorting diff, never hand-edited from v3;
  2. the settlement representation -- the portfolio block splits cash into settled
     and unsettled balances, and the prompt carries ONE neutral sentence stating
     the settled-funds purchase rule; amended 2026-10-27
     (docs/amendments/v4_component2_rendering_amendment.md): settled cash leads the
     block as the deployable figure, the decision guidance on cash denotes settled
     cash (CASH_SWEEP_EDITS + V4_OUTPUT_SCHEMA), and the cash block is pinned;
  3. the removal of the environment-mode header lines (EXECUTION MODE / PHASE).
Any further component fails verification. The earlier safety argument ("v4 is
byte-identical to v2, so it contains no novel language") held only until
component 2 landed; it is replaced here by per-component checks, each re-derived
from the committed blobs rather than trusting the changelog note. It also pins the code-side half of the ablation — the JSON
output-contract appendix, which lives in prompt_builder, not in the .txt — because
that is where the ablation was originally incomplete: `_output_schema_for` selected
the shorting-aware schema on `major >= 3`, so v4 would have inherited SHORT/COVER
in its action enum while its own prose said long-only.

Run with: python -m pytest tests/test_v4_ablation.py -v
"""
from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import prompt_builder as pb  # noqa: E402
from src.prompt_builder import (  # noqa: E402
    V2_OUTPUT_SCHEMA,
    V3_OUTPUT_SCHEMA,
    V4_OUTPUT_SCHEMA,
    SettlementPromptMismatchError,
    _environment_lines,
    _format_portfolio_block,
    _output_schema_for,
    build_prompts,
    environment_metadata_removed,
    load_prompt_template,
    shows_settlement_split,
    uses_per_ticker_last_action,
)

PROMPTS = ROOT / "prompts"
V3_COMMIT = "57cb14f4"  # the commit that introduced v3 (prompts/CHANGELOG.md)

# Shorting-ENABLING content. Note this is deliberately not a bare "short" token
# scan: v2/v4 contain the word in their long-only *prohibition* ("No short
# selling"), which is content that must be PRESENT, not absent. The distinction
# between prohibiting shorting and enabling it is the whole point of the ablation.
SHORTING_ENABLEMENT = (
    "### Short selling",
    "Long and short positions are both permitted",
    "Gross short exposure",
    "gross short exposure",
    "A 10% stop-loss applies to each",
    "covering a short",
    "buy, sell, short, or cover",
)

# The long-only prohibition v4 must carry (verbatim from v2).
LONG_ONLY_CLAUSE = "- Long positions only. No short selling, no leverage, no options."

# Component 2: the ONE neutral sentence, and where it sits (the last hard limit).
SETTLEMENT_RULE_LINE = (
    "- Purchases may use only your settled cash; the proceeds of a sale become "
    "settled cash one trading day after the sale."
)
HALTS_LINE = "- Trading halts if the portfolio draws down 30% from its peak value."
# Words that would turn a rule statement into strategy guidance.
GUIDANCE_WORDS = ("should", "avoid", "prefer", "consider", "recommend", "best",
                  "ideally", "try to", "make sure", "you ought")

# Component 2, consistency sweep (amendment 2026-10-27,
# docs/amendments/v4_component2_rendering_amendment.md, element 2): every
# decision-guidance reference to cash in the template denotes settled
# (deployable) cash. Each pair is (v2 passage, v4 passage), quoted exactly; these
# and the rule line are the ONLY template differences component 2 may make.
CASH_SWEEP_EDITS = (
    (  # "What you receive"
        "- Your portfolio: current cash, and every open position with its entry price,\n"
        "  current price, unrealized profit/loss in percent, and size as a share of\n"
        "  portfolio value.\n",
        "- Your portfolio: your settled (deployable) cash, total cash and unsettled cash,\n"
        "  and every open position with its entry price, current price, unrealized\n"
        "  profit/loss in percent, and size as a share of portfolio value.\n",
    ),
    (  # "Cash as a position"
        "Cash is a position. Holding it forgoes expected return; deploying all of it forgoes\n"
        "the flexibility to act on a new opportunity without first selling something\n"
        "existing. Treat your cash level as a deliberate choice.\n"
        "\n"
        "When your portfolio is near fully invested (cash near zero), briefly state why\n"
        "near-full deployment fits your current opportunity set. Equally, if you hold a large\n"
        "cash balance, be able to state why your current opportunities do not warrant fuller\n"
        "deployment. There is no required cash level — the requirement is that the level is\n"
        "chosen, not defaulted.\n",
        "Cash is a position. Holding it forgoes expected return; deploying all of your\n"
        "settled cash forgoes the flexibility to act on a new opportunity without first\n"
        "selling something existing. Treat your settled (deployable) cash level as a\n"
        "deliberate choice.\n"
        "\n"
        "When your settled cash is near zero, briefly state why near-full deployment fits\n"
        "your current opportunity set. Equally, if you hold a large settled cash balance, be\n"
        "able to state why your current opportunities do not warrant fuller deployment. There\n"
        "is no required cash level — the requirement is that the level is chosen, not\n"
        "defaulted.\n",
    ),
    (  # the cash_rationale requirement, Output format
        "- A cash rationale, per the \"Cash as a position\" requirement — required when cash is\n"
        "  near zero or unusually high.\n",
        "- A cash rationale, per the \"Cash as a position\" requirement — required when settled\n"
        "  cash is near zero or unusually high.\n",
    ),
)

# Component 2, salience inversion (amendment element 1) and pinned rendering
# (element 3): the literal cash block a v4 model receives for a known state.
PINNED_STATE = {"total_value": 2000.0, "cash": 1500.0, "cash_pct": 0.75,
                "settled_cash": 1100.0, "unsettled_cash": 400.0,
                "settled_cash_pct": 0.55, "holdings": []}
PINNED_V4_CASH_BLOCK = (
    "YOUR CURRENT PORTFOLIO STATE:\n"
    "  Total value:   $2,000.00\n"
    "  Settled cash (deployable): $1,100.00 (55.0%)\n"
    "  Total cash:     $1,500.00\n"
    "  Unsettled cash: $400.00\n"
    "  Open positions: 0 / 50 max\n"
    "  (no open positions — 100% cash)"
)
V2_CASH_LINE_PREFIX = "  Cash:          $"
V4_CASH_LINE_PREFIXES = ("  Settled cash (deployable): $", "  Total cash:     $",
                         "  Unsettled cash: $")


def _sweep_lines(side: int) -> set[str]:
    """Every line of the registered sweep passages on one side (0 = v2, 1 = v4)."""
    return {l for pair in CASH_SWEEP_EDITS for l in pair[side].split("\n") if l}


def _norm(b: bytes) -> bytes:
    """EOL-normalize. The repo is core.autocrlf=true: blobs are LF, checkouts CRLF."""
    return b.replace(b"\r\n", b"\n")


def _blob(rev: str, path: str) -> bytes:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True)
    assert r.returncode == 0, f"git show {rev}:{path} failed: {r.stderr.decode(errors='replace')}"
    return r.stdout


# ---------------------------------------------------------------------------
# The gates: v4 = v2 + three separately-verified components
# ---------------------------------------------------------------------------

def _v2_at_v3_commit() -> str:
    return _norm(_blob(V3_COMMIT, "prompts/v2.txt")).decode("utf-8")


def _v4_text() -> str:
    return _norm((PROMPTS / "v4.txt").read_bytes()).decode("utf-8")


def _revert_component_2(v4: str) -> str:
    """v4 with every registered component-2 template edit undone."""
    assert v4.count(SETTLEMENT_RULE_LINE + "\n") == 1
    out = v4.replace(SETTLEMENT_RULE_LINE + "\n", "")
    for v2_passage, v4_passage in CASH_SWEEP_EDITS:
        assert out.count(v4_passage) == 1, f"sweep passage not found exactly once: {v4_passage[:60]!r}"
        out = out.replace(v4_passage, v2_passage)
    return out


def test_component_1_shorting_ablation_is_mechanically_v2():
    """Component 1. Undo the registered component-2 edits (the rule line and the
    three cash-sweep passages) and v4 must equal the v2 that the inverted diff was
    computed against, byte for byte. A delta here is an ablation error or a
    non-shorting change smuggled into v3 back in June -- a HALT condition, not
    something to absorb."""
    assert _revert_component_2(_v4_text()) == _v2_at_v3_commit()


def test_component_2_settlement_rule_is_exactly_one_neutral_sentence():
    """Component 2 (rule statement). With the cash sweep set aside, the diff
    against v2 is ONE inserted line, it is the registered sentence, it sits
    directly after the last hard limit, and it is a rule statement with no
    strategy guidance. A second sentence is a fourth component and fails."""
    v4_sans_sweep = _v4_text()
    for v2_passage, v4_passage in CASH_SWEEP_EDITS:
        v4_sans_sweep = v4_sans_sweep.replace(v4_passage, v2_passage)
    v2, v4 = _v2_at_v3_commit().split("\n"), v4_sans_sweep.split("\n")
    ops = [op for op in difflib.SequenceMatcher(None, v2, v4).get_opcodes() if op[0] != "equal"]
    assert len(ops) == 1 and ops[0][0] == "insert" and ops[0][4] - ops[0][3] == 1, ops
    inserted = v4[ops[0][3]]
    assert inserted == SETTLEMENT_RULE_LINE
    assert v4[ops[0][3] - 1] == HALTS_LINE, "the rule must be the last hard limit"
    body = inserted[2:]
    assert body.count(".") == 1 and body.endswith("."), "exactly one sentence"
    assert not any(w in inserted.lower() for w in GUIDANCE_WORDS), "rule statement only"


def test_component_2_cash_sweep_points_guidance_at_settled_cash_and_adds_no_guidance():
    """Amendment element 2. Each swept passage names settled cash, adds no
    strategy-guidance vocabulary, and keeps v2's sentence count (wording is
    re-pointed, never extended). The schema's cash_rationale line is the one
    code-side member of the sweep."""
    for v2_passage, v4_passage in CASH_SWEEP_EDITS:
        assert "settled" in v4_passage and "settled" not in v2_passage
        for w in GUIDANCE_WORDS:
            assert v4_passage.lower().count(w) == v2_passage.lower().count(w), w
        assert v4_passage.count(".") == v2_passage.count(".")
    v2_schema, v4_schema = V2_OUTPUT_SCHEMA.split("\n"), V4_OUTPUT_SCHEMA.split("\n")
    changed = [(a, b) for a, b in zip(v2_schema, v4_schema) if a != b]
    assert len(v2_schema) == len(v4_schema) and len(changed) == 1
    assert changed[0][0].startswith('  "cash_rationale":') and "settled" in changed[0][1]


def test_component_2_state_schema_settled_cash_leads_as_the_deployable_figure():
    """Amendment element 1. Settled cash is the first cash line and the only one
    with a percent-of-book figure; total and unsettled follow as bare amounts.
    v1-v3 keep the single Cash line unchanged."""
    plain = _format_portfolio_block(PINNED_STATE, 50).split("\n")
    split = _format_portfolio_block(PINNED_STATE, 50, settlement_split=True).split("\n")
    assert [l for l in plain if l not in split] == ["  Cash:          $1,500.00 (75.0%)"]
    added = [l for l in split if l not in plain]
    assert added == ["  Settled cash (deployable): $1,100.00 (55.0%)",
                     "  Total cash:     $1,500.00", "  Unsettled cash: $400.00"]
    assert "%" in added[0] and not any("%" in l for l in added[1:])
    assert split.index(added[0]) == 2, "settled cash is the first cash line"
    assert shows_settlement_split("v4") and shows_settlement_split("v4.0")
    assert not any(shows_settlement_split(v) for v in ("v1", "v2", "v3"))


def test_v4_cash_block_renders_exactly():
    """Amendment element 3: the literal rendered cash block, pinned. Any change to
    labels, order, spacing or which line carries the percent fails here."""
    assert _format_portfolio_block(PINNED_STATE, 50, settlement_split=True) == PINNED_V4_CASH_BLOCK


def test_v4_cash_block_renders_exactly_in_the_full_user_prompt(monkeypatch):
    """The same pin, through build_prompts with the real template: what a v4 model
    actually receives, not just the helper's output."""
    _system, user = _render(monkeypatch, "v4", state=PINNED_STATE)
    assert PINNED_V4_CASH_BLOCK in user
    assert "  Cash:          $" not in user


def test_component_3_environment_lines_removed_for_v4_and_only_by_a_signed_off_event_elsewhere():
    s = {"mode": "broker_paper", "phase": "Phase A - Paper Trading"}
    for v in ("v1", "v2", "v3"):
        assert _environment_lines(v, s) == ["EXECUTION MODE: BROKER_PAPER",
                                            "PHASE: Phase A - Paper Trading"]
    assert _environment_lines("v4", s) == []
    # Migration branch: the removal lands alone on v3 as the Oct 16 event, via the
    # signed-off setting. Absent today, so v3 renders the lines exactly as before.
    assert environment_metadata_removed("v3", s) is False
    assert environment_metadata_removed("v3", {**s, "prompt_environment_metadata": "removed"}) is True
    assert _environment_lines("v3", {**s, "prompt_environment_metadata": "removed"}) == []


# ---- rendered-prompt verification: nothing beyond the three components ------

def _mkdf(base):
    close = np.linspace(base * 0.9, base, 30)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
         "Volume": np.full(30, 1_000_000.0)},
        index=pd.date_range("2026-04-01", periods=30, freq="D"))


def _render(monkeypatch, version, template_hook=None, enforce=True, extra=None, state=None):
    """(system, user) as a model receives them, with the REAL templates."""
    settings = {
        "prompt_version": version, "mode": "broker_paper", "phase": "Phase A - Paper Trading",
        "portfolio_rules": {"max_trades_per_day": 50, "max_positions": 50},
        "settlement": {"enforce_settled_funds": enforce, "activation_date": "2026-01-01"},
        **(extra or {}),
    }
    monkeypatch.setattr(pb, "load_settings", lambda: settings)
    if template_hook:
        real = pb.load_prompt_template
        monkeypatch.setattr(pb, "load_prompt_template", lambda v: template_hook(real(v)))
    tickers = ["AAPL", "TSLA", "MSFT"]
    state = state or {"total_value": 2000.0, "cash": 1500.0, "cash_pct": 0.75,
                      "settled_cash": 1100.0, "unsettled_cash": 400.0, "settled_cash_pct": 0.55,
                      "holdings": [{"ticker": "AAPL", "shares": 2.0, "avg_cost": 178.0,
                                    "current_price": 180.0, "weight": 0.18, "unrealized_pl_pct": 0.011}]}
    system, user, _v, _i = build_prompts(
        state, {t: _mkdf(b) for t, b in zip(tickers, [180, 250, 420])},
        datetime(2026, 10, 16, 9, 35), shortlisted_symbols=tickers, recent_decisions=[],
        last_actions={}, include_chart_image=False)
    return system, user


def _line_delta(a: str, b: str):
    d = list(difflib.ndiff(a.split("\n"), b.split("\n")))
    return ([l[2:] for l in d if l.startswith("- ")], [l[2:] for l in d if l.startswith("+ ")])


def _schema_delta_rows() -> list[int]:
    a, b = V2_OUTPUT_SCHEMA.split("\n"), V4_OUTPUT_SCHEMA.split("\n")
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def _fourth_component_violations(v2_render, v4_render):
    """Everything that differs between v2 and v4 renders beyond the three registered
    components. Empty means verified."""
    (v2s, v2u), (v4s, v4u) = v2_render, v4_render
    bad = []
    sys_removed, sys_added = _line_delta(v2s, v4s)
    # component 2: the rule line, the registered cash-sweep passages, and the
    # appendix's one cash_rationale line (V2 -> V4 output schema)
    v2_ok = _sweep_lines(0) | {V2_OUTPUT_SCHEMA.split("\n")[i] for i in _schema_delta_rows()}
    v4_ok = _sweep_lines(1) | {V4_OUTPUT_SCHEMA.split("\n")[i] for i in _schema_delta_rows()}
    bad += [("system -", l) for l in sys_removed if l not in v2_ok]
    bad += [("system +", l) for l in sys_added if l != SETTLEMENT_RULE_LINE and l not in v4_ok]
    usr_removed, usr_added = _line_delta(v2u, v4u)
    bad += [("user -", l) for l in usr_removed
            if not (l.startswith("EXECUTION MODE: ") or l.startswith("PHASE: ")
                    or l.startswith(V2_CASH_LINE_PREFIX))]
    bad += [("user +", l) for l in usr_added if not l.startswith(V4_CASH_LINE_PREFIXES)]
    return bad


def test_v4_render_differs_from_v2_by_exactly_the_three_components(monkeypatch):
    v2 = _render(monkeypatch, "v2")
    v4 = _render(monkeypatch, "v4")
    assert _fourth_component_violations(v2, v4) == []
    # and the three components are each actually present
    assert SETTLEMENT_RULE_LINE in v4[0] and SETTLEMENT_RULE_LINE not in v2[0]
    assert "Settled cash (deployable):" in v4[1] and "Unsettled cash:" in v4[1]
    assert "Settled cash" not in v2[1]
    assert all(after in v4[0] and after not in v2[0] for _before, after in CASH_SWEEP_EDITS)
    assert "EXECUTION MODE" in v2[1] and "PHASE:" in v2[1]
    assert "EXECUTION MODE" not in v4[1] and "PHASE:" not in v4[1]


def test_any_fourth_component_fails_verification(monkeypatch):
    """The sentence 'any fourth fails verification' verifies nothing unless a fourth
    actually fails. Inject one into the system prompt and one into the user block."""
    v2 = _render(monkeypatch, "v2")
    sneaky_system = _render(monkeypatch, "v4", template_hook=lambda t: t + "\nBe patient with entries.\n")
    assert any(k == "system +" for k, _ in _fourth_component_violations(v2, sneaky_system))
    second_sentence = _render(monkeypatch, "v4", template_hook=lambda t: t.replace(
        SETTLEMENT_RULE_LINE, SETTLEMENT_RULE_LINE + "\n- Plan your sales around settlement."))
    assert any("Plan your sales" in l for _, l in _fourth_component_violations(v2, second_sentence))


def test_v4_prompt_refuses_to_build_while_settlement_is_not_enforced(monkeypatch):
    """The prompt states the settled-funds rule; building it while the simulator
    does not enforce the rule would tell every model something false."""
    with pytest.raises(SettlementPromptMismatchError):
        _render(monkeypatch, "v4", enforce=False)
    _render(monkeypatch, "v3", enforce=False)      # v3 states no such rule: unaffected
    _render(monkeypatch, "v2", enforce=False)


def test_v3_render_is_unchanged_by_the_v4_machinery(monkeypatch):
    """Removal is never mid-regime: with no signed-off setting, v3 still renders
    EXECUTION MODE and PHASE and has no settled/unsettled lines."""
    system, user = _render(monkeypatch, "v3")
    assert "EXECUTION MODE: BROKER_PAPER" in user and "PHASE: Phase A - Paper Trading" in user
    assert "Settled cash" not in user and SETTLEMENT_RULE_LINE not in system


def test_v3_working_tree_still_matches_its_commit():
    """The inversion is only meaningful if v3 has not drifted since 57cb14f4."""
    assert _norm((PROMPTS / "v3.txt").read_bytes()) == _norm(_blob(V3_COMMIT, "prompts/v3.txt"))


def test_v4_carries_no_shorting_enablement_and_v3_does():
    v4 = load_prompt_template("v4").replace("\r\n", "\n")
    v3 = load_prompt_template("v3").replace("\r\n", "\n")
    for phrase in SHORTING_ENABLEMENT:
        assert phrase not in v4, f"v4 still enables shorting: {phrase!r}"
        assert phrase in v3, (
            f"{phrase!r} is not in v3 — this test's notion of shorting-enabling "
            "content has drifted from the actual v3 text"
        )


def test_v4_restores_the_long_only_prohibition():
    """v4 does not merely omit shorting — it reinstates v2's explicit ban, which
    is what actually keeps a model from attempting a short it cannot express."""
    v4 = load_prompt_template("v4").replace("\r\n", "\n")
    assert LONG_ONLY_CLAUSE in v4
    assert LONG_ONLY_CLAUSE not in load_prompt_template("v3").replace("\r\n", "\n")


def test_v4_retains_everything_that_must_not_change():
    """The non-shorting rule set carries over untouched — the ablation removed
    shorting content and nothing else. Each clause is quoted in its v2 form,
    including the parenthetical-free wording v3 had modified."""
    v4 = load_prompt_template("v4").replace("\r\n", "\n")
    for clause in (
        "- Maximum 50 open positions.",
        "- Maximum 20% of portfolio value in any single name.",
        "- Maximum 50 trades per day.",
        "- A 15% stop-loss applies to each position.",
        "- Trading halts if the portfolio draws down 30% from its peak value.",
        "### Confidence scoring",
        "### Acting against your own recent decisions",
        "### Cash as a position",
        "### Reviewing losing positions",
        "### Why act now",
        "### Pre-market briefing",
        "## Output format",
        "Current market data for the asset universe.",
    ):
        assert clause in v4, f"v4 lost non-shorting content: {clause!r}"


# ---------------------------------------------------------------------------
# The code-side half: the output-contract appendix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version,expected", [
    ("v1", None),
    ("v2", V2_OUTPUT_SCHEMA),
    ("v3", V3_OUTPUT_SCHEMA),   # the shorting regime — unchanged
    ("v4", V4_OUTPUT_SCHEMA),   # long-only: v2's appendix with the settled-cash cash_rationale
    ("v4.0", V4_OUTPUT_SCHEMA),
    ("v5", V2_OUTPUT_SCHEMA),   # unknown future major falls back long-only (safe direction)
])
def test_output_schema_selection(version, expected):
    assert _output_schema_for(version) is expected


def test_v4_appendix_offers_no_short_actions():
    """The regression this test exists for: under `major >= 3`, v4 would have been
    handed V3_OUTPUT_SCHEMA and kept offering SHORT/COVER in the action enum."""
    appendix = _output_schema_for("v4")
    assert '"SHORT"' not in appendix and '"COVER"' not in appendix
    assert '"action": "BUY" | "SELL" | "HOLD"' in appendix
    assert '"SHORT"' in _output_schema_for("v3")  # v3 unchanged


def test_full_v4_system_prompt_offers_no_short_side():
    """Template + appendix together — what a model actually receives under v4.
    The prompt must give it no way to express a short, in prose or in schema."""
    assembled = (load_prompt_template("v4") + "\n\n" + _output_schema_for("v4")).replace("\r\n", "\n")
    for phrase in SHORTING_ENABLEMENT:
        assert phrase not in assembled, f"assembled v4 system prompt leaks {phrase!r}"
    assert '"SHORT"' not in assembled and '"COVER"' not in assembled
    assert LONG_ONLY_CLAUSE in assembled


def test_v4_keeps_the_v2_last_action_feature():
    """Ablation removes shorting, not the v2 feature set."""
    assert uses_per_ticker_last_action("v4") is True


# ---------------------------------------------------------------------------
# Ledger registration
# ---------------------------------------------------------------------------

def test_regime_boundary_registered():
    ledger = json.loads((ROOT / "scripts" / "phase_a_integrity_ledger.json").read_text(encoding="utf-8"))
    b = ledger["regime_boundaries"]["phase_b_long_only"]
    assert b["date"] == "2026-10-23"  # the landing
    assert b["first_effective_session"] == "2026-10-26"  # the record stamps
    assert b["type"] == "prompt_regime_boundary"
    assert b["prompt_version"] == {"from": "v3", "to": "v4"}
    assert b["scope"] == "all_six_arms"
    assert b["live_cohort"] == ["claude", "claude_opus", "gpt", "gemini", "grok"]
    assert "open_shorts_at_boundary" in b
    # The boundary entry is append-only, so it keeps the landed (pre-amendment)
    # digest; the CURRENT v4.txt must match the latest internal revision entry.
    assert V4_LANDED_SHA256 in b["v4_equivalence"]
    revision = _v4_revision_entry(ledger)
    assert revision["pre"]["prompts/v4.txt_sha256_lf"] == V4_LANDED_SHA256
    digest = hashlib.sha256(_norm((PROMPTS / "v4.txt").read_bytes())).hexdigest()
    assert digest == revision["post"]["prompts/v4.txt_sha256_lf"], (
        "ledger records a v4 sha256 that no longer matches prompts/v4.txt"
    )
    # Component 1's reference: the v2 blob the inverted diff was computed against.
    ref = hashlib.sha256(_norm(_blob(V3_COMMIT, "prompts/v2.txt"))).hexdigest()
    assert ref in b["v4_equivalence"], "ledger lost the component-1 reference digest"


V4_LANDED_SHA256 = "5a275e2b0eb306cccb48c136f39784817de9f727e32b3154673403643b4c6d07"
V4_REVISION_ID = "v4_component2_rendering_amendment_2026_10_27"


def _v4_revision_entry(ledger):
    hits = [e for e in ledger["operational_events"]["2026-10"] if e.get("id") == V4_REVISION_ID]
    assert len(hits) == 1, f"ledger must carry exactly one {V4_REVISION_ID} entry"
    return hits[0]


def test_v4_internal_revision_registered_as_a_boundary_within_the_segment():
    """The amendment is an internal revision of the v4 segment, not a new regime:
    prompt_version stays v4, the regime boundary is untouched, and the ledger
    names where the revision starts."""
    ledger = json.loads((ROOT / "scripts" / "phase_a_integrity_ledger.json").read_text(encoding="utf-8"))
    e = _v4_revision_entry(ledger)
    assert e["type"] == "prompt_internal_revision"
    assert e["boundary"] == "regime_boundaries.phase_b_long_only"
    assert e["prompt_version"] == "v4"
    assert e["first_effective_session"] == "2026-10-28"
    assert e["pre_revision_sessions"] == ["2026-10-26", "2026-10-27"]
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert settings["prompt_version"] == "v4"


def test_config_flipped_at_landing():
    """v4 landed 2026-10-23 as ONE event: prompt v4, shorting off, settled-funds
    enforcement on. A v4 prompt refuses to build without enforcement, so the
    three flags can only move together."""
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert settings["prompt_version"] == "v4"
    assert settings["portfolio_rules"]["shorting_enabled"] is False
    assert settings["settlement"]["enforce_settled_funds"] is True
    assert settings["settlement"]["activation_date"] == "2026-10-23"
