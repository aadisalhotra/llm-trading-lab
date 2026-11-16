"""Logging-only proof for the 2026-11-15 logging package: every adapter's
request is byte-identical before and after.

The package adds record fields (api_error_class, model_version_raw,
num_sources_used, citations) and must not touch what goes on the wire. This
test runs, in one process against the SAME installed SDKs:

  * the adapter sources as they stood at 3ec5024f1, copied verbatim into
    tests/fixtures/adapters_pre_logging_pkg/*.py.txt (blob ids pinned below),
  * the current adapters in src/adapters/,

through one fake transport that records the exact bytes each would send —
Client.send on every installed httpx-family client for the three SDK adapters
(Anthropic, OpenAI, Gemini), requests.Session.send for the two raw-REST
adapters (xAI, DeepSeek) — and
asserts method, URL and body bytes are equal for each adapter, with and
without chart images. Nothing is sent: the transports are replaced and socket
connects are refused, so a send that escapes the fakes fails the test instead
of reaching a provider.

Why "httpx-family": the CI lock (requirements.txt) has anthropic 2.3.0 and
openai 3.37.0, which send through the `httpx2` package, while google-genai
2.33.0 still uses `httpx`. A machine with older SDKs may use `httpx` for all
three. Both are patched whenever importable.

Comparing old against new in the same interpreter is what makes this
SDK-version-independent. A stored golden body would depend on the local SDK
(this machine and CI's requirements lock differ), and would fail for the
SDK's reasons, not ours.

The fixtures are pinned to this package. A future change that deliberately
alters a request body must re-snapshot them, which is the point: the change
then shows up in a diff instead of inside a "logging" commit.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import socket

import httpx
import pytest
import requests

try:  # the CI lock's anthropic/openai transport; absent on older installs
    import httpx2
except ImportError:  # pragma: no cover - depends on the installed SDKs
    httpx2 = None

HTTPX_FAMILY = [m for m in (httpx, httpx2) if m is not None]

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "adapters_pre_logging_pkg"
BASE_COMMIT = "3ec5024f1"

# git blob ids of src/adapters/<name>_adapter.py at BASE_COMMIT
# (`git rev-parse 3ec5024f1:src/adapters/<name>_adapter.py`).
PINNED_BLOBS = {
    "anthropic": "4b98b2da12c4a57d33b9b5af89da1bc0ad71c24b",
    "openai": "698c9c607b9e8cb4f3166eacf629b49cb2d80733",
    "gemini": "71b603c75a7f73377e56efe4801b436788b01cbd",
    "xai": "bb6e47c20b3f75be46fcff9f480c1d49e4ff5d24",
    "deepseek": "2587e8ada3d1c2da68ff75169d84bb267bb6a6d4",
}

CLASS_NAMES = {
    "anthropic": "AnthropicAdapter",
    "openai": "OpenAIAdapter",
    "gemini": "GeminiAdapter",
    "xai": "XAIAdapter",
    "deepseek": "DeepSeekAdapter",
}

# The configured production ids (config/settings.json), so cost lookups and
# any id-dependent request shaping run exactly as in production.
MODEL_IDS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.4-2026-03-05",
    "gemini": "gemini-3.1-pro-preview",
    "xai": "grok-4.20-0309-reasoning",
    "deepseek": "deepseek-v4-pro",
}

ENV_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
            "XAI_API_KEY", "DEEPSEEK_API_KEY")

SYSTEM = "system prompt — with a non-ASCII dash"
USER = 'user prompt {"json": "inside", "n": 1}'
IMAGES = [b"\x89PNG\r\n\x1a\nfake-chart-1", b"\x89PNG\r\n\x1a\nfake-chart-2"]

_DECISION_JSON = '{"decisions": [], "overall_reasoning": "x"}'

_HTTPX_RESPONSES = {
    "api.openai.com": {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0,
        "model": "gpt-5.4-2026-03-05",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": _DECISION_JSON}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    },
    "api.anthropic.com": {
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": _DECISION_JSON}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    },
    "generativelanguage.googleapis.com": {
        "candidates": [{"content": {"role": "model", "parts": [{"text": _DECISION_JSON}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        "modelVersion": "gemini-3.1-pro-preview",
    },
}

_REQUESTS_RESPONSE = {
    "id": "x", "model": "m",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": _DECISION_JSON}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "num_sources_used": 0},
}


def _git_blob_id(data: bytes) -> str:
    # Normalize to LF: the object store holds LF; a Windows checkout may not.
    data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _load_pre_package(name: str):
    """Import a fixture copy as a sibling of src.adapters so its relative
    imports (.base, ..analytics.cost_rates) resolve exactly as the original's."""
    import src.adapters  # noqa: F401 — the parent package must be importable

    path = FIXTURES / f"{name}_adapter.py.txt"
    mod_name = f"src.adapters._pre_logging_pkg_{name}"
    loader = importlib.machinery.SourceFileLoader(mod_name, str(path))
    spec = importlib.util.spec_from_loader(mod_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    loader.exec_module(mod)
    return getattr(mod, CLASS_NAMES[name])


def _load_current(name: str):
    mod = importlib.import_module(f"src.adapters.{name}_adapter")
    return getattr(mod, CLASS_NAMES[name])


@pytest.fixture
def wire(monkeypatch):
    """Replace both transports; collect (method, url, body bytes) per send."""
    sent: list[tuple[str, str, bytes]] = []

    def make_httpx_send(lib):
        def fake_httpx_send(self, request, *args, **kwargs):
            body = request.read()
            sent.append((request.method, str(request.url), body))
            payload = _HTTPX_RESPONSES[request.url.host]
            return lib.Response(200, json=payload, request=request)
        return fake_httpx_send

    def fake_requests_send(self, prepared, **kwargs):
        body = prepared.body
        if isinstance(body, str):
            body = body.encode("utf-8")
        sent.append((prepared.method, prepared.url, body or b""))
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps(_REQUESTS_RESPONSE).encode("utf-8")
        resp.headers["Content-Type"] = "application/json"
        resp.url = prepared.url
        resp.request = prepared
        return resp

    def refuse_connect(self, *a, **kw):
        raise OSError("test_adapter_request_invariance: outbound connect refused")

    for lib in HTTPX_FAMILY:
        monkeypatch.setattr(lib.Client, "send", make_httpx_send(lib))
    monkeypatch.setattr(requests.Session, "send", fake_requests_send)
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    for k in ENV_KEYS:
        monkeypatch.setenv(k, "test-key-not-real")
    return sent


@pytest.mark.parametrize("name", sorted(PINNED_BLOBS))
def test_fixture_is_the_pre_package_source(name):
    data = (FIXTURES / f"{name}_adapter.py.txt").read_bytes()
    assert _git_blob_id(data) == PINNED_BLOBS[name], (
        f"{name} fixture is not the {BASE_COMMIT} source")


@pytest.mark.parametrize("with_images", [False, True], ids=["text", "images"])
@pytest.mark.parametrize("name", sorted(PINNED_BLOBS))
def test_request_bytes_identical_before_and_after(name, with_images, wire):
    images = IMAGES if with_images else None
    for cls in (_load_pre_package(name), _load_current(name)):
        cls(MODEL_IDS[name])._call_api(SYSTEM, USER, images)
    assert len(wire) == 2, f"expected one request per adapter version, got {len(wire)}"
    (m0, u0, b0), (m1, u1, b1) = wire
    assert (m0, u0) == (m1, u1)
    assert b0, "captured an empty body: the fake transport saw nothing"
    assert b0 == b1, (
        f"{name} request body changed:\nbefore: {b0[:400]!r}\nafter:  {b1[:400]!r}")


@pytest.mark.parametrize("name", sorted(PINNED_BLOBS))
def test_probe_temperature_request_identical_too(name, wire):
    """The RQ6 determinism probe sets temperature=0.0; that path must behave
    identically too. Compared as outcomes, not only bodies: under the CI lock's
    anthropic 2.3.0, Messages.create() no longer accepts `temperature`, so the
    Claude call raises TypeError before anything is sent, in the old adapter and
    the new one alike. That is pre-existing (reported with this package, not
    changed by it); what this test pins is that the package did not alter it."""
    outcomes = []
    for cls in (_load_pre_package(name), _load_current(name)):
        before = len(wire)
        try:
            cls(MODEL_IDS[name], temperature=0.0)._call_api(SYSTEM, USER, None)
        except Exception as e:  # noqa: BLE001 - the outcome under comparison
            outcomes.append(("raised", type(e).__name__, str(e), len(wire) - before))
        else:
            outcomes.append(("sent",) + wire[-1])
    assert outcomes[0] == outcomes[1]
