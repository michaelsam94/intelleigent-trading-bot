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
import os
import re
import sys
import smtplib
import time
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Project root on path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

GAME_URL = "https://www.binance.com/en/game/button/btc-button-Jan2026"
POLL_INTERVAL_SEC = 1.0
# Selectors to try for timer (update if page structure differs; game may be in iframe)
# Binance BTC Button uses TimeCounter with digits in separate elements (TimeCounter_digitSet__num__ArgPy)
TIMER_SELECTORS = [
    "[class*='TimeCounter_timeCounter']",  # Binance: container with MM:SS as separate digits
    "[class*='timer']",
    "[class*='countdown']",
    "[class*='Timer']",
    "[data-timer]",
    "div[class*='Timer']",
    ".css-timer",
    "[class*='time']",
]
# Binance: digit elements inside TimeCounter (order: min1, min2, sec1, sec2) → "MM:SS"
TIMER_DIGIT_CLASS = "[class*='TimeCounter_digitSet__num']"
# Binance BTC Button: real game button is img or wrapper with BitcoinButton_bitcoinBtn in class
BUTTON_SELECTORS = [
    "img[class*='BitcoinButton_bitcoinBtn']",
    "[class*='BitcoinButton_bitcoinBtn']",
    "[class*='BitcoinButton']",
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
# Attempts left: look for "X attempts" or "remaining X" on page
ATTEMPTS_LEFT_SELECTORS = [
    "[class*='attempt']",
    "[class*='remaining']",
    "[class*='chance']",
]

# Env vars for email (set on server; never commit credentials)
ENV_SMTP_EMAIL = "BINANCE_BUTTON_SMTP_EMAIL"
ENV_SMTP_PASSWORD = "BINANCE_BUTTON_SMTP_PASSWORD"
ENV_EMAIL_TO = "BINANCE_BUTTON_EMAIL_TO"  # recipient; if unset, email is sent to SMTP address
GMAIL_SMTP = ("smtp.gmail.com", 587)


def load_cookies(path: Path):
    if not path.is_file():
        print(f"ERROR: Cookie file not found: {path}")
        print("Create it with your Binance cookies (see docs/BINANCE_BTC_BUTTON_WATCH.md)")
        return None
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict) and "cookies" in data:
        raw = data["cookies"]
    else:
        print("ERROR: Cookie file must be a JSON array of cookie objects or { \"cookies\": [...] }")
        return None
    return normalize_cookies_for_playwright(raw)


def normalize_cookies_for_playwright(cookies: list[dict]) -> list[dict]:
    """Playwright expects sameSite in (Strict|Lax|None). Browser exports often use other values."""
    out = []
    for c in cookies:
        c = dict(c)
        same = c.get("sameSite") or c.get("same_site")
        if same is not None:
            s = str(same).strip().lower()
            if s == "strict":
                c["sameSite"] = "Strict"
            elif s == "lax":
                c["sameSite"] = "Lax"
            elif s in ("none", "no_restriction", "unspecified"):
                c["sameSite"] = "None"
            elif str(same) in ("Strict", "Lax", "None"):
                c["sameSite"] = str(same)
            else:
                c["sameSite"] = "Lax"
        out.append(c)
    return out


def get_timer_from_frame(frame):
    """Try to read timer from a page frame (main or iframe). Returns (timer_sec, frame) or (None, None)."""
    # Binance BTC Button: timer is TimeCounter with 4 digit elements (min1, min2, sec1, sec2)
    try:
        container = frame.query_selector("[class*='TimeCounter_timeCounter']")
        if container:
            digits_el = container.query_selector_all(TIMER_DIGIT_CLASS)
            if len(digits_el) >= 4:
                parts = []
                for el in digits_el[:4]:
                    t = el.inner_text().strip()
                    if t and t[0].isdigit():
                        parts.append(t[0])
                if len(parts) == 4:
                    mm_ss = f"{parts[0]}{parts[1]}:{parts[2]}{parts[3]}"
                    sec = parse_timer_text(mm_ss)
                    if sec is not None:
                        return sec, frame
    except Exception:
        pass

    for sel in TIMER_SELECTORS:
        try:
            el = frame.query_selector(sel)
            if el:
                text = el.inner_text()
                sec = parse_timer_text(text)
                if sec is not None:
                    return sec, frame
        except Exception:
            continue
    try:
        body = frame.query_selector("body")
        if body:
            text = body.inner_text()
            m = re.search(r"(\d{1,2}):(\d{2})", text)
            if m:
                sec = int(m.group(1)) * 60 + int(m.group(2))
                if 0 <= sec <= 60:
                    return sec, frame
    except Exception:
        pass
    return None, None


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


def get_attempts_left(page) -> int | None:
    """Try to read remaining attempts from the page. Returns None if not found."""
    for sel in ATTEMPTS_LEFT_SELECTORS:
        try:
            els = page.query_selector_all(sel)
            for el in els[:5]:
                try:
                    t = el.inner_text()
                    # e.g. "46 attempts left", "attempts: 47", "5/47"
                    m = re.search(r"(\d+)\s*(?:attempts?|left|remaining|/|:)", t, re.I)
                    if m:
                        return int(m.group(1))
                    m = re.search(r"(?:attempts?|left)\s*[:\s]*(\d+)", t, re.I)
                    if m:
                        return int(m.group(1))
                except Exception:
                    continue
        except Exception:
            continue
    try:
        body = page.query_selector("body")
        if body:
            text = body.inner_text()
            m = re.search(r"(\d+)\s*/\s*(\d+)\s*(?:attempt|click)", text, re.I)
            if m:
                return int(m.group(2)) - int(m.group(1))  # remaining = total - used
            m = re.search(r"(?:attempts?|left)\s*[:\s]*(\d+)", text, re.I)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def send_attempt_email(
    from_email: str,
    smtp_password: str,
    to_email: str,
    attempt_used: int,
    time_reached: str,
    attempts_left: int | None,
) -> bool:
    """Send a single attempt report via Gmail SMTP. Returns True on success."""
    subject = f"Binance BTC Button — attempt #{attempt_used} at {time_reached}"
    body_lines = [
        "BTC Button attempt report",
        "",
        f"Attempt used: {attempt_used}",
        f"Time reached when clicked: {time_reached}",
        f"Attempts left: {attempts_left if attempts_left is not None else 'N/A (check game)'}",
        "",
        "— binance_btc_button_watch.py",
    ]
    body = "\n".join(body_lines)
    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(*GMAIL_SMTP) as server:
            server.starttls()
            server.login(from_email, smtp_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"\n  [Email failed: {e}]")
        return False


def main():
    p = argparse.ArgumentParser(description="Watch Binance BTC Button game timer.")
    p.add_argument("-c", "--cookies", required=True, type=Path, help="Path to JSON cookie file")
    p.add_argument("--notify-under", type=int, default=15, help="Notify when timer (seconds) is under this (default 15)")
    p.add_argument("--headless", action="store_true", help="Run browser headless")
    p.add_argument("--auto-click", action="store_true", help="Click only when timer is below leaderboard best (and within --max-clicks)")
    p.add_argument("--best-time", type=int, default=None, metavar="SEC", help="Override leaderboard best time in seconds (e.g. 8) if page parsing fails")
    p.add_argument("--max-clicks", type=int, default=None, metavar="N", help="Max auto-clicks per run (default 1 if --one-shot else 5)")
    p.add_argument("--leaderboard-margin", type=int, default=0, metavar="SEC", help="Only click when timer <= best - SEC (default 0)")
    p.add_argument("--one-shot", action="store_true", help="One attempt per run: click once when conditions met, send email if env set, then exit")
    p.add_argument("--test-click", action="store_true", help="Find and click the button once then exit (uses 1 attempt; use to verify button works)")
    p.add_argument("--test-find-button", action="store_true", help="Only find and report the button (no click, no attempt used); use if --test-click did not update Last Attempt")
    args = p.parse_args()
    if args.max_clicks is None:
        args.max_clicks = 1 if args.one_shot else 5

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
        time.sleep(3)  # let game iframe and dynamic content load

        # One-time parse of leaderboard so user can see if it works (with or without --best-time)
        parsed_best: int | None = None
        for frame in page.frames:
            b = get_leaderboard_best_sec(frame)
            if b is not None:
                parsed_best = b if parsed_best is None else min(parsed_best, b)
        if parsed_best is not None:
            print(f"  Leaderboard best (parsed from page): {parsed_best}s")
        else:
            print("  Leaderboard best: could not parse from page (use --best-time SEC to set threshold)")

        if args.test_click:
            print("  Test-click: looking for button to click once...")
            clicked = False
            for frame in page.frames:
                for sel in BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn and btn.is_visible():
                            btn.click()
                            print("  [OK] Button found and clicked (1 attempt used).")
                            print("  (If Binance 'Last Attempt' did not update, we may have clicked the wrong element; try --test-find-button to see what we match.)")
                            clicked = True
                            break
                    except Exception as e:
                        continue
                if clicked:
                    break
            if not clicked:
                print("  [FAIL] Could not find or click the button. Check BUTTON_SELECTORS or page structure.")
            time.sleep(1)
            browser.close()
            return 0 if clicked else 1

        if args.test_find_button:
            print("  Test-find-button: listing what we would click (no click, no attempt used)...")
            found_any = False
            for i, frame in enumerate(page.frames):
                for sel in BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn and btn.is_visible():
                            cls = btn.get_attribute("class") or ""
                            tag = btn.evaluate("el => el.tagName").lower() if btn else ""
                            print(f"  Would click: selector={sel!r} frame={i} tag={tag} class={cls[:80]!r}")
                            found_any = True
                    except Exception:
                        continue
            if not found_any:
                print("  [FAIL] No button found. Check BUTTON_SELECTORS or page structure.")
            else:
                print("  ^ If the real game button has different class, add it to BUTTON_SELECTORS in the script.")
            browser.close()
            return 0 if found_any else 1

        last_notify_sec = -999
        last_timer_sec = None
        leaderboard_best_sec: int | None = parsed_best  # use parsed value when no --best-time override
        LEADERBOARD_REFRESH_INTERVAL = 30.0
        last_leaderboard_refresh = -LEADERBOARD_REFRESH_INTERVAL  # refresh on first loop
        last_status_log = 0.0
        STATUS_LOG_INTERVAL = 30.0  # log a timestamped line every 30s so logs/cron show progress
        clicks_used = 0
        print("Watching timer (Ctrl+C to stop)...")
        if args.auto_click:
            print(f"  Auto-click: only when timer <= leaderboard best (minus margin), max {args.max_clicks} clicks this run.")
            if args.one_shot:
                print("  One-shot: will exit after first click.")
            if os.environ.get(ENV_SMTP_EMAIL) and os.environ.get(ENV_SMTP_PASSWORD):
                print("  Email: will send attempt report after each click (env set).")
            if args.best_time is not None:
                leaderboard_best_sec = args.best_time
                print(f"  Using --best-time override: {args.best_time}s")

        try:
            game_frame = None  # iframe where timer was found; None = main page
            while True:
                timer_sec = None
                timer_el = None
                # Try main page then every frame (game is often in an iframe on Binance)
                for frame in page.frames:
                    sec, found_frame = get_timer_from_frame(frame)
                    if sec is not None:
                        timer_sec = sec
                        game_frame = found_frame
                        break
                if timer_sec is None:
                    game_frame = None

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
                        fresh = get_leaderboard_best_sec(game_frame or page)
                        if fresh is not None:
                            leaderboard_best_sec = fresh
                            print(f"\n  [Leaderboard best: {leaderboard_best_sec}s]")
                    effective_best = leaderboard_best_sec if leaderboard_best_sec is not None else args.best_time

                    # Auto-click only when: below leaderboard best (minus margin), within attempt cap
                    if args.auto_click and timer_sec > 0 and clicks_used < args.max_clicks:
                        threshold = (effective_best - args.leaderboard_margin) if effective_best is not None else None
                        if threshold is not None and timer_sec <= threshold:
                            target = game_frame if game_frame else page
                            for sel in BUTTON_SELECTORS:
                                try:
                                    btn = target.query_selector(sel)
                                    if btn and btn.is_visible():
                                        btn.click()
                                        clicks_used += 1
                                        print(f"\n  [Auto-clicked at {ts} (below best {effective_best}s) — {clicks_used}/{args.max_clicks} clicks used]")
                                        last_notify_sec = -999
                                        # Email report (env only; no credentials in repo)
                                        time.sleep(2.0)
                                        attempts_left = get_attempts_left(game_frame or page)
                                        smtp_email = os.environ.get(ENV_SMTP_EMAIL)
                                        smtp_password = os.environ.get(ENV_SMTP_PASSWORD)
                                        email_to = os.environ.get(ENV_EMAIL_TO) or smtp_email
                                        if smtp_email and smtp_password:
                                            if send_attempt_email(smtp_email, smtp_password, email_to, clicks_used, ts, attempts_left):
                                                print("  [Email sent.]")
                                        if args.one_shot:
                                            print("  [One-shot: exiting after one attempt.]")
                                            browser.close()
                                            return 0
                                        break
                                except Exception:
                                    continue
                        elif effective_best is None and timer_sec <= 3:
                            print("\n  [No leaderboard best or --best-time; skipping auto-click. Set --best-time SEC to override.]")

                # Periodic status line every 30s (always, even if timer not detected) so logs show script is alive
                now_mono = time.monotonic()
                if last_status_log == 0.0:
                    last_status_log = now_mono
                if (now_mono - last_status_log) >= STATUS_LOG_INTERVAL:
                    last_status_log = now_mono
                    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    threshold = (leaderboard_best_sec if leaderboard_best_sec is not None else args.best_time) or "?"
                    # Use current timer, or last seen if this poll missed it (avoids "not detected" when timer is working)
                    sec = timer_sec if timer_sec is not None else last_timer_sec
                    if sec is not None:
                        mm, ss = divmod(sec, 60)
                        print(f"\n  [{stamp} UTC] Timer: {mm}:{ss:02d} ({sec}s) — waiting for ≤{threshold}s to click")
                    else:
                        print(f"\n  [{stamp} UTC] Still running — timer not detected (page may differ); waiting for ≤{threshold}s to click")

                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("\nStopped.")

        browser.close()

    return 0


if __name__ == "__main__":
    exit(main())
