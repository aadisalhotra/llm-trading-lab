"""Schema-close back-fill for reports/monthly/2026-05/data_layer.json.

The single, reproducible write that makes May the first canonical monthly record:
reads the existing layer, applies the integrity-layer ADDITIONS (plus one rename,
one restructure, two corrections), writes back, then VERIFIES. No commit.

ADDITIVE + IDEMPOTENT: every mutation is guarded so re-running is a no-op. Sound
existing fields (performance sharpe/vol/drawdown, inception cumulative) are never
recomputed — only added to. A timestamped pre-write backup is created once.

Run:  python -m scripts.backfill_may_data_layer            # write + verify
      python -m scripts.backfill_may_data_layer --verify   # verify only (no write)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYER = os.path.join(ROOT, "reports", "monthly", "2026-05", "data_layer.json")
BACKUP = os.path.join(ROOT, "reports", "monthly", "2026-05", "data_layer.pre_backfill.json")

# Canonical model keys in the layer (Sonnet="claude", Opus="claude_opus")
MODELS = ["claude", "gpt", "gemini", "grok", "deepseek", "claude_opus"]

# ---- per-model integrity values (from the spec) ----------------------------
CUM_CLEAN = {
    "gpt":         {"value": 0.0766, "source": "raw",   "confidence": "estimable", "model_spliced": False},
    "deepseek":    {"value": 0.0586, "source": "raw",   "confidence": "estimable", "model_spliced": True},
    "gemini":      {"value": 0.0494, "source": "defab", "confidence": "estimable", "model_spliced": False},
    "grok":        {"value": 0.0459, "source": "defab", "confidence": "estimable", "model_spliced": False},
    "claude":      {"value": None,   "source": "not_estimable", "confidence": "not_reliably_estimable", "model_spliced": False},
    "claude_opus": {"value": None,   "source": "not_estimable", "confidence": "not_reliably_estimable", "model_spliced": False},
}
DATA_CONFIDENCE = {
    "gpt": "estimable", "grok": "estimable",
    "deepseek": "exploratory_model_splice", "gemini": "exploratory_completeness",
    "claude": "non_estimable_corrupt_book", "claude_opus": "non_estimable_corrupt_book",
}
RISK_BASIS = {
    "gpt": "raw", "grok": "raw_basis_sensitive", "gemini": "raw_basis_sensitive",
    "deepseek": "spliced", "claude": "non_estimable", "claude_opus": "non_estimable",
}
PROFILE_STATUS = {
    "gpt": "estimable_primary", "grok": "estimable_primary",
    "deepseek": "provisional_model_splice", "gemini": "performance_only",
    "claude": "non_estimable", "claude_opus": "non_estimable",
}
RQ_TRUST_FLAG = {
    "gpt": "estimable", "grok": "estimable",
    "deepseek": "exploratory_model_splice", "gemini": "exploratory_completeness",
    "claude": "non_estimable_corrupt_book", "claude_opus": "non_estimable_corrupt_book",
}
PER_MODEL_FABRICATION = {  # bucket A: launch-window fabrication count / gross USD
    "claude":      {"count": 28, "gross_usd": 233010},
    "claude_opus": {"count": 32, "gross_usd": 187658},
    "deepseek":    {"count": 1,  "gross_usd": 7570},
    "gemini":      {"count": 1,  "gross_usd": 5318},
    "grok":        {"count": 1,  "gross_usd": 4989},
    "gpt":         {"count": 1,  "gross_usd": 1104},
}
CLEAN_WINDOW_SOURCE = {
    "gpt": "raw", "deepseek": "raw", "gemini": "defab", "grok": "defab",
    "claude": "not_estimable", "claude_opus": "not_estimable",
}
NON_ESTIMABLE_MODELS = ["claude", "claude_opus"]


def _decision_completeness(layer):
    """api_success / records per model (Gemini ~0.57), from the integrity block."""
    fr = layer["methodology_data_integrity_rq"]["data_integrity"]["per_model_failure_rate"]
    out = {}
    for k, v in fr.items():
        rec = v.get("records") or 0
        out[k] = round(v["api_success"] / rec, 4) if rec else None
    return out


# ===========================================================================
# Mutations (each guarded -> idempotent)
# ===========================================================================

def apply_backfill(layer: dict) -> list[str]:
    changes: list[str] = []
    rm = layer["report_meta"]

    # ---- report_meta ----
    if rm.get("schema_version") != "1.0":
        rm["schema_version"] = "1.0"
        changes.append("report_meta.schema_version = '1.0' [ADD]")

    if not isinstance(rm.get("risk_free_rate"), dict):
        rm["risk_free_rate"] = {"value": 0.0368, "as_of": "2026-04-09"}
        changes.append("report_meta.risk_free_rate -> {value:0.0368, as_of:'2026-04-09'} [CORRECT: scalar->object]")
    # note dates: 2026-06-01 -> inception-pinned 4/9
    for fld, txt in (("risk_free_rate_note", rm.get("risk_free_rate_note", "")),):
        if "2026-06-01" in (txt or ""):
            rm[fld] = txt.replace("as of 2026-06-01", "as of 2026-04-09 (inception-pinned)")
            changes.append(f"report_meta.{fld}: as-of 2026-06-01 -> 2026-04-09 [CORRECT]")
    mn = rm.get("methodology_notes", {})
    if "2026-06-01" in (mn.get("risk_free_rate", "") or ""):
        mn["risk_free_rate"] = mn["risk_free_rate"].replace("source date 2026-06-01", "as-of inception 2026-04-09")
        changes.append("report_meta.methodology_notes.risk_free_rate: source date 2026-06-01 -> 2026-04-09 [CORRECT]")

    # DeepSeek snapshot leg dates (match Page 5): flash 2026-04-24, v4-pro 2026-05-21
    ds = rm["pinned_snapshots"]["deepseek"]["may_snapshots"]
    for snap in ds:
        if snap["snapshot_id"] == "deepseek-v4-flash" and snap.get("first_date") != "2026-04-24":
            snap["first_date"] = "2026-04-24"
            changes.append("report_meta.pinned_snapshots.deepseek flash first_date 2026-05-01 -> 2026-04-24 [CORRECT]")
        if snap["snapshot_id"] == "deepseek-v4-pro" and snap.get("first_date") != "2026-05-21":
            snap["first_date"] = "2026-05-21"
            changes.append("report_meta.pinned_snapshots.deepseek v4-pro first_date 2026-05-22 -> 2026-05-21 [CORRECT: transition boundary]")

    if "descriptive_sharpe" not in rm["spy_benchmark"]:
        rm["spy_benchmark"]["descriptive_sharpe"] = 6.11
        changes.append("report_meta.spy_benchmark.descriptive_sharpe = 6.11 [ADD]")

    # ---- leaderboard: rename + per-model adds + SPY row ----
    spy_cum_inception = rm["spy_benchmark"].get("cumulative_return_since_inception")
    spy_month = rm["spy_benchmark"].get("monthly_return")
    have_spy = any(r.get("model") == "spy_benchmark" for r in layer["leaderboard"])
    for row in layer["leaderboard"]:
        if "cumulative_return" in row and "cumulative_return_inception" not in row:
            row["cumulative_return_inception"] = row.pop("cumulative_return")
            changes.append(f"leaderboard[{row['model']}].cumulative_return -> cumulative_return_inception [RENAME]")
        mk = row["model"]
        if mk in CUM_CLEAN and "cumulative_return_clean" not in row:
            row["cumulative_return_clean"] = dict(CUM_CLEAN[mk])
            changes.append(f"leaderboard[{mk}].cumulative_return_clean [ADD]")
        if mk in DATA_CONFIDENCE and "data_confidence" not in row:
            row["data_confidence"] = DATA_CONFIDENCE[mk]
            changes.append(f"leaderboard[{mk}].data_confidence = {DATA_CONFIDENCE[mk]} [ADD]")
    if not have_spy:
        layer["leaderboard"].append({
            "model": "spy_benchmark", "rank": None,
            "monthly_return": spy_month,
            "cumulative_return_inception": spy_cum_inception,
            "cumulative_return_clean": {"value": 0.0637, "source": "benchmark", "confidence": "benchmark", "model_spliced": False},
            "spy_relative_alpha": 0.0,
            "data_confidence": "benchmark",
        })
        changes.append("leaderboard[spy_benchmark] row [ADD]")

    # ---- performance: risk_basis + SPY row (existing sharpe/vol/dd untouched) ----
    for mk, p in layer["performance"].items():
        if p is not None and "risk_basis" not in p:
            p["risk_basis"] = RISK_BASIS.get(mk)
            changes.append(f"performance[{mk}].risk_basis = {RISK_BASIS.get(mk)} [ADD]")
    if "spy_benchmark" not in layer["performance"]:
        layer["performance"]["spy_benchmark"] = {
            "monthly_return": spy_month, "max_drawdown": None, "volatility": None,
            "sharpe": 6.11, "trade_count": None, "win_rate": None,
            "turnover": None, "avg_hold_days": None, "risk_basis": "benchmark",
        }
        changes.append("performance[spy_benchmark] row (sharpe=6.11 descriptive) [ADD]")

    # ---- charts ----
    if "equity_curve_carries_shakedown_fabrication" not in layer["charts"]:
        layer["charts"]["equity_curve_carries_shakedown_fabrication"] = True
        changes.append("charts.equity_curve_carries_shakedown_fabrication = true [ADD]")

    # ---- cross_model_behavioral: restructure rq1 + episode register ----
    cmb = layer["cross_model_behavioral"]
    if "rq1_herding_point_estimate" in cmb and "rq1" not in cmb:
        numeric = cmb.pop("rq1_herding_point_estimate")
        numeric["reasons"] = {"gemini": "completeness", "deepseek": "model_splice",
                              "claude": "corrupt_book", "claude_opus": "corrupt_book"}
        cmb["rq1"] = {
            "primary": {"status": "not_estimable", "reason": "single_pair_cohort", "deferred_to": "phase_b"},
            "exploratory_pairwise": numeric,
        }
        changes.append("cross_model_behavioral.rq1 [RESTRUCTURE: primary not_estimable + numeric demoted to exploratory_pairwise]")
    if "cross_model_episode_register" not in cmb:
        cmb["cross_model_episode_register"] = []  # no cohort-wide behavioral episodes formally on record
        changes.append("cross_model_behavioral.cross_model_episode_register = [] [ADD]")

    # ---- profiles: status, decision_completeness, drop data_caveat, suppress non-estimable ----
    dc = _decision_completeness(layer)
    for prof in layer["profiles"]:
        mk = prof["model"]
        if "status" not in prof:
            prof["status"] = PROFILE_STATUS.get(mk)
            changes.append(f"profiles[{mk}].status = {PROFILE_STATUS.get(mk)} [ADD]")
        if "decision_completeness" not in prof:
            prof["decision_completeness"] = dc.get(mk)
            changes.append(f"profiles[{mk}].decision_completeness = {dc.get(mk)} [ADD]")
        if "data_caveat" in prof:
            prof.pop("data_caveat")
            changes.append(f"profiles[{mk}].data_caveat [DROP: subsumed by status]")
        if mk in NON_ESTIMABLE_MODELS:
            # suppress (values NOT published) — corrupt-book; status conveys why
            em = prof.get("evidence_metrics", {})
            for f in ("rq2_disposition_difference", "rq3_confidence_outcome_corr"):
                if em.get(f) is not None:
                    em[f] = None
                    changes.append(f"profiles[{mk}].evidence_metrics.{f} -> null [SUPPRESS non-estimable]")
            # Suppress style_tag/risk_posture_tag/strengths/weaknesses for the
            # non-estimable models. risk_posture_tag survives as a SCHEMA FIELD
            # (populate-or-null) but is nulled here: its values are deployment/
            # cash characterizations derived from the book, and these books carry
            # phantom cash/shorts, so the claims are not certifiable -- same
            # treatment as strengths/weaknesses, consistent with non_estimable
            # status. It stays populated (non-null) for the estimable and
            # performance-shown models: GPT, Grok, DeepSeek, Gemini.
            for f in ("style_tag", "risk_posture_tag", "strengths", "weaknesses"):
                if prof.get(f) is not None:
                    prof[f] = None
                    changes.append(f"profiles[{mk}].{f} -> null [SUPPRESS non-estimable]")

    # ---- methodology_data_integrity_rq ----
    mdr = layer["methodology_data_integrity_rq"]
    if "known_caveats" not in mdr:
        mdr["known_caveats"] = {
            "state_integrity": {
                "bugs": [
                    {"id": "launch_day_clobber", "occurred": "2026-04-09", "fix_date": "2026-04-10",
                     "fix_commit": "d2e862ca", "models": "all_six"},
                    {"id": "cross_model_commingling", "occurred": "2026-04-10/2026-04-21", "fix_date": "2026-04-21",
                     "fix_commit": "cacd8058", "models": ["claude", "claude_opus"]},
                ],
                "per_model_fabrication": PER_MODEL_FABRICATION,
                "audit": {"runs": "2800+", "clean_after": "2026-04-23",
                          "clean_runs": {"clean_april": 73, "may": 257, "early_june": 13}, "anomalies": 0},
                "persistence": {
                    "stock_persists_for": ["claude", "claude_opus"],
                    "carried_forward": {"claude": {"phantom_cash": 297000},
                                        "claude_opus": {"phantom_shorts": 575000}},
                    "scope": "phase_a_wide", "consequence": "non_estimable",
                },
                "clean_window_figures_source": CLEAN_WINDOW_SOURCE,
            },
            "model_identity": {
                "deepseek_model_splice": {
                    "alias": "deepseek-reasoner",
                    "transitions": [
                        {"date": "2026-04-24", "to": "v4-flash", "note": "sub-frontier/off-spec, silent provider repoint"},
                        {"date": "2026-05-21", "to": "v4-pro", "note": None},
                    ],
                    "off_spec_window": "2026-04-24 to 2026-05-21",
                    "detection": "undetected ~4 weeks; month-boundary-only check",
                    "performance_status": "real, retained model_spliced",
                    "decision_based_status": "exploratory",
                },
            },
        }
        changes.append("methodology_data_integrity_rq.known_caveats {state_integrity, model_identity} [ADD]")
    # TASK 4a: repoint-boundary provenance note on the DeepSeek model-identity record.
    # Separate idempotent guard so it lands even when known_caveats already exists.
    # (model_identity/known_caveats are built HERE, not in build_monthly_data_layer.py.)
    _mi = mdr.get("known_caveats", {}).get("model_identity", {}).get("deepseek_model_splice")
    if _mi is not None and "first_success_note" not in _mi:
        _mi["first_success_note"] = (
            "first_date 2026-05-21 is the config-repoint boundary; first logged v4-pro "
            "success is 2026-05-22 (n_success unchanged; flagged because the model-identity "
            "analysis turns on this boundary)")
        changes.append("known_caveats.model_identity.deepseek_model_splice.first_success_note [ADD]")
    if "inclusion_gates" not in mdr:
        mdr["inclusion_gates"] = {"completeness_min": 0.80, "uncorrupted_book": True,
                                  "model_identity_stable": True, "passing": ["gpt", "grok"]}
        changes.append("methodology_data_integrity_rq.inclusion_gates [ADD]")
    if "gate_scope_refinement" not in mdr:
        mdr["gate_scope_refinement"] = (
            "book-integrity gate certifies point-return estimability, not daily-risk-path "
            "estimability when residual phantom sits in a volatile name; September pass")
        changes.append("methodology_data_integrity_rq.gate_scope_refinement [ADD]")

    # per-RQ point-estimate status (RQ4/5/6 already have one).
    # RATIFIED CORRECTION (layered on the d413a4ec back-fill): the six-model RQ2/RQ3
    # pools are SUPERSEDED, not reported. They pool non-estimable models (claude,
    # claude_opus: corrupt book) and exploratory models (gemini: completeness;
    # deepseek: model-splice) together with the estimable GPT/Grok, which contradicts
    # the three-gate inclusion framework. Per-model GPT/Grok is the reported primary.
    # Pooled values are retained untouched for provenance.
    pe = mdr["rq_update"]["point_estimates"]
    pe_status = {"RQ1": "not_estimable", "RQ2": "superseded_six_model_pool", "RQ3": "superseded_six_model_pool"}
    for rq, st in pe_status.items():
        if rq in pe and pe[rq].get("status") != st:
            pe[rq]["status"] = st
            changes.append(f"point_estimates.{rq}.status = {st} [SET: reclassified]")
    # RQ2/RQ3 six-model-pool supersession scaffold (idempotent): reported flag,
    # reported-primary pointer (per-model GPT/Grok cohort), and provenance note.
    POOL_META = {
        "RQ2": {"metric": "disposition_difference",
                "pool_field": "pooled_disposition_difference", "n_field": "n_sale_records"},
        "RQ3": {"metric": "confidence_outcome_corr",
                "pool_field": "pooled_confidence_outcome_corr", "n_field": "n_closed_trades"},
    }
    for rq, meta in POOL_META.items():
        node = pe.get(rq)
        if node is None:
            continue
        if node.get("reported") is not False:
            node["reported"] = False
            changes.append(f"point_estimates.{rq}.reported = false [ADD: pool not reported]")
        if "reported_primary" not in node:
            node["reported_primary"] = {
                "basis": "per_model",
                "cohort": ["gpt", "grok"],
                "status": "estimable",
                "metric": meta["metric"],
                "value_ref": f"per_model.gpt.{meta['metric']}, per_model.grok.{meta['metric']}",
                "note": (
                    f"Reported primary for {rq} is the per-model GPT and Grok {meta['metric']} "
                    "-- the two models that clear the three-gate inclusion framework "
                    "(uncorrupted book, stable model identity, completeness >= 0.80) and are "
                    "each individually estimable. Authoritative values live in per_model.gpt "
                    "and per_model.grok and are unchanged."
                ),
            }
            changes.append(f"point_estimates.{rq}.reported_primary [ADD: per-model GPT/Grok primary]")
        if "supersession_note" not in node:
            node["supersession_note"] = (
                f"The six-model pooled {meta['metric']} ({meta['pool_field']}, "
                f"{meta['n_field']}={node.get(meta['n_field'])}) pools non-estimable models "
                "(claude, claude_opus: corrupt book) and exploratory models (gemini: "
                "completeness; deepseek: model-splice) together with the estimable GPT/Grok "
                "cohort, which contradicts the inclusion framework. It is superseded by the "
                "per-model GPT/Grok reported primary and is retained for provenance only -- "
                "not reported."
            )
            changes.append(f"point_estimates.{rq}.supersession_note [ADD]")
    # RQ2/RQ3 per-model trust flag
    for rq in ("RQ2", "RQ3"):
        for mk, pm in pe[rq].get("per_model", {}).items():
            if "trust_flag" not in pm:
                pm["trust_flag"] = RQ_TRUST_FLAG.get(mk)
                changes.append(f"point_estimates.{rq}.per_model[{mk}].trust_flag = {RQ_TRUST_FLAG.get(mk)} [ADD]")

    return changes


# ===========================================================================
# Verification
# ===========================================================================

def verify(layer: dict, perf_before: dict) -> tuple[bool, list[str]]:
    issues: list[str] = []

    def req(cond, msg):
        if not cond:
            issues.append(msg)

    rm = layer["report_meta"]
    req(rm.get("schema_version") == "1.0", "report_meta.schema_version missing/wrong")
    req(isinstance(rm.get("risk_free_rate"), dict) and rm["risk_free_rate"].get("as_of") == "2026-04-09",
        "report_meta.risk_free_rate object/as_of wrong")
    req("2026-06-01" not in json.dumps(rm.get("risk_free_rate_note", "")) and
        "2026-06-01" not in json.dumps(rm.get("methodology_notes", {}).get("risk_free_rate", "")),
        "risk_free_rate as-of still references 2026-06-01")
    ds = {s["snapshot_id"]: s for s in rm["pinned_snapshots"]["deepseek"]["may_snapshots"]}
    req(ds.get("deepseek-v4-flash", {}).get("first_date") == "2026-04-24", "deepseek flash first_date != 2026-04-24")
    req(ds.get("deepseek-v4-pro", {}).get("first_date") == "2026-05-21", "deepseek v4-pro first_date != 2026-05-21")
    req(rm["spy_benchmark"].get("descriptive_sharpe") == 6.11, "spy_benchmark.descriptive_sharpe != 6.11")

    lb = {r["model"]: r for r in layer["leaderboard"]}
    for mk in MODELS:
        r = lb.get(mk, {})
        req("cumulative_return" not in r, f"leaderboard[{mk}] still has old cumulative_return")
        req("cumulative_return_inception" in r, f"leaderboard[{mk}].cumulative_return_inception missing")
        req(isinstance(r.get("cumulative_return_clean"), dict) and
            set(r["cumulative_return_clean"]) >= {"value", "source", "confidence", "model_spliced"},
            f"leaderboard[{mk}].cumulative_return_clean malformed")
        req("data_confidence" in r, f"leaderboard[{mk}].data_confidence missing")
    req("spy_benchmark" in lb and lb["spy_benchmark"]["cumulative_return_clean"]["value"] == 0.0637,
        "leaderboard SPY row missing / wrong clean value")

    for mk in MODELS:
        req("risk_basis" in (layer["performance"].get(mk) or {}), f"performance[{mk}].risk_basis missing")
    req(layer["performance"].get("spy_benchmark", {}).get("sharpe") == 6.11, "performance SPY descriptive sharpe missing")
    # TASK 4b: SPY descriptive-Sharpe single-source — performance row and report_meta must agree.
    req(layer["performance"].get("spy_benchmark", {}).get("sharpe") == rm["spy_benchmark"].get("descriptive_sharpe"),
        "SPY single-source: performance.spy_benchmark.sharpe != report_meta.spy_benchmark.descriptive_sharpe")

    req(layer["charts"].get("equity_curve_carries_shakedown_fabrication") is True,
        "charts.equity_curve_carries_shakedown_fabrication missing")

    cmb = layer["cross_model_behavioral"]
    req("rq1_herding_point_estimate" not in cmb, "old rq1_herding_point_estimate still present")
    req(cmb.get("rq1", {}).get("primary", {}).get("status") == "not_estimable", "rq1.primary not restructured")
    req("exploratory_pairwise" in cmb.get("rq1", {}), "rq1.exploratory_pairwise missing")
    req("cross_model_episode_register" in cmb, "cross_model_episode_register missing")

    for prof in layer["profiles"]:
        req("status" in prof, f"profiles[{prof['model']}].status missing")
        req("decision_completeness" in prof, f"profiles[{prof['model']}].decision_completeness missing")
        req("data_caveat" not in prof, f"profiles[{prof['model']}].data_caveat not dropped")
    pem = {p["model"]: p for p in layer["profiles"]}
    # risk_posture_tag is a RETAINED schema field (key present in every profile),
    # but populate-or-null: non-null for the estimable / performance-shown models,
    # null for the non_estimable_corrupt_book models. Field surviving != populated.
    RPT_POPULATED = ["gpt", "grok", "deepseek", "gemini"]
    for mk in MODELS:
        req("risk_posture_tag" in pem[mk],
            f"profiles[{mk}].risk_posture_tag field missing (schema: retained field)")
    for mk in RPT_POPULATED:
        req(pem[mk].get("risk_posture_tag") is not None,
            f"profiles[{mk}].risk_posture_tag null (schema: non-null for estimable/perf-shown)")
    for mk in NON_ESTIMABLE_MODELS:
        req(pem[mk]["evidence_metrics"]["rq2_disposition_difference"] is None and
            pem[mk]["evidence_metrics"]["rq3_confidence_outcome_corr"] is None,
            f"profiles[{mk}] RQ2/RQ3 not suppressed")
        req(pem[mk].get("style_tag") is None and pem[mk].get("risk_posture_tag") is None and
            pem[mk].get("strengths") is None and pem[mk].get("weaknesses") is None,
            f"profiles[{mk}] style_tag/risk_posture_tag/strengths/weaknesses not suppressed")

    mdr = layer["methodology_data_integrity_rq"]
    req("known_caveats" in mdr and "state_integrity" in mdr["known_caveats"] and
        "model_identity" in mdr["known_caveats"], "known_caveats incomplete")
    req("inclusion_gates" in mdr and mdr["inclusion_gates"].get("passing") == ["gpt", "grok"],
        "inclusion_gates missing/wrong")
    req("gate_scope_refinement" in mdr, "gate_scope_refinement missing")
    pe = mdr["rq_update"]["point_estimates"]
    req(pe["RQ1"].get("status") == "not_estimable", "RQ1.status missing")
    # RQ2/RQ3 six-model pools reclassified as superseded (NOT reported); per-model
    # GPT/Grok is the reported primary. The pool must never be flagged estimable --
    # it pools non-estimable (corrupt-book) and exploratory models.
    req(pe["RQ2"].get("status") == "superseded_six_model_pool" and
        pe["RQ3"].get("status") == "superseded_six_model_pool",
        "RQ2/RQ3 status not reclassified to superseded_six_model_pool")
    req(pe["RQ2"].get("reported") is False and pe["RQ3"].get("reported") is False,
        "RQ2/RQ3 six-model pool not flagged reported:false")
    for rq in ("RQ2", "RQ3"):
        rp = pe[rq].get("reported_primary", {})
        req(rp.get("status") == "estimable" and rp.get("cohort") == ["gpt", "grok"],
            f"{rq}.reported_primary missing / not the estimable GPT/Grok cohort")
        # integrity invariant: no figure flagged estimable pools a non-estimable model.
        req(not str(pe[rq].get("status", "")).startswith("estimable"),
            f"{rq} pool still flagged estimable while pooling non-estimable models")
        for mk, pm in pe[rq]["per_model"].items():
            req("trust_flag" in pm, f"{rq}.per_model[{mk}].trust_flag missing")
        # the reported-primary cohort (GPT, Grok) is individually estimable
        req(pe[rq]["per_model"]["gpt"].get("trust_flag") == "estimable" and
            pe[rq]["per_model"]["grok"].get("trust_flag") == "estimable",
            f"{rq}.per_model GPT/Grok not both individually estimable")

    # --- RQ-consistency cross-check: observation-weighted off-diagonal action_concordance
    #     == the (now relocated) RQ1 exploratory_pairwise concordance scalar ---
    cm = layer["charts"]["correlation_matrix"]
    ac, ns, mods = cm["action_concordance"], cm["n_shared_ticks"], cm["models"]
    num = den = 0.0
    for a in mods:
        for b in mods:
            if a >= b:
                continue
            v, w = ac[a][b], ns[a][b]
            if v is not None and w:
                num += v * w; den += w
    weighted = num / den if den else None
    scalar = cmb["rq1"]["exploratory_pairwise"]["observed_action_concordance"]
    reconciles = weighted is not None and abs(weighted - scalar) < 1e-9
    req(reconciles, f"RQ1-consistency FAIL: weighted off-diag mean {weighted} != scalar {scalar}")

    # --- performance returns unchanged (sharpe/vol/dd) ---
    for mk in MODELS:
        for f in ("sharpe", "volatility", "max_drawdown", "monthly_return"):
            before, after = perf_before[mk][f], layer["performance"][mk][f]
            req(before == after, f"performance[{mk}].{f} CHANGED {before} -> {after}")

    return (len(issues) == 0), issues, weighted, scalar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="verify only; do not write")
    args = ap.parse_args()

    with open(LAYER, encoding="utf-8") as f:
        layer = json.load(f)
    perf_before = {mk: dict(layer["performance"][mk]) for mk in MODELS}

    if not args.verify:
        if not os.path.exists(BACKUP):
            with open(BACKUP, "w", encoding="utf-8") as f:
                json.dump(layer, f, indent=2)
            print(f"[backup] wrote {os.path.relpath(BACKUP, ROOT)}")
        else:
            print(f"[backup] {os.path.relpath(BACKUP, ROOT)} already exists (kept)")
        changes = apply_backfill(layer)
        with open(LAYER, "w", encoding="utf-8") as f:
            json.dump(layer, f, indent=2)
        print(f"[write] {os.path.relpath(LAYER, ROOT)} ({len(changes)} mutations)\n")
        print("CHANGE DIFF")
        print("-" * 70)
        for c in changes:
            print(" ", c)
        print()

    ok, issues, weighted, scalar = verify(layer, perf_before)
    rq1_ok = weighted is not None and abs(weighted - scalar) < 1e-9
    tagged = ("RQ1-consistency", "SPY single-source", "SPY descriptive sharpe", "CHANGED")
    schema_issues = [i for i in issues if not any(t in i for t in tagged)]
    spy_ok = not any(("SPY single-source" in i or "SPY descriptive sharpe" in i) for i in issues)
    perf_ok = not any("CHANGED" in i for i in issues)
    print("CROSS-CHECK SUITE")
    print("-" * 70)
    print(f"  1. schema presence (populate-or-null)             : {'PASS' if not schema_issues else 'FAIL'}")
    print(f"  2. RQ1 herding consistency (matrix wmean==scalar) : {'PASS' if rq1_ok else 'FAIL'}  (w={weighted:.10f} s={scalar:.10f})")
    print(f"  3. SPY descriptive-Sharpe single-source           : {'PASS' if spy_ok else 'FAIL'}")
    print(f"  4. performance returns unchanged (sharpe/vol/dd)  : {'PASS' if perf_ok else 'FAIL'}")
    print(f"  -- RQ2/RQ3 : six-model pools SUPERSEDED (reported:false); per-model GPT/Grok reported primary (estimable); trust flags present")
    print(f"  -- RQ4/5/6 : accumulating inputs / Open / no-data -> OUT OF SCOPE for the monthly-layer cross-check (NOT asserted)")
    if issues:
        print("\n  ISSUES:")
        for i in issues:
            print("   -", i)
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
