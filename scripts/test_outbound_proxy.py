#!/usr/bin/env python3
"""
Quick HTTPS test through the same proxy env vars as eth_ta_telegram / telegram_broadcast.

Example (local SOCKS5 forwarding to your tunnel):
  export BINANCE_HTTPS_PROXY=socks5h://127.0.0.1:1080
  python scripts/test_outbound_proxy.py

Or add BINANCE_HTTPS_PROXY (or HTTPS_PROXY) to project .env — this script merges .env like eth_ta_telegram.

Proxy resolution matches common/proxy_env.py (Binance vs Telegram order differs slightly — see source).

SOCKS5 split vars (good for Nord on PythonAnywhere — put secrets only in .env, never in chat):
  SOCKS5_PROXY_HOST=...  SOCKS5_PROXY_PORT=1080  SOCKS5_PROXY_USER=...  SOCKS5_PROXY_PASSWORD=...

SOCKS5 / SOCKS5h URLs require PySocks:
  pip install PySocks
  # or: pip install "requests[socks]"

If you see SOCKSHTTPSConnectionPool + "Connection refused" / errno 111: PySocks is fine; nothing is
listening on the proxy host:port. Start the tunnel first, e.g.  ssh -D 1080 -N user@remote
then check:  nc -zv 127.0.0.1 1080
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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


def _redact_proxy_url(u: str) -> str:
    if not u.strip():
        return "(none)"
    low = u.lower()
    if "@" in u and (low.startswith("socks5h://") or low.startswith("socks5://") or "://" in u):
        try:
            scheme, rest = u.split("://", 1)
            hostpart = rest.split("@", 1)[-1]
            return f"{scheme}://***@{hostpart}"
        except (ValueError, IndexError):
            pass
    return u


def _print_connectivity_hint(exc: BaseException, proxy_url: str) -> None:
    """Context-specific stderr hints; avoid suggesting PySocks when SOCKS already works."""
    msg = str(exc).lower()
    pu = (proxy_url or "").lower()
    if not pu:
        return
    if "connection refused" in msg or "errno 111" in msg:
        if "socks" in pu:
            if "127.0.0.1" in pu or "localhost" in pu:
                print(
                    "Hint: connection refused to local SOCKS — nothing on that port. Start a listener, e.g.\n"
                    "  ssh -D 1080 -N user@your-server\n"
                    "Then:  nc -zv 127.0.0.1 1080",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    "Hint: connection refused reaching the SOCKS server (TCP to host:1080 failed before auth).\n"
                    "  • On PythonAnywhere: many plans block outbound TCP to arbitrary hosts/ports (including "
                    "Nord SOCKS on 1080). Your .env can be correct and still fail here. Check their outgoing "
                    "access docs; you may need a paid tier with full internet, or run this bot on a VPS/home PC.\n"
                    "  • Sanity check from this same shell:  "
                    "timeout 5 bash -c 'echo >/dev/tcp/HOST/1080' 2>/dev/null && echo open || echo closed\n"
                    "    (replace HOST with your SOCKS5_PROXY_HOST), or from another machine: "
                    "nc -zv amsterdam.nl.socks.nordhold.net 1080\n"
                    "  • Try another region hostname from Nord’s SOCKS list if one POP is down.\n"
                    "  • OpenVPN server names (eg2.nordvpn.com) are not SOCKS endpoints — use *.socks.nordhold.net.",
                    file=sys.stderr,
                    flush=True,
                )
        else:
            print(
                "Hint: connection refused — is your HTTP(S) proxy running and is the URL/port correct?",
                file=sys.stderr,
                flush=True,
            )
        return
    if "network is unreachable" in msg or "errno 101" in msg:
        print(
            "Hint: network unreachable — routing/firewall blocks reach to proxy or destination.",
            file=sys.stderr,
            flush=True,
        )
        return
    if "socks" in pu and (
        "missing dependencies" in msg
        or "socksdependency" in msg.replace(" ", "")
        or ("pysocks" in msg and "install" in msg)
    ):
        print(
            'Hint: install SOCKS support: pip install PySocks  (or pip install "requests[socks]")',
            file=sys.stderr,
            flush=True,
        )


def main() -> int:
    _load_project_dotenv()
    from common.proxy_env import (
        effective_proxy_url_binance,
        effective_proxy_url_telegram,
        requests_proxies_dict,
    )

    import requests

    pb = effective_proxy_url_binance()
    pt = effective_proxy_url_telegram()
    proxies_bin = requests_proxies_dict(pb)
    proxies_tg = requests_proxies_dict(pt)
    print(f"Effective Binance proxy: {_redact_proxy_url(pb)}", flush=True)
    print(f"Effective Telegram proxy: {_redact_proxy_url(pt)}", flush=True)
    if not proxies_bin and not proxies_tg:
        print(
            "No proxy env configured — direct connect (shell HTTP(S)_PROXY may still apply).",
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
            proxies=proxies_bin,
            timeout=t,
        )
        print(f"Binance ping: HTTP {r.status_code}", flush=True)
    except Exception as e:
        print(f"Binance ping FAILED: {e}", file=sys.stderr, flush=True)
        _print_connectivity_hint(e, pb)
        return 1

    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not tok:
        print("TELEGRAM_BOT_TOKEN unset — skipping Telegram getMe", flush=True)
        return 0

    try:
        url = f"https://api.telegram.org/bot{tok}/getMe"
        r2 = requests.get(url, proxies=proxies_tg, timeout=15)
        body = r2.json() if r2.content else {}
        ok = body.get("ok")
        un = (body.get("result") or {}).get("username", "?")
        print(f"Telegram getMe: HTTP {r2.status_code} ok={ok} username={un}", flush=True)
        if not ok:
            return 1
    except Exception as e:
        print(f"Telegram getMe FAILED: {e}", file=sys.stderr, flush=True)
        _print_connectivity_hint(e, pt)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
