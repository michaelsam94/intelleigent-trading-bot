"""
Resolve outbound proxy URLs for requests / python-binance.

Single URL (set one of):
  BINANCE_HTTPS_PROXY, HTTPS_PROXY, TELEGRAM_HTTPS_PROXY (telegram order in effective_proxy_url_telegram)

SOCKS5 as separate vars (avoids URL-encoding long passwords in one string; works with Nord and others):
  SOCKS5_PROXY_HOST=nl.socks.nordhold.net   # from Nord Account → SOCKS5 / manual setup
  SOCKS5_PROXY_PORT=1080                  # optional, default 1080
  SOCKS5_PROXY_USER=...                   # Nord “service credentials” username
  SOCKS5_PROXY_PASSWORD=...               # Nord service password

Built URL: socks5h://USER:PASS@HOST:PORT  (DNS via proxy; requires PySocks)

PythonAnywhere: OpenVPN/full VPN is not supported on shared hosting; use SOCKS5 from Python only.
Free tier may still block outbound to Binance/Telegram/Nord — paid egress may be required.
"""
from __future__ import annotations

import os
import urllib.parse


def _build_socks5h_from_parts() -> str | None:
    host = (os.environ.get("SOCKS5_PROXY_HOST") or "").strip()
    user = (os.environ.get("SOCKS5_PROXY_USER") or "").strip()
    pw = os.environ.get("SOCKS5_PROXY_PASSWORD")
    if pw is None:
        pw = ""
    if not host or not user:
        return None
    raw_port = (os.environ.get("SOCKS5_PROXY_PORT") or "1080").strip() or "1080"
    try:
        port = int(raw_port)
    except ValueError:
        port = 1080
    if not (1 <= port <= 65535):
        port = 1080
    u = urllib.parse.quote(user, safe="")
    p = urllib.parse.quote(str(pw), safe="")
    return f"socks5h://{u}:{p}@{host}:{port}"


def effective_proxy_url_binance() -> str:
    """BINANCE_HTTPS_PROXY → HTTPS_PROXY → SOCKS5_* composite."""
    for key in ("BINANCE_HTTPS_PROXY", "HTTPS_PROXY"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return _build_socks5h_from_parts() or ""


def effective_proxy_url_telegram() -> str:
    """TELEGRAM_HTTPS_PROXY → HTTPS_PROXY → BINANCE_HTTPS_PROXY → SOCKS5_* composite."""
    for key in ("TELEGRAM_HTTPS_PROXY", "HTTPS_PROXY", "BINANCE_HTTPS_PROXY"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return _build_socks5h_from_parts() or ""


def requests_proxies_dict(proxy_url: str) -> dict[str, str] | None:
    u = (proxy_url or "").strip()
    if not u:
        return None
    return {"http": u, "https": u}
