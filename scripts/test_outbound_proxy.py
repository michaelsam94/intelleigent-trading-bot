#!/usr/bin/env python3
"""
Quick HTTPS test through the same proxy env vars as eth_ta_telegram / telegram_broadcast.

Example (local SOCKS5 forwarding to your tunnel):
  export BINANCE_HTTPS_PROXY=socks5h://127.0.0.1:1080
  python scripts/test_outbound_proxy.py

Or add BINANCE_HTTPS_PROXY (or HTTPS_PROXY) to project .env — this script merges .env like eth_ta_telegram.

Proxy resolution for this script (matches telegram_broadcast):
  TELEGRAM_HTTPS_PROXY → HTTPS_PROXY → BINANCE_HTTPS_PROXY

SOCKS5 / SOCKS5h URLs require PySocks:
  pip install PySocks
  # or: pip install "requests[socks]"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_project_dotenv() -> None:
    """Merge project root .env into os.environ (same rules as scripts/eth_ta_telegram.py)."""
    p = _ROOT / ".env"
    if not p.is_file():
        return
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        eq = line.index("=")
        key = line[:eq].strip()
        val = line[eq + 1 :].strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1].replace("\\'", "'")
        if key:
            os.environ[key] = val


def _requests_proxies() -> dict[str, str] | None:
    p = (
        os.environ.get("TELEGRAM_HTTPS_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("BINANCE_HTTPS_PROXY")
        or ""
    ).strip()
    if not p:
        return None
    return {"http": p, "https": p}


def main() -> int:
    _load_project_dotenv()
    import requests

    proxies = _requests_proxies()
    proxy_show = (
        (proxies or {}).get("https", "")
        if proxies
        else (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "").strip()
    )
    if proxies:
        print(f"Using explicit proxies: {proxy_show}", flush=True)
    else:
        print(
            "No TELEGRAM_HTTPS_PROXY / HTTPS_PROXY / BINANCE_HTTPS_PROXY — "
            "direct connect (requests may still honor shell HTTP(S)_PROXY).",
            flush=True,
        )

    try:
        t = float(os.environ.get("BINANCE_REQUEST_TIMEOUT_SEC", "30") or 30)
    except ValueError:
        t = 30.0
    t = max(1.0, min(t, 120.0))

    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ping",
            proxies=proxies,
            timeout=t,
        )
        print(f"Binance ping: HTTP {r.status_code}", flush=True)
    except Exception as e:
        print(f"Binance ping FAILED: {e}", file=sys.stderr, flush=True)
        p = proxy_show.lower()
        if "socks" in p:
            print(
                'Hint: install SOCKS support: pip install PySocks  (or pip install "requests[socks]")',
                file=sys.stderr,
                flush=True,
            )
        return 1

    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not tok:
        print("TELEGRAM_BOT_TOKEN unset — skipping Telegram getMe", flush=True)
        return 0

    try:
        url = f"https://api.telegram.org/bot{tok}/getMe"
        r2 = requests.get(url, proxies=proxies, timeout=15)
        body = r2.json() if r2.content else {}
        ok = body.get("ok")
        un = (body.get("result") or {}).get("username", "?")
        print(f"Telegram getMe: HTTP {r2.status_code} ok={ok} username={un}", flush=True)
        if not ok:
            return 1
    except Exception as e:
        print(f"Telegram getMe FAILED: {e}", file=sys.stderr, flush=True)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
