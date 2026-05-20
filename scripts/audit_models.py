"""Audit each provider's live model catalog vs config/settings.json.

Hits the /v1/models list endpoint on each of the 5 providers we use, dumps
the full catalog, and flags whether the model_id we currently have wired
up appears in the provider's live inventory. This is the source of truth
for "is our config still pointing at a real model" — much more reliable
than scraping docs because each catalog comes from the provider itself.

Run as:
    python -m scripts.audit_models

Reads keys from .env via the existing config_loader. Prints to stdout.
Non-zero exit if any configured model is missing from its provider's
catalog (so this can be wired into CI as a sanity check later if you want).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests

# Allow running as a script from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config_loader import force_utf8_console, load_env, load_settings  # noqa: E402

HTTP_TIMEOUT = 30


def fetch_anthropic_models() -> list[dict[str, Any]]:
    """GET https://api.anthropic.com/v1/models — returns list of {id, display_name, type, created_at}"""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    r = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("data") or []


def fetch_openai_models() -> list[dict[str, Any]]:
    """GET https://api.openai.com/v1/models"""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing")
    r = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return (r.json() or {}).get("data") or []


def fetch_xai_models() -> list[dict[str, Any]]:
    """GET https://api.x.ai/v1/models — OpenAI-compatible shape"""
    key = os.getenv("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY missing")
    r = requests.get(
        "https://api.x.ai/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return (r.json() or {}).get("data") or []


def fetch_deepseek_models() -> list[dict[str, Any]]:
    """GET https://api.deepseek.com/v1/models — OpenAI-compatible shape"""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    r = requests.get(
        "https://api.deepseek.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return (r.json() or {}).get("data") or []


def fetch_google_models() -> list[dict[str, Any]]:
    """Use the google-generativeai SDK to list models. Falls back to the
    REST endpoint if the SDK isn't installed in the env this runs from.
    """
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY missing")
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        out = []
        for m in genai.list_models():
            out.append({
                "name": m.name,
                "display_name": getattr(m, "display_name", ""),
                "supported_actions": list(getattr(m, "supported_generation_methods", []) or []),
                "input_token_limit": getattr(m, "input_token_limit", None),
                "output_token_limit": getattr(m, "output_token_limit", None),
            })
        return out
    except ImportError:
        # REST fallback
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return (r.json() or {}).get("models") or []


def main() -> int:
    force_utf8_console()
    load_env()
    settings = load_settings()

    providers = {
        "anthropic": (fetch_anthropic_models, "id"),
        "openai":    (fetch_openai_models, "id"),
        "google":    (fetch_google_models, "name"),
        "xai":       (fetch_xai_models, "id"),
        "deepseek":  (fetch_deepseek_models, "id"),
    }

    # Map our model_keys to (provider, model_id) for the comparison
    configured: dict[str, list[tuple[str, str]]] = {}
    for key, cfg in (settings.get("models") or {}).items():
        provider = cfg.get("provider")
        model = cfg.get("model")
        configured.setdefault(provider, []).append((key, model))

    any_missing = False
    for provider, (fetcher, id_field) in providers.items():
        print(f"\n{'=' * 70}")
        print(f"PROVIDER: {provider}")
        print('=' * 70)
        try:
            catalog = fetcher()
        except Exception as e:
            print(f"  ERROR fetching catalog: {e}")
            continue

        # Print the live catalog
        print(f"  Live catalog ({len(catalog)} entries):")
        for entry in catalog:
            mid = entry.get(id_field) or entry.get("id") or entry.get("name") or "?"
            display = entry.get("display_name") or entry.get("name") or ""
            extra = ""
            if "input_token_limit" in entry and entry.get("input_token_limit"):
                extra = f"  ctx={entry['input_token_limit']}"
            print(f"    - {mid:50}  {display}{extra}")

        # Check our configured strings against the catalog
        ours = configured.get(provider, [])
        if not ours:
            continue
        print(f"  Our config:")
        catalog_ids = set()
        for entry in catalog:
            mid = entry.get(id_field) or entry.get("id") or entry.get("name") or ""
            catalog_ids.add(mid)
            # For Google, also strip the "models/" prefix for matching
            if mid.startswith("models/"):
                catalog_ids.add(mid[len("models/"):])
        for our_key, our_model in ours:
            in_catalog = our_model in catalog_ids
            status = "OK" if in_catalog else "MISSING"
            print(f"    [{status}] {our_key} -> {our_model}")
            if not in_catalog:
                any_missing = True

    print()
    if any_missing:
        print("WARNING: one or more configured model_ids are NOT in the provider's live catalog.")
        return 2
    print("All configured model_ids are present in their providers' live catalogs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
