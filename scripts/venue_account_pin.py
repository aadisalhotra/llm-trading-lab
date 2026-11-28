"""Print the pin for the account the broker_paper keys reach: its sha256, nothing else.

For the handover (Batch 4 item (f)): after the PI puts the new paper account's
keys in .env, this prints the value for config/settings.json ->
venue_account.broker_paper.account_sha256, which is committed under review
before the transition script runs. The account number is never printed.

Read-only: one GET /v2/account.
Usage: python scripts/venue_account_pin.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config_loader import force_utf8_console, load_env  # noqa: E402
from src.execution.account_pin import account_fingerprint  # noqa: E402


def main() -> int:
    force_utf8_console()
    load_env()
    from src.execution.broker import BrokerClient
    acct = BrokerClient("broker_paper").get_account()
    number = (acct or {}).get("account_number")
    if not number:
        sys.stderr.write("the account object names no account number\n")
        return 2
    sys.stdout.write(account_fingerprint(number) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
