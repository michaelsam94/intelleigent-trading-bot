"""
Telegram broadcast: multiple chat_ids from data/telegram_subscribers.json (filled when users send /start
to the subscriber bot) plus optional legacy TELEGRAM_CHAT_ID / config telegram_chat_id.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from typing import Any

log = logging.getLogger("telegram_broadcast")

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore


def _is_placeholder(val: Any) -> bool:
    if not val or not isinstance(val, str):
        return True
    s = val.strip()
    return "<" in s or ">" in s or s.lower().startswith("your-") or s == ""


def subscribers_file_path() -> str:
    p = (os.environ.get("TELEGRAM_SUBSCRIBERS_FILE") or "data/telegram_subscribers.json").strip()
    return os.path.abspath(p)


def _ensure_parent_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _lock(f, exclusive: bool) -> None:
    if not fcntl:
        return
    fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)


def _unlock(f) -> None:
    if not fcntl:
        return
    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def load_subscribers_only() -> list[str]:
    path = subscribers_file_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            _lock(f, exclusive=False)
            try:
                raw = f.read()
            finally:
                _unlock(f)
        if not raw.strip():
            return []
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
        if isinstance(data, dict) and "chat_ids" in data:
            return [str(x).strip() for x in data["chat_ids"] if str(x).strip()]
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read subscribers file %s: %s", path, e)
    return []


def add_subscriber(chat_id: str | int) -> bool:
    """Append chat_id to subscribers file. Returns True if newly added."""
    cid = str(chat_id).strip()
    if not cid:
        return False
    path = subscribers_file_path()
    _ensure_parent_dir(path)
    open_mode = "a+" if os.path.isfile(path) else "w+"
    with open(path, open_mode, encoding="utf-8") as f:
        _lock(f, exclusive=True)
        try:
            f.seek(0)
            raw = f.read()
            data: list = json.loads(raw) if raw.strip() else []
            if not isinstance(data, list):
                data = []
            normalized = [str(x).strip() for x in data]
            if cid in normalized:
                return False
            normalized.append(cid)
            f.seek(0)
            f.truncate()
            json.dump(normalized, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            _unlock(f)
    return True


def remove_subscriber(chat_id: str | int) -> bool:
    """Remove chat_id from file. Returns True if it was present."""
    cid = str(chat_id).strip()
    if not cid or not os.path.isfile(subscribers_file_path()):
        return False
    path = subscribers_file_path()
    with open(path, "r+", encoding="utf-8") as f:
        _lock(f, exclusive=True)
        try:
            raw = f.read()
            data: list = json.loads(raw) if raw.strip() else []
            if not isinstance(data, list):
                data = []
            normalized = [str(x).strip() for x in data]
            if cid not in normalized:
                return False
            normalized = [x for x in normalized if x != cid]
            f.seek(0)
            f.truncate()
            json.dump(normalized, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            _unlock(f)
    return True


def subscriber_count() -> int:
    return len(load_subscribers_only())


def recipient_chat_ids(config: dict | None = None) -> list[str]:
    """
    All destinations for trade/score Telegram alerts:
    1) chat_ids from subscribers file (/start registrations)
    2) config telegram_chat_id if set and not a placeholder
    3) env TELEGRAM_CHAT_ID (legacy single admin channel)
    """
    config = config or {}
    out: list[str] = []
    seen: set[str] = set()
    for cid in load_subscribers_only():
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    cfg_chat = str(config.get("telegram_chat_id") or "").strip().replace("\n", "").replace("\r", "")
    if cfg_chat and not _is_placeholder(cfg_chat) and cfg_chat not in seen:
        seen.add(cfg_chat)
        out.append(cfg_chat)
    env_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip().replace("\n", "").replace("\r", "")
    if env_chat and not _is_placeholder(env_chat) and env_chat not in seen:
        seen.add(env_chat)
        out.append(env_chat)
    return out


def broadcast_telegram_markdown(bot_token: str, text: str, config: dict | None = None) -> int:
    """
    Send one markdown message to every recipient. Returns count of successful API ok responses.
    """
    import requests

    chats = recipient_chat_ids(config)
    if not bot_token or not chats:
        return 0
    ok = 0
    for i, chat_id in enumerate(chats):
        if i:
            time.sleep(0.04)
        try:
            url = (
                "https://api.telegram.org/bot"
                + bot_token
                + "/sendMessage?chat_id="
                + str(chat_id).strip()
                + "&parse_mode=markdown&text="
                + urllib.parse.quote(text)
            )
            r = requests.get(url, timeout=12)
            body = r.json() if r.content else {}
            if body.get("ok"):
                ok += 1
            else:
                log.warning(
                    "Telegram send failed chat_id=%s: %s",
                    chat_id,
                    body.get("description", r.text[:300]),
                )
        except Exception as e:
            log.warning("Telegram send error chat_id=%s: %s", chat_id, e)
    return ok


def broadcast_telegram_plain(bot_token: str, text: str, config: dict | None = None) -> int:
    """
    Send plain text to every recipient (no parse_mode — safe for arbitrary characters).
    """
    import requests

    chats = recipient_chat_ids(config)
    if not bot_token or not chats:
        return 0
    ok = 0
    for i, chat_id in enumerate(chats):
        if i:
            time.sleep(0.04)
        try:
            url = (
                "https://api.telegram.org/bot"
                + bot_token
                + "/sendMessage?chat_id="
                + str(chat_id).strip()
                + "&text="
                + urllib.parse.quote(text)
            )
            r = requests.get(url, timeout=12)
            body = r.json() if r.content else {}
            if body.get("ok"):
                ok += 1
            else:
                log.warning(
                    "Telegram plain send failed chat_id=%s: %s",
                    chat_id,
                    body.get("description", r.text[:300]),
                )
        except Exception as e:
            log.warning("Telegram plain send error chat_id=%s: %s", chat_id, e)
    return ok


def send_telegram_plain(bot_token: str, chat_id: str | int, text: str) -> bool:
    """Single chat, no parse_mode (safe for arbitrary text). Uses stdlib only."""
    import urllib.request

    enc_tok = urllib.parse.quote(bot_token, safe="")
    q = urllib.parse.urlencode({"chat_id": str(chat_id).strip(), "text": text})
    url = f"https://api.telegram.org/bot{enc_tok}/sendMessage?{q}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
        return bool(data.get("ok"))
    except Exception as e:
        log.debug("send_telegram_plain failed: %s", e)
        return False
