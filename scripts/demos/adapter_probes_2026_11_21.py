"""Adapter probes for the refreshed cohort (hub 2026-11-21, item 2).

Off-cycle induced tests: a handful of calls per refreshed arm, outside every
workflow, no orders, short synthetic input (not the calibration set). They
enter no series. Per call this records the model identifier echoed, the
finish/stop reason, the full usage (reasoning tokens included), the effort or
level field as sent, whether the API reports one, and the temperature
outcome (accepted or rejected; honoured-vs-ignored is not decidable from a
handful of calls and is not claimed). No server-side fallback field is ever
sent. Keys are read from the environment and never printed or written.

Usage: python scripts/demos/adapter_probes_2026_11_21.py --out docs/ops/adapter_probes_2026-11-21
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config_loader import load_env  # noqa: E402

TIMEOUT = 600
SYSTEM = ('Respond with one JSON object only: {"answer": <integer>, "note": "<at most ten words>"}. '
          'No prose, no code fences.')
SHORT = "What is 17 * 23? Output json only."
HEAVY = ("How many ordered pairs of integers (x, y) satisfy x^2 + y^2 = 5^10? Reason carefully "
         "and check your count before answering. Output json only.")
SMALL_CAP = 300
# Round 2: a prompt that needs real reasoning, under a cap far below it. If
# reasoning counts against the cap, the call ends on the budget.
HARD = ("Let N be the number of ordered pairs of integers (x, y) with x^2 + y^2 = 5^10 * 13^4 * 17^2. "
        "Also let M be the number of those pairs with x > 0 and y > 0 and gcd(x, y) = 1. "
        "Work it out step by step internally and verify both counts. Output json with answer = N + M.")
TINY_CAP = 64

# $/1M tokens (input, output) for the cost line: Anthropic from the claude-api
# reference, xAI from its language-models API, OpenAI and Google from the
# provider pages saved 2026-11-21 (Argon's pre-December rate).
PRICES = {"claude-sonnet-5-5": (2, 10), "claude-opus-5-5": (4, 20), "gpt-6-astra": (10, 50),
          "grok-4.7": (2, 6), "gemini-4-argon": (2, 10)}


def _jsonable(obj: Any) -> Any:
    for attr in ("model_dump", "to_dict"):
        if hasattr(obj, attr):
            try:
                return getattr(obj, attr)(mode="json") if attr == "model_dump" else obj.to_dict()
            except TypeError:
                return getattr(obj, attr)()
    return obj


def _run(name: str, model: str, sent: dict, fn: Callable[[], Any]) -> dict:
    t0 = time.monotonic()
    rec: dict[str, Any] = {"probe": name, "model_requested": model, "sent": sent,
                           "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        raw = fn()
        rec["ok"] = True
        rec["raw"] = _jsonable(raw)
    except Exception as e:  # noqa: BLE001 -- a rejection is a result here
        rec["ok"] = False
        rec["error_type"] = type(e).__name__
        rec["error"] = str(e)[:2000]
        body = getattr(getattr(e, "response", None), "text", None)
        if body:
            rec["error_body"] = body[:4000]
    rec["latency_s"] = round(time.monotonic() - t0, 2)
    return rec


# ------------------------------------------------------------------ Anthropic
def anthropic_probes(model: str, effort: str) -> list[dict]:
    import anthropic
    c = anthropic.Anthropic(timeout=TIMEOUT, max_retries=0)

    def call(user, max_tokens=4096, **kw):
        return lambda: c.messages.create(model=model, max_tokens=max_tokens, system=SYSTEM,
                                         messages=[{"role": "user", "content": user}], **kw)
    th = {"type": "adaptive"}
    oc = {"effort": effort}
    return [
        _run("level_sent", model, {"thinking": th, "output_config": oc},
             call(SHORT, thinking=th, output_config=oc)),
        _run("level_omitted", model, {"thinking": "omitted", "output_config": "omitted"}, call(SHORT)),
        _run("temperature", model, {"thinking": th, "output_config": oc, "temperature": 0.3},
             call(SHORT, thinking=th, output_config=oc, temperature=0.3)),
        _run("small_cap_heavy", model, {"thinking": th, "output_config": oc, "max_tokens": SMALL_CAP},
             call(HEAVY, max_tokens=SMALL_CAP, thinking=th, output_config=oc)),
        _run("level_sent_heavy", model, {"thinking": th, "output_config": oc},
             call(HEAVY, thinking=th, output_config=oc, max_tokens=16384)),
    ]


# --------------------------------------------------------------------- OpenAI
def openai_probes(model: str) -> list[dict]:
    import openai
    c = openai.OpenAI(timeout=TIMEOUT, max_retries=0)
    msgs = lambda u: [{"role": "system", "content": SYSTEM}, {"role": "user", "content": u}]  # noqa: E731
    fmt = {"type": "json_object"}
    return [
        _run("chat_level_omitted", model, {"api": "chat.completions", "reasoning_effort": "omitted",
                                           "response_format": fmt},
             lambda: c.chat.completions.create(model=model, messages=msgs(SHORT), response_format=fmt,
                                               max_completion_tokens=16384)),
        _run("responses_level_omitted", model, {"api": "responses", "reasoning": "omitted"},
             lambda: c.responses.create(model=model, instructions=SYSTEM, input=SHORT,
                                        max_output_tokens=16384)),
        _run("temperature", model, {"api": "chat.completions", "temperature": 0.3},
             lambda: c.chat.completions.create(model=model, messages=msgs(SHORT), response_format=fmt,
                                               max_completion_tokens=16384, temperature=0.3)),
        _run("small_cap_heavy", model, {"api": "chat.completions", "max_completion_tokens": SMALL_CAP},
             lambda: c.chat.completions.create(model=model, messages=msgs(HEAVY), response_format=fmt,
                                               max_completion_tokens=SMALL_CAP)),
        _run("chat_level_omitted_heavy", model, {"api": "chat.completions"},
             lambda: c.chat.completions.create(model=model, messages=msgs(HEAVY), response_format=fmt,
                                               max_completion_tokens=16384)),
    ]


# ------------------------------------------------------------------------ xAI
def xai_probes(model: str) -> list[dict]:
    import requests
    key = os.environ.get("XAI_API_KEY", "")
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def chat(user, **extra):
        def f():
            body = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                                 {"role": "user", "content": user}],
                    "response_format": {"type": "json_object"}, **extra}
            body.setdefault("max_tokens", 16384)
            r = requests.post("https://api.x.ai/v1/chat/completions", headers=hdr, json=body,
                              timeout=TIMEOUT)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1500]}")
            return r.json()
        return f

    def responses(user, **extra):
        def f():
            body = {"model": model, "instructions": SYSTEM, "input": user, **extra}
            r = requests.post("https://api.x.ai/v1/responses", headers=hdr, json=body, timeout=TIMEOUT)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1500]}")
            return r.json()
        return f
    out = [
        _run("chat_level_sent", model, {"api": "chat.completions", "reasoning_effort": "high"},
             chat(SHORT, reasoning_effort="high")),
        _run("chat_level_omitted", model, {"api": "chat.completions", "reasoning_effort": "omitted"},
             chat(SHORT)),
        _run("temperature", model, {"api": "chat.completions", "reasoning_effort": "high",
                                    "temperature": 0.3},
             chat(SHORT, reasoning_effort="high", temperature=0.3)),
        _run("small_cap_heavy", model, {"api": "chat.completions", "reasoning_effort": "high",
                                        "max_tokens": SMALL_CAP},
             chat(HEAVY, reasoning_effort="high", max_tokens=SMALL_CAP)),
        _run("chat_level_sent_heavy", model, {"api": "chat.completions", "reasoning_effort": "high"},
             chat(HEAVY, reasoning_effort="high")),
    ]
    if not out[0]["ok"]:
        out.append(_run("responses_level_sent", model, {"api": "responses",
                                                        "reasoning": {"effort": "high"}},
                        responses(SHORT, reasoning={"effort": "high"})))
    return out


# --------------------------------------------------------------------- Google
def gemini_probes(model: str) -> list[dict]:
    from google import genai
    from google.genai import types as gt
    c = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))

    def call(user, level=None, max_out=16384, temperature=None):
        cfg = gt.GenerateContentConfig(
            system_instruction=SYSTEM, response_mime_type="application/json",
            max_output_tokens=max_out, temperature=temperature,
            thinking_config=gt.ThinkingConfig(thinking_level=level) if level else None,
            http_options=gt.HttpOptions(timeout=TIMEOUT * 1000))
        return lambda: c.models.generate_content(model=model, contents=user, config=cfg)
    return [
        _run("level_sent", model, {"thinking_level": "MEDIUM"}, call(SHORT, "MEDIUM")),
        _run("level_omitted", model, {"thinking_level": "omitted"}, call(SHORT)),
        _run("temperature", model, {"thinking_level": "MEDIUM", "temperature": 0.3},
             call(SHORT, "MEDIUM", temperature=0.3)),
        _run("small_cap_heavy", model, {"thinking_level": "MEDIUM", "max_output_tokens": SMALL_CAP},
             call(HEAVY, "MEDIUM", max_out=SMALL_CAP)),
        _run("level_sent_heavy", model, {"thinking_level": "MEDIUM"}, call(HEAVY, "MEDIUM")),
        _run("level_omitted_heavy", model, {"thinking_level": "omitted"}, call(HEAVY)),
    ]


# -------------------------------------------------------------------- round 2
def round2(model: str) -> list[dict]:
    """Send/omit pairs on HARD (equivalence by reasoning-token counts) and
    HARD under TINY_CAP (does reasoning count against the cap?)."""
    if model.startswith("claude"):
        import anthropic
        c = anthropic.Anthropic(timeout=TIMEOUT, max_retries=0)
        effort = "high" if "sonnet" in model else "medium"
        th, oc = {"type": "adaptive"}, {"effort": effort}

        def call(user, max_tokens=16384, **kw):
            return lambda: c.messages.create(model=model, max_tokens=max_tokens, system=SYSTEM,
                                             messages=[{"role": "user", "content": user}], **kw)
        return [_run("level_sent", model, {"thinking": th, "output_config": oc},
                     call(SHORT, thinking=th, output_config=oc)),
                _run("hard_level_sent", model, {"thinking": th, "output_config": oc},
                     call(HARD, thinking=th, output_config=oc)),
                _run("hard_level_omitted", model, {"thinking": "omitted", "output_config": "omitted"},
                     call(HARD)),
                _run("hard_tiny_cap", model, {"thinking": th, "output_config": oc,
                                              "max_tokens": TINY_CAP},
                     call(HARD, max_tokens=TINY_CAP, thinking=th, output_config=oc))]
    if model == "gpt-6-astra":
        import openai
        c = openai.OpenAI(timeout=TIMEOUT, max_retries=0)
        fmt = {"type": "json_object"}

        def chat(user, cap=16384, **kw):
            return lambda: c.chat.completions.create(
                model=model, messages=[{"role": "system", "content": SYSTEM},
                                       {"role": "user", "content": user}],
                response_format=fmt, max_completion_tokens=cap, **kw)
        return [_run("hard_level_sent_medium", model, {"api": "chat.completions",
                                                       "reasoning_effort": "medium"},
                     chat(HARD, reasoning_effort="medium")),
                _run("hard_level_omitted", model, {"api": "chat.completions",
                                                   "reasoning_effort": "omitted"}, chat(HARD)),
                _run("hard_tiny_cap", model, {"api": "chat.completions",
                                              "max_completion_tokens": TINY_CAP},
                     chat(HARD, cap=TINY_CAP)),
                _run("responses_level_omitted_hard", model, {"api": "responses", "reasoning": "omitted"},
                     lambda: c.responses.create(model=model, instructions=SYSTEM, input=HARD,
                                                max_output_tokens=16384))]
    if model == "grok-4.7":
        import requests
        hdr = {"Authorization": f"Bearer {os.environ.get('XAI_API_KEY', '')}",
               "Content-Type": "application/json"}

        def chat(user, **extra):
            def f():
                body = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                                     {"role": "user", "content": user}],
                        "response_format": {"type": "json_object"}, "max_tokens": 16384, **extra}
                r = requests.post("https://api.x.ai/v1/chat/completions", headers=hdr, json=body,
                                  timeout=TIMEOUT)
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1500]}")
                return r.json()
            return f
        return [_run("hard_level_sent", model, {"api": "chat.completions", "reasoning_effort": "high"},
                     chat(HARD, reasoning_effort="high")),
                _run("hard_level_omitted", model, {"api": "chat.completions",
                                                   "reasoning_effort": "omitted"}, chat(HARD)),
                _run("hard_tiny_cap", model, {"api": "chat.completions", "reasoning_effort": "high",
                                              "max_tokens": TINY_CAP},
                     chat(HARD, reasoning_effort="high", max_tokens=TINY_CAP))]
    if model == "gemini-4-argon":
        from google import genai
        from google.genai import types as gt
        c = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))

        def call(user, level=None, max_out=16384):
            cfg = gt.GenerateContentConfig(
                system_instruction=SYSTEM, response_mime_type="application/json",
                max_output_tokens=max_out,
                thinking_config=gt.ThinkingConfig(thinking_level=level) if level else None,
                http_options=gt.HttpOptions(timeout=TIMEOUT * 1000))
            return lambda: c.models.generate_content(model=model, contents=user, config=cfg)
        return [_run("hard_level_sent", model, {"thinking_level": "MEDIUM"}, call(HARD, "MEDIUM")),
                _run("hard_level_omitted", model, {"thinking_level": "omitted"}, call(HARD)),
                _run("hard_tiny_cap", model, {"thinking_level": "MEDIUM", "max_output_tokens": TINY_CAP},
                     call(HARD, "MEDIUM", max_out=TINY_CAP))]
    raise ValueError(model)


# -------------------------------------------------------------------- summary
def summarize(rec: dict) -> dict:
    r = rec.get("raw") or {}
    s: dict[str, Any] = {"probe": rec["probe"], "ok": rec["ok"], "latency_s": rec["latency_s"]}
    if not rec["ok"]:
        s["error"] = (rec.get("error") or "")[:300]
        return s
    if "stop_reason" in r:                                     # Anthropic
        u = r.get("usage") or {}
        s.update(model=r.get("model"), finish=r.get("stop_reason"), input=u.get("input_tokens"),
                 output=u.get("output_tokens"),
                 reasoning=(u.get("output_tokens_details") or {}).get("thinking_tokens"),
                 blocks=[b.get("type") for b in r.get("content", [])])
    elif "choices" in r:                                       # chat completions
        u = r.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        s.update(model=r.get("model"), finish=r["choices"][0].get("finish_reason"),
                 input=u.get("prompt_tokens"), output=u.get("completion_tokens"),
                 reasoning=det.get("reasoning_tokens"),
                 text=(r["choices"][0].get("message") or {}).get("content", "")[:120])
    elif "output" in r and "usage" in r:                       # responses
        u = r.get("usage") or {}
        s.update(model=r.get("model"), finish=r.get("status"), reasoning_reported=r.get("reasoning"),
                 input=u.get("input_tokens"), output=u.get("output_tokens"),
                 reasoning=(u.get("output_tokens_details") or {}).get("reasoning_tokens"))
    elif "candidates" in r:                                    # Gemini
        u = r.get("usage_metadata") or {}
        cand = (r.get("candidates") or [{}])[0]
        s.update(model=r.get("model_version"), finish=cand.get("finish_reason"),
                 input=u.get("prompt_token_count"), output=u.get("candidates_token_count"),
                 reasoning=u.get("thoughts_token_count"), total=u.get("total_token_count"))
    return s


def cost_usd(model: str, s: dict) -> float:
    """Billed cost of one call from its usage. Gemini and xAI bill reasoning as
    output but report it outside the output count (candidates_token_count;
    completion_tokens, where xAI's total = prompt + completion + reasoning).
    Anthropic and OpenAI output counts already include it."""
    pi, po = PRICES[model]
    out = (s.get("output") or 0)
    if model.startswith(("gemini", "grok")):
        out += s.get("reasoning") or 0
    return ((s.get("input") or 0) * pi + out * po) / 1e6


def main() -> int:
    ap = argparse.ArgumentParser(description="Refreshed-cohort adapter probes (induced tests).")
    ap.add_argument("--out", required=True)
    ap.add_argument("--round", type=int, choices=(1, 2), default=1)
    a = ap.parse_args()
    load_env()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    models = ("claude-sonnet-5-5", "claude-opus-5-5", "gpt-6-astra", "grok-4.7", "gemini-4-argon")
    if a.round == 1:
        arms = {"claude-sonnet-5-5": lambda: anthropic_probes("claude-sonnet-5-5", "high"),
                "claude-opus-5-5": lambda: anthropic_probes("claude-opus-5-5", "medium"),
                "gpt-6-astra": lambda: openai_probes("gpt-6-astra"),
                "grok-4.7": lambda: xai_probes("grok-4.7"),
                "gemini-4-argon": lambda: gemini_probes("gemini-4-argon")}
    else:
        arms = {m: (lambda m=m: round2(m)) for m in models}
    with cf.ThreadPoolExecutor(max_workers=len(arms)) as pool:
        futs = {pool.submit(fn): m for m, fn in arms.items()}
        results = {futs[f]: f.result() for f in cf.as_completed(futs)}
    mpath = out / "MANIFEST.json"
    manifest = (json.loads(mpath.read_text(encoding="utf-8")) if a.round == 2 and mpath.exists() else
                {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "purpose": "Hub 2026-11-21 item 2: refreshed-cohort adapter probes. Induced tests; "
                            "enter no series. Synthetic input, not the calibration set. No orders. "
                            "No server-side fallback field sent.",
                 "system": SYSTEM, "inputs": {"short": SHORT, "heavy": HEAVY},
                 "small_cap": SMALL_CAP, "files": [], "summary": {}})
    if a.round == 2:
        manifest["round2_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest["inputs"]["hard"] = HARD
        manifest["tiny_cap"] = TINY_CAP
    suffix = "" if a.round == 1 else "_round2"
    for model, recs in results.items():
        p = out / f"{model}{suffix}.json"
        p.write_text(json.dumps(recs, indent=2, ensure_ascii=False, default=str) + "\n",
                     encoding="utf-8", newline="\n")
        manifest["files"].append({"file": p.name, "bytes": p.stat().st_size,
                                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        key = model + suffix
        manifest["summary"][key] = [summarize(r) for r in recs]
        for s in manifest["summary"][key]:
            s["cost_usd"] = round(cost_usd(model, s), 5) if s["ok"] else 0.0
    # Re-summarize every saved file from its raw records, so the summary of an
    # earlier round reflects the current summarizer.
    for f in manifest["files"]:
        key = f["file"][:-len(".json")]
        model = key.replace("_round2", "")
        recs = json.loads((out / f["file"]).read_text(encoding="utf-8"))
        manifest["summary"][key] = [summarize(r) for r in recs]
        for s in manifest["summary"][key]:
            s["cost_usd"] = round(cost_usd(model, s), 5) if s["ok"] else 0.0
    manifest["total_cost_usd"] = round(sum(s["cost_usd"] for v in manifest["summary"].values()
                                           for s in v), 4)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False,
                                                  default=str) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest["summary"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
