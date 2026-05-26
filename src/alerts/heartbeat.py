"""Dead-man's-switch heartbeat for intraday tick liveness.

An independent external monitor (Healthchecks.io, or any inbound-ping watchdog)
owns the question this layer answers: "did a decision tick actually land in the
last ~35 minutes during market hours?" The pipeline pings a unique URL ONLY
after an intraday tick runs to completion with the market open — i.e. only when
a real decision was made, not merely when the workflow ran. If the watchdog
stops hearing pings, IT raises the alarm over its own infrastructure
(email / SMS / push), with no dependence on this pipeline, on GitHub Actions,
or on the in-process Gmail transport.

That independence is the entire point. The 2026-05-26 outage proved two things:
a workflow run can report `success` while making zero decisions (a dropped
self-chain dispatch), and the in-process email alerter is useless precisely
when the tick never runs. A monitor that watches *committed decisions* from
*outside* the pipeline is the only thing that catches "the lab went quiet."

Contract (mirrors `email_alerts.send_email`):
  - `send_heartbeat()` NEVER raises. A failed ping is logged and returns False,
    so a flaky network or a watchdog outage can never crash — or meaningfully
    delay — a trading tick.
  - Self-disabling. If `HEARTBEAT_URL` is unset (or `alerts.heartbeat.enabled`
    is false) the call is a logged no-op. Deploying this code is therefore a
    true no-op until the external check is provisioned — zero risk to the
    running pipeline.
  - Independent of `alerts.enabled`. Muting email alerts must not blind the
    liveness monitor, so this path checks only its own `alerts.heartbeat`
    config.
  - The success ping hits `HEARTBEAT_URL`; a failure ping hits
    `HEARTBEAT_URL/fail` (the Healthchecks convention) to trip the watchdog
    immediately instead of waiting out its grace window. The success ping is
    the load-bearing signal — its *absence* is what triggers the alarm. The
    fail ping is best-effort acceleration and is harmless on services that
    don't implement `/fail`.

Provisioning lives outside this repo — see `docs/MONITORING.md` for the
Healthchecks.io setup, the market-hours schedule, and where the `HEARTBEAT_URL`
secret goes.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ..config_loader import load_settings

logger = logging.getLogger("llmlab.alerts.heartbeat")

DEFAULT_TIMEOUT_SEC = 10.0


def _heartbeat_config(settings: dict[str, Any] | None) -> dict[str, Any]:
    """The `alerts.heartbeat` sub-block, defaulting to {} if absent."""
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            logger.exception("Could not load settings for heartbeat config")
            settings = {}
    return (settings.get("alerts", {}) or {}).get("heartbeat", {}) or {}


def send_heartbeat(success: bool = True, *, settings: dict[str, Any] | None = None) -> bool:
    """Ping the external liveness watchdog. Returns True on a 2xx response.

    Wrapped end-to-end in try/except: any failure is logged and returns False.
    Callers never need to guard this, and it never blocks a tick longer than
    the (short, configurable) HTTP timeout.

    `success=True`  -> ping the base URL ("a decision tick landed").
    `success=False` -> ping `<base>/fail` to trip the watchdog immediately on a
                       known-fatal abort, rather than waiting out its grace.
    """
    try:
        cfg = _heartbeat_config(settings)
        if not cfg.get("enabled", True):
            logger.debug("Heartbeat disabled in settings — skipping ping")
            return False

        base = (os.getenv("HEARTBEAT_URL") or "").strip().rstrip("/")
        if not base:
            # Deliberate no-op until the external monitor is provisioned. Logged
            # at INFO so the unconfigured state is visible in the tick log.
            logger.info("HEARTBEAT_URL not set — skipping liveness ping (monitor not provisioned)")
            return False

        url = base if success else base + "/fail"
        timeout = float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SEC))

        # Lazy import so a packaging hiccup with requests can never break the
        # pipeline's import graph — this module is imported at pipeline start.
        import requests

        resp = requests.get(url, timeout=timeout)
        ok = 200 <= resp.status_code < 300
        if ok:
            logger.info("Heartbeat ping sent [%s] -> HTTP %s", "ok" if success else "fail",
                        resp.status_code)
        else:
            logger.warning("Heartbeat ping returned non-2xx [%s]: HTTP %s", url, resp.status_code)
        return ok
    except Exception as e:  # noqa: BLE001 — a failed heartbeat must never crash or stall a tick
        logger.warning("Heartbeat ping failed (non-fatal): %s", e)
        return False


def _send_test_heartbeat() -> bool:
    """Fire a one-off success ping to verify the wiring:

        python -m src.alerts.heartbeat

    Prints whether the ping reached the watchdog. Requires HEARTBEAT_URL in the
    environment (load it from .env via the call below).
    """
    from ..config_loader import force_utf8_console, load_env
    force_utf8_console()
    load_env()
    if not (os.getenv("HEARTBEAT_URL") or "").strip():
        print("HEARTBEAT_URL not set — nothing to test. Add it to .env first.")
        return False
    ok = send_heartbeat(success=True)
    print("Test heartbeat", "SENT (watchdog should now show a recent ping)" if ok
          else "NOT sent — see log above")
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    raise SystemExit(0 if _send_test_heartbeat() else 1)
