"""O-2 per-model API-configuration verification (read-only; no pipeline, no broker, no writes to data/).

Runs from the repo root with the production adapters and config/settings.json models.
Per model, on one fixed high-entropy prompt:
  D1, D2  production adapter, temperature=None (exactly the live wire body)
  Z1, Z2  same adapter, temperature=0.0 (the RQ6 probe path), except DeepSeek,
          whose adapter never sends temperature (so a Z call would test nothing)
Reads: the model id returned, the adapter's metadata (reasoning tokens, finish
reason), and for Anthropic the response's content-block types, observed by a
call-through wrapper on the SDK method. The adapter code is not changed.
Temperature verdict: honoured if Z1 == Z2 while D1 != D2 (the D pair is the
positive control that the prompt varies at the default); rejected if the
provider returns an error at temperature=0; ignored if the Z pair varies like
the D pair; inconclusive if the D pair does not vary.
"""
import json
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, ".")
from src.config_loader import load_env, load_settings  # noqa: E402
from src.adapters import get_adapter  # noqa: E402

OUT = sys.argv[1]
SYSTEM = "You are a test harness. Follow the instruction exactly."
USER = 'Output a random 10-digit number as JSON, exactly {"n": <number>}, and nothing else.'
ORDER = ["claude", "claude_opus", "gpt", "gemini", "grok", "deepseek"]

# Observe Anthropic content-block types without altering the adapter.
_seen_blocks: list[list[str]] = []
try:
    import anthropic.resources.messages as _am
    _orig_create = _am.Messages.create

    def _wrapped(self, *a, **k):
        r = _orig_create(self, *a, **k)
        _seen_blocks.append([getattr(b, "type", "?") for b in r.content])
        return r
    _am.Messages.create = _wrapped
except Exception:  # noqa: BLE001
    traceback.print_exc()


def call(provider, model, temperature):
    ad = get_adapter(provider, model, temperature=temperature)
    n0 = len(_seen_blocks)
    t = time.perf_counter()
    try:
        raw, rid, md = ad._call_api(SYSTEM, USER)
        out = {"ok": True, "text": (raw or "").strip()[:200], "model_id_returned": rid, "metadata": md}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:600]}"}
    out["latency_s"] = round(time.perf_counter() - t, 2)
    out["temperature_sent"] = temperature
    out["at"] = datetime.now(timezone.utc).isoformat()
    if len(_seen_blocks) > n0:
        out["anthropic_block_types"] = _seen_blocks[-1]
    return out


def main():
    load_env()
    s = load_settings()
    res = {"run_at": datetime.now(timezone.utc).isoformat(), "system": SYSTEM, "user": USER, "models": {}}
    for k in ORDER:
        cfg = s["models"][k]
        prov, model = cfg["provider"], cfg["model"]
        r = {"provider": prov, "model_configured": model, "calls": {}}
        for tag in ("D1", "D2"):
            r["calls"][tag] = call(prov, model, None)
        if k != "deepseek":
            for tag in ("Z1", "Z2"):
                r["calls"][tag] = call(prov, model, 0.0)
        res["models"][k] = r
        print(k, {t: (c.get("text") if c["ok"] else "ERR " + c["error"][:120]) for t, c in r["calls"].items()}, flush=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), indent=1, default=str)


if __name__ == "__main__":
    main()
