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

Under PM2, stdout is not a TTY — Python may buffer prints. This script uses flush=True;
also set PYTHONUNBUFFERED=1 or run: python -u scripts/telegram_poll_debug.py

If a webhook is set, getUpdates returns nothing until you deleteWebhook.

Subscriber mode (default ON): on /start saves chat_id to data/telegram_subscribers.json so the trading
server can broadcast alerts to every user who started the bot. Disable with TELEGRAM_REGISTER_SUBSCRIBERS=0.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.telegram_broadcast import (
    add_subscriber,
    remove_subscriber,
    send_telegram_plain,
    subscriber_count,
    subscribers_file_path,
)

BASE = "https://api.telegram.org"


def _log(msg: str, *, err: bool = False) -> None:
    f = sys.stderr if err else sys.stdout
    print(msg, file=f, flush=True)


def _get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _register_enabled() -> bool:
    v = os.environ.get("TELEGRAM_REGISTER_SUBSCRIBERS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _command_base(text: str) -> str | None:
    if not text:
        return None
    first = text.split()[0]
    return first.split("@", 1)[0].lower()


def _handle_incoming_message(token: str, msg_obj: dict) -> None:
    chat = msg_obj.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    text = (msg_obj.get("text") or "").strip()
    cmd = _command_base(text)
    if cmd == "/start":
        new = add_subscriber(chat_id)
        n = subscriber_count()
        if new:
            reply = "You are subscribed to trading alerts on this bot."
        else:
            reply = "You were already subscribed. Trading alerts will be sent here."
        reply += f"\nSubscribers: {n}. Send /stop to unsubscribe."
        send_telegram_plain(token, chat_id, reply)
        _log(f"Subscriber {'+' if new else '='}{chat_id} (total {n})")
    elif cmd == "/stop":
        removed = remove_subscriber(chat_id)
        n = subscriber_count()
        reply = "Unsubscribed. You will not receive alerts." if removed else "You were not in the subscriber list."
        reply += f"\nSubscribers: {n}."
        send_telegram_plain(token, chat_id, reply)
        _log(f"Subscriber -{chat_id} removed={removed} (total {n})")


def main() -> int:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        _log("ERROR: TELEGRAM_BOT_TOKEN is empty. Add it to .env and: pm2 restart telegram-poll-debug --update-env", err=True)
        return 1

    enc = urllib.parse.quote(token, safe="")
    # Prove token works (wrong token = immediate failure)
    try:
        me = _get(f"{BASE}/bot{enc}/getMe", timeout=15)
    except Exception as e:
        _log(f"ERROR: getMe failed (bad token or network): {e}", err=True)
        return 1
    if not me.get("ok"):
        _log(f"ERROR: getMe: {me}", err=True)
        return 1
    uname = (me.get("result") or {}).get("username", "?")
    _log(f"OK: bot @{uname} (token …{token[-6:]})")

    # Webhook blocks long polling — user often sees empty getUpdates
    try:
        wh = _get(f"{BASE}/bot{enc}/getWebhookInfo", timeout=15)
    except Exception as e:
        _log(f"WARN: getWebhookInfo failed: {e}", err=True)
        wh = {}
    wh_url = ((wh.get("result") or {}) or {}).get("url") or ""
    if wh_url:
        _log(
            f"WARN: Webhook is set to: {wh_url!r}\n"
            "      getUpdates will stay empty until you remove it.\n"
            "      Add TELEGRAM_DELETE_WEBHOOK=1 to .env once, then:\n"
            "        pm2 restart telegram-poll-debug --update-env\n"
            "      Or open: https://api.telegram.org/bot<TOKEN>/deleteWebhook",
            err=True,
        )
    else:
        _log("OK: no webhook (long polling can receive updates).")

    if os.environ.get("TELEGRAM_DELETE_WEBHOOK", "").strip() in ("1", "true", "yes"):
        out = _get(f"{BASE}/bot{enc}/deleteWebhook", timeout=15)
        _log("deleteWebhook: " + json.dumps(out, indent=2))

    if _register_enabled():
        _log(
            f"/start → save chat to {subscribers_file_path()} (now {subscriber_count()}). "
            "TELEGRAM_REGISTER_SUBSCRIBERS=0 disables this."
        )
    else:
        _log("Subscriber registration OFF (TELEGRAM_REGISTER_SUBSCRIBERS=0). Only logging updates.")

    offset = 0
    _log("Polling getUpdates. Send /start to this bot in Telegram (same @username as above).")
    idle_loops = 0
    while True:
        q = urllib.parse.urlencode({"timeout": 25, "offset": offset})
        url = f"{BASE}/bot{enc}/getUpdates?{q}"
        try:
            data = _get(url, timeout=35)
        except Exception as e:
            _log(f"Request error: {e}", err=True)
            time.sleep(2)
            continue
        if not data.get("ok"):
            _log(f"Telegram error: {data}", err=True)
            time.sleep(2)
            continue
        batch = data.get("result", [])
        if batch:
            idle_loops = 0
            for upd in batch:
                offset = upd["update_id"] + 1
                _log(json.dumps(upd, indent=2, ensure_ascii=False))
                if _register_enabled():
                    msg_obj = upd.get("message") or upd.get("edited_message")
                    if isinstance(msg_obj, dict):
                        try:
                            _handle_incoming_message(token, msg_obj)
                        except Exception as e:
                            _log(f"subscriber handler error: {e}", err=True)
        else:
            idle_loops += 1
            # ~every 2.5 min of empty long-polls, prove we're alive in PM2 logs
            if idle_loops % 6 == 0:
                _log(f"…polling (no updates yet). offset={offset} If /start does nothing, check webhook warning above.")
        time.sleep(0.1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
