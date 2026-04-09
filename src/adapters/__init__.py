"""Model adapter layer.

Every provider implements `BaseAdapter.generate_decision()` and returns a
parsed `DecisionResult`. The factory below resolves an adapter by provider
name so the rest of the pipeline never imports provider SDKs directly.
"""
from __future__ import annotations

from .base import BaseAdapter, DecisionResult
from .anthropic_adapter import AnthropicAdapter
from .openai_adapter import OpenAIAdapter
from .gemini_adapter import GeminiAdapter
from .xai_adapter import XAIAdapter
from .deepseek_adapter import DeepSeekAdapter

_REGISTRY: dict[str, type[BaseAdapter]] = {
    "anthropic": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "google": GeminiAdapter,
    "xai": XAIAdapter,
    "deepseek": DeepSeekAdapter,
}


def get_adapter(provider: str, model: str) -> BaseAdapter:
    """Resolve an adapter for the given provider name."""
    key = provider.lower()
    if key not in _REGISTRY:
        raise ValueError(f"Unknown provider: {provider}. Known: {list(_REGISTRY)}")
    return _REGISTRY[key](model=model)


__all__ = [
    "BaseAdapter",
    "DecisionResult",
    "get_adapter",
    "AnthropicAdapter",
    "OpenAIAdapter",
    "GeminiAdapter",
    "XAIAdapter",
    "DeepSeekAdapter",
]
