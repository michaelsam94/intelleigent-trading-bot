# Binance BTC Button game watcher

Script: `scripts/binance_btc_button_watch.py`  
Game: [BTC Button Jan 2026](https://www.binance.com/en/game/button/btc-button-Jan2026)

## What it does

- Opens the game page in a browser with **your cookies** (you stay logged in).
- Watches the **60:00 → 00:00** countdown.
- **Prints** the current timer and **alerts** (beep + message) when the timer is under a set number of seconds (default 15) so you can click manually.
- Optional **auto-click** only when the timer is **below the current best score on the leaderboard** (so you only use attempts when you can beat the record), with a cap on clicks per run so you don’t run out of attempts (use `--auto-click`, `--max-clicks`, and optionally `--best-time`; use at your own risk and check Binance terms).
- **One attempt per run** with `--one-shot`: makes a single click when conditions are met, then exits (no continuous attempts).
- **Email after each attempt** (optional): if you set Gmail SMTP credentials in **environment variables on the server** (never in the repo), the script sends one email per click with: attempt used, time reached when clicked, and attempts left (if the page shows it).
- **Telegram after each click** (optional): if you set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the environment (or in `.env` when using PM2), the script sends a short message to your Telegram when it attempts one click (time reached, clicks this run, attempts left).

## Setup

1. **Install Playwright**
   ```bash
   pip install playwright
   playwright install chromium
   ```
   **On Linux (e.g. Ubuntu server):** Chromium needs system libraries. Install them so headless mode works:
   ```bash
   playwright install-deps
   ```
   (Uses `sudo`.) If that fails, install manually, e.g.:
   ```bash
   sudo apt-get update
   sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2
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

3. **Email after each attempt (optional, server only)**

   To get an email report after each click (attempt used, time reached, attempts left), set these **environment variables on the server** (do not commit them; add them manually where the script runs):

   - `BINANCE_BUTTON_SMTP_EMAIL` — Gmail address used to send (e.g. `yourname@gmail.com`)
   - `BINANCE_BUTTON_SMTP_PASSWORD` — Gmail App Password (not your normal password; create one in Google Account → Security → 2-Step Verification → App passwords)
   - `BINANCE_BUTTON_EMAIL_TO` — (optional) Recipient address. If set, reports are sent to this address; if unset, they are sent to the SMTP email.

   If either `BINANCE_BUTTON_SMTP_EMAIL` or `BINANCE_BUTTON_SMTP_PASSWORD` is missing, no email is sent.

   **Telegram (optional)**  
   To get a Telegram message when the script clicks the BTC button once, set (e.g. in `.env` or before `pm2 start`):
   - `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `TELEGRAM_CHAT_ID` — your chat ID (e.g. from [@userinfobot](https://t.me/userinfobot))
   If either is missing, no Telegram message is sent.

4. **Ignore the file in Git**

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

# One attempt per run: click once when timer is below best, send email if env set, then exit (good for cron)
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --auto-click --best-time 8 --one-shot --headless

# Test that the button can be found and clicked (uses 1 attempt, then exits)
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --test-click --headless

# See which element we would click, without clicking (no attempt used). Use if --test-click did not update "Last Attempt".
python scripts/binance_btc_button_watch.py -c data/binance_btc_button_cookies.json --test-find-button --headless
```

- **Ctrl+C** stops the script.
- **Test click**: Use `--test-click` to open the page, find the game button, click it once, and exit. Prints `[OK] Button found and clicked` or `[FAIL]`. **Uses one attempt** if the click succeeds. If Binance "Last Attempt" does **not** update to today, the script may have clicked a different element (e.g. another button); use `--test-find-button` next to see what we match.
- **Test find button**: Use `--test-find-button` to list which element(s) the script would click (selector, frame, tag, class). **Does not click** and does not use an attempt. Use this to confirm we target the real game button, or to get the button's class so it can be added to the script.
- **One-shot**: Use `--one-shot` so the script makes **one** attempt (one click when conditions are met), sends the email report if SMTP env vars are set, then exits. This avoids using multiple attempts in one run; schedule the script (e.g. with cron) to run periodically.
- **Email body** contains: attempt used (e.g. 1), time reached when you clicked (e.g. 0:05), and attempts left (from the page if detected, otherwise "N/A (check game)").
- **Auto-click behaviour**: The script tries to read the leaderboard best time (closest to 00:00). It only clicks when the current timer is **at or below** that best time (so there’s a chance to beat it), and stops after `--max-clicks` (default 5) to avoid using all your attempts. Use `--best-time SEC` if the page structure doesn’t allow reading the leaderboard.
- If the timer is not detected, the page layout may have changed; you may need to update the selectors in the script (see `TIMER_SELECTORS` in the script).

## Rules (reminder)

- [Binance Button Game FAQ](https://www.binance.info/en-AU/support/faq/detail/3941d3c08da244e0ac83af65c89d3eb5): each click uses one attempt; timer resets if someone else clicks before 00:00; to win, your click must be the last before 00:00.
- Automation may be against Binance terms; use at your own risk.
