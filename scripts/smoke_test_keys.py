"""One-shot smoke test for every API key. Run before Phase A go-live."""
import os
import sys
import logging
import traceback
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config_loader import force_utf8_console  # noqa: E402

force_utf8_console()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("smoke")

ROOT = Path(__file__).resolve().parents[1]
# Load .env.txt first (per Windows gotcha), then .env as fallback
load_dotenv(ROOT / ".env.txt")
load_dotenv(ROOT / ".env")

results = []


def record(name, ok, status, detail):
    results.append((name, ok, status, detail))
    log.info("%-10s %s  status=%s  %s", name, "PASS" if ok else "FAIL", status, detail)


def test_anthropic():
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16,
            messages=[{"role": "user", "content": "ping"}],
        )
        text = resp.content[0].text.strip() if resp.content else ""
        record("Anthropic", bool(text), 200, f"reply={text!r}")
    except Exception as e:
        record("Anthropic", False, getattr(e, "status_code", "ERR"), str(e))


def test_openai():
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.chat.completions.create(
            model="gpt-5.4",
            max_completion_tokens=16,
            messages=[{"role": "user", "content": "ping"}],
        )
        text = (resp.choices[0].message.content or "").strip()
        record("OpenAI", True, 200, f"reply={text!r}")
    except Exception as e:
        record("OpenAI", False, getattr(e, "status_code", "ERR"), str(e))


def test_google():
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        model = genai.GenerativeModel("gemini-3.1-pro-preview")
        resp = model.generate_content("ping")
        text = (resp.text or "").strip()
        record("Google", True, 200, f"reply={text!r}")
    except Exception as e:
        record("Google", False, "ERR", str(e))


def test_xai():
    try:
        r = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}"},
            json={
                "model": "grok-4.20-0309-reasoning",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 16,
            },
            timeout=60,
        )
        ok = r.status_code == 200
        detail = r.json().get("choices", [{}])[0].get("message", {}).get("content", "") if ok else r.text[:200]
        record("xAI", ok, r.status_code, f"reply={detail!r}" if ok else detail)
    except Exception as e:
        record("xAI", False, "ERR", str(e))


def test_deepseek():
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"},
            json={
                "model": "deepseek-reasoner",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 16,
            },
            timeout=60,
        )
        ok = r.status_code == 200
        detail = r.json().get("choices", [{}])[0].get("message", {}).get("content", "") if ok else r.text[:200]
        record("DeepSeek", ok, r.status_code, f"reply={detail!r}" if ok else detail)
    except Exception as e:
        record("DeepSeek", False, "ERR", str(e))


def test_alpaca():
    try:
        base = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        r = requests.get(
            f"{base}/v2/account",
            headers={
                "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
            },
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            detail = f"id={j.get('id','?')[:8]} status={j.get('status')} equity={j.get('equity')}"
        else:
            detail = r.text[:200]
        record("Alpaca", ok, r.status_code, detail)
    except Exception as e:
        record("Alpaca", False, "ERR", str(e))


def test_finnhub():
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": "AAPL",
                "from": "2026-04-01",
                "to": "2026-04-12",
                "token": os.environ["FINNHUB_API_KEY"],
            },
            timeout=30,
        )
        ok = r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0
        if ok:
            detail = f"{len(r.json())} headlines, first={r.json()[0].get('headline','')[:60]!r}"
        else:
            detail = r.text[:200] if r.status_code != 200 else "empty list"
        record("Finnhub", ok, r.status_code, detail)
    except Exception as e:
        record("Finnhub", False, "ERR", str(e))


def test_newsapi():
    try:
        r = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"category": "business", "language": "en", "pageSize": 1},
            headers={"X-Api-Key": os.environ["NEWS_API_KEY"]},
            timeout=30,
        )
        j = r.json()
        ok = r.status_code == 200 and j.get("status") == "ok" and j.get("articles")
        if ok:
            detail = f"{j.get('totalResults')} total, first={j['articles'][0].get('title','')[:60]!r}"
        else:
            detail = str(j)[:200]
        record("NewsAPI", bool(ok), r.status_code, detail)
    except Exception as e:
        record("NewsAPI", False, "ERR", str(e))


def main():
    for fn in [
        test_anthropic, test_openai, test_google, test_xai, test_deepseek,
        test_alpaca, test_finnhub, test_newsapi,
    ]:
        try:
            fn()
        except Exception:
            log.error("test crashed:\n%s", traceback.format_exc())

    print("\n" + "=" * 78)
    print(f"{'SERVICE':<12} {'RESULT':<8} {'STATUS':<8} DETAIL")
    print("-" * 78)
    for name, ok, status, detail in results:
        safe = detail[:48].encode("ascii", "replace").decode("ascii")
        print(f"{name:<12} {'PASS' if ok else 'FAIL':<8} {str(status):<8} {safe}")
    print("=" * 78)
    passed = sum(1 for _, ok, *_ in results if ok)
    print(f"{passed}/{len(results)} green")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
