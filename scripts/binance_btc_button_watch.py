#!/usr/bin/env python3
"""
Monitor Binance BTC Button game: https://www.binance.com/en/game/button/btc-button-Jan2026
Watches the countdown timer and notifies when it's low so you can click (or optionally auto-click).
Uses cookies from a local file (never commit): see docs/BINANCE_BTC_BUTTON_WATCH.md

Usage:
  pip install playwright
  playwright install chromium
  # Create data/binance_btc_button_cookies.json with your cookies (see docs)
  python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json [--notify-under 15] [--headless]
"""
import sys
import time
import argparse
import json
from pathlib import Path

# Project root on path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

GAME_URL = "https://www.binance.com/en/game/button/btc-button-Jan2026"
POLL_INTERVAL_SEC = 1.0
# Selectors to try for timer (update if page structure differs)
TIMER_SELECTORS = [
    "[class*='timer']",
    "[class*='countdown']",
    "[data-timer]",
    "div[class*='Timer']",
    ".css-timer",
]
BUTTON_SELECTORS = [
    "button[class*='button']",
    "[class*='click']",
    "button:not([disabled])",
]
# Leaderboard: best score = smallest time (closest to 00:00). Try table rows, list items, or time-like text.
LEADERBOARD_SELECTORS = [
    "[class*='leaderboard'] tr",
    "[class*='ranking'] tr",
    "[class*='leaderboard'] [class*='row']",
    "table tr",
    "[class*='score']",
]


def load_cookies(path: Path):
    if not path.is_file():
        print(f"ERROR: Cookie file not found: {path}")
        print("Create it with your Binance cookies (see docs/BINANCE_BTC_BUTTON_WATCH.md)")
        return None
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "cookies" in data:
        return data["cookies"]
    print("ERROR: Cookie file must be a JSON array of cookie objects or { \"cookies\": [...] }")
    return None


def parse_timer_text(text: str):
    """Parse MM:SS or M:SS to total seconds. Returns None if not parseable."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip().replace("\u200b", "").replace(" ", "")
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        m, s = int(parts[0]), int(parts[1])
        if 0 <= m <= 60 and 0 <= s < 60:
            return m * 60 + s
    except ValueError:
        pass
    return None


def get_leaderboard_best_sec(page) -> int | None:
    """
    Try to read the leaderboard and return the best time in seconds (closest to 00:00 = smallest).
    Used to only auto-click when current timer is below this (so we can beat the record).
    """
    import re
    candidates: list[int] = []
    # Try structured leaderboard rows first
    for sel in LEADERBOARD_SELECTORS:
        try:
            els = page.query_selector_all(sel)
            for el in els[:10]:  # top rows only
                try:
                    t = el.inner_text()
                    sec = parse_timer_text(t)
                    if sec is not None and 0 <= sec <= 60:
                        candidates.append(sec)
                except Exception:
                    continue
            if candidates:
                break
        except Exception:
            continue
    # Fallback: any MM:SS in body that looks like a low time (likely a score)
    if not candidates:
        try:
            body = page.query_selector("body")
            if body:
                text = body.inner_text()
                for m in re.finditer(r"(\d{1,2}):(\d{2})", text):
                    sec = int(m.group(1)) * 60 + int(m.group(2))
                    if 0 <= sec <= 60:
                        candidates.append(sec)
        except Exception:
            pass
    return min(candidates) if candidates else None


def main():
    p = argparse.ArgumentParser(description="Watch Binance BTC Button game timer.")
    p.add_argument("-c", "--cookies", required=True, type=Path, help="Path to JSON cookie file")
    p.add_argument("--notify-under", type=int, default=15, help="Notify when timer (seconds) is under this (default 15)")
    p.add_argument("--headless", action="store_true", help="Run browser headless")
    p.add_argument("--auto-click", action="store_true", help="Click only when timer is below leaderboard best (and within --max-clicks)")
    p.add_argument("--best-time", type=int, default=None, metavar="SEC", help="Override leaderboard best time in seconds (e.g. 8) if page parsing fails")
    p.add_argument("--max-clicks", type=int, default=5, metavar="N", help="Max auto-clicks per run to preserve attempts (default 5)")
    p.add_argument("--leaderboard-margin", type=int, default=0, metavar="SEC", help="Only click when timer <= best - SEC (default 0)")
    args = p.parse_args()

    cookies = load_cookies(args.cookies.resolve())
    if not cookies:
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install Playwright: pip install playwright && playwright install chromium")
        return 1

    print(f"Opening game page (headless={args.headless})...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(GAME_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        last_notify_sec = -999
        last_timer_sec = None
        leaderboard_best_sec: int | None = None
        LEADERBOARD_REFRESH_INTERVAL = 30.0
        last_leaderboard_refresh = -LEADERBOARD_REFRESH_INTERVAL  # refresh on first loop
        clicks_used = 0
        print("Watching timer (Ctrl+C to stop)...")
        if args.auto_click:
            print(f"  Auto-click: only when timer <= leaderboard best (minus margin), max {args.max_clicks} clicks this run.")
            if args.best_time is not None:
                leaderboard_best_sec = args.best_time
                print(f"  Using --best-time override: {args.best_time}s")

        try:
            while True:
                timer_sec = None
                timer_el = None
                for sel in TIMER_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            text = el.inner_text()
                            timer_sec = parse_timer_text(text)
                            if timer_sec is not None:
                                timer_el = el
                                break
                    except Exception:
                        continue
                if timer_sec is None:
                    # Fallback: try to find any text like 59:00 or 1:30
                    body = page.query_selector("body")
                    if body:
                        text = body.inner_text()
                        import re
                        m = re.search(r"(\d{1,2}):(\d{2})", text)
                        if m:
                            timer_sec = int(m.group(1)) * 60 + int(m.group(2))
                            if 0 <= timer_sec <= 60:
                                pass
                            else:
                                timer_sec = None

                if timer_sec is not None:
                    mm, ss = divmod(timer_sec, 60)
                    ts = f"{mm}:{ss:02d}"
                    if last_timer_sec != timer_sec:
                        print(f"\r  Timer: {ts} ({timer_sec}s)  ", end="", flush=True)
                        last_timer_sec = timer_sec

                    if timer_sec <= args.notify_under and timer_sec != last_notify_sec:
                        last_notify_sec = timer_sec
                        print(f"\n  >>> LOW TIMER: {ts} - consider clicking now! <<<")
                        try:
                            import subprocess
                            subprocess.run(["printf", "\\a"], shell=False, check=False)
                        except Exception:
                            pass

                    # Refresh leaderboard best periodically (or use --best-time override)
                    now = time.monotonic()
                    if args.auto_click and (now - last_leaderboard_refresh) >= LEADERBOARD_REFRESH_INTERVAL:
                        last_leaderboard_refresh = now
                        fresh = get_leaderboard_best_sec(page)
                        if fresh is not None:
                            leaderboard_best_sec = fresh
                            print(f"\n  [Leaderboard best: {leaderboard_best_sec}s]")
                    effective_best = leaderboard_best_sec if leaderboard_best_sec is not None else args.best_time

                    # Auto-click only when: below leaderboard best (minus margin), within attempt cap
                    if args.auto_click and timer_sec > 0 and clicks_used < args.max_clicks:
                        threshold = (effective_best - args.leaderboard_margin) if effective_best is not None else None
                        if threshold is not None and timer_sec <= threshold:
                            for sel in BUTTON_SELECTORS:
                                try:
                                    btn = page.query_selector(sel)
                                    if btn and btn.is_visible():
                                        btn.click()
                                        clicks_used += 1
                                        print(f"\n  [Auto-clicked at {ts} (below best {effective_best}s) — {clicks_used}/{args.max_clicks} clicks used]")
                                        last_notify_sec = -999
                                        break
                                except Exception:
                                    continue
                        elif effective_best is None and timer_sec <= 3:
                            print("\n  [No leaderboard best or --best-time; skipping auto-click. Set --best-time SEC to override.]")

                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("\nStopped.")

        browser.close()

    return 0


if __name__ == "__main__":
    exit(main())
