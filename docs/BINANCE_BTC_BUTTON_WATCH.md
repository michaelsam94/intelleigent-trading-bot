# Binance BTC Button game watcher

Script: `scripts/binance_btc_button_watch.py`  
Game: [BTC Button Jan 2026](https://www.binance.com/en/game/button/btc-button-Jan2026)

## What it does

- Opens the game page in a browser with **your cookies** (you stay logged in).
- Watches the **60:00 → 00:00** countdown.
- **Prints** the current timer and **alerts** (beep + message) when the timer is under a set number of seconds (default 15) so you can click manually.
- Optional **auto-click** only when the timer is **below the current best score on the leaderboard** (so you only use attempts when you can beat the record), with a cap on clicks per run so you don’t run out of attempts (use `--auto-click`, `--max-clicks`, and optionally `--best-time`; use at your own risk and check Binance terms).

## Setup

1. **Install Playwright**
   ```bash
   pip install playwright
   playwright install chromium
   ```

2. **Create a cookie file (never commit this)**

   You must export your Binance cookies into a JSON file so the script can load the page as you.

   **Option A – Export from browser**

   - Log in to Binance in Chrome.
   - Install an extension like [EditThisCookie](https://chrome.google.com/webstore/detail/editthiscookie) or [Cookie-Editor](https://cookie-editor.cgagnier.ca/).
   - Go to `https://www.binance.com` and export cookies as **JSON**.
   - Save as `data/binance_btc_button_cookies.json` in the project.

   **Option B – Manual format**

   The file must be a JSON **array** of cookie objects, or an object with a `"cookies"` key that is that array. Each cookie should have at least:

   - `name`, `value`
   - `domain` (e.g. `".binance.com"`)
   - `path` (e.g. `"/"`)

   Example (fake values):

   ```json
   [
     {"name": "cookie_pairs", "value": "YOUR_VALUE", "domain": ".binance.com", "path": "/"},
     {"name": "BNC-LOGIN-DEVICE", "value": "YOUR_VALUE", "domain": ".binance.com", "path": "/"}
   ]
   ```

   Or:

   ```json
   {
     "cookies": [
       {"name": "...", "value": "...", "domain": ".binance.com", "path": "/"}
     ]
   }
   ```

   Save as `data/binance_btc_button_cookies.json` (or another path and pass it with `-c`).

3. **Ignore the file in Git**

   The repo already ignores `data/` or you can add:

   ```
   binance_btc_button_cookies.json
   binance_*_cookies.json
   ```

   so cookie files are never committed.

## Usage

From project root:

```bash
# Watch only; alert when timer < 15s (default)
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json

# Alert when timer < 10s
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --notify-under 10

# Run headless (no window). Omit --headless to show the browser window.
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --headless

# Auto-click only when timer is below leaderboard best, max 5 clicks this run (preserve attempts)
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --auto-click --max-clicks 5

# If the script can't read the leaderboard, set the current best time manually (e.g. 8 seconds)
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --auto-click --best-time 8 --max-clicks 10

# Only click when you're at least 2 seconds better than current best (--leaderboard-margin 2)
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --auto-click --best-time 8 --leaderboard-margin 2 --max-clicks 5
```

- **Ctrl+C** stops the script.
- **Auto-click behaviour**: The script tries to read the leaderboard best time (closest to 00:00). It only clicks when the current timer is **at or below** that best time (so there’s a chance to beat it), and stops after `--max-clicks` (default 5) to avoid using all your attempts. Use `--best-time SEC` if the page structure doesn’t allow reading the leaderboard.
- If the timer is not detected, the page layout may have changed; you may need to update the selectors in the script (see `TIMER_SELECTORS` in the script).

## Rules (reminder)

- [Binance Button Game FAQ](https://www.binance.info/en-AU/support/faq/detail/3941d3c08da244e0ac83af65c89d3eb5): each click uses one attempt; timer resets if someone else clicks before 00:00; to win, your click must be the last before 00:00.
- Automation may be against Binance terms; use at your own risk.
