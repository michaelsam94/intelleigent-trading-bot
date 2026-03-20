#!/usr/bin/env python3
"""
Debug: receive Telegram updates via long polling (getUpdates).

Sending messages (sendMessage) works without this. Receiving /start requires either
polling (this script) or a webhook.

Usage:
  export TELEGRAM_BOT_TOKEN="123456:ABC..."
  python scripts/telegram_poll_debug.py

Optional:
  export TELEGRAM_DELETE_WEBHOOK=1   # call deleteWebhook once before polling
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://api.telegram.org"


def _get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> int:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 1

    if os.environ.get("TELEGRAM_DELETE_WEBHOOK", "").strip() in ("1", "true", "yes"):
        u = f"{BASE}/bot{urllib.parse.quote(token, safe='')}/deleteWebhook"
        out = _get(u)
        print("deleteWebhook:", json.dumps(out, indent=2))

    offset = 0
    print("Polling getUpdates (Ctrl+C to stop). Send /start to the bot in Telegram.\n")
    while True:
        q = urllib.parse.urlencode({"timeout": 25, "offset": offset})
        url = f"{BASE}/bot{urllib.parse.quote(token, safe='')}/getUpdates?{q}"
        try:
            data = _get(url, timeout=35)
        except Exception as e:
            print("Request error:", e)
            time.sleep(2)
            continue
        if not data.get("ok"):
            print("Telegram error:", data)
            time.sleep(2)
            continue
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            print(json.dumps(upd, indent=2, ensure_ascii=False))
        time.sleep(0.1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
