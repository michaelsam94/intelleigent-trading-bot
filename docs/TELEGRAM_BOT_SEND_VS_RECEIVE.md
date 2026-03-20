# Telegram bot: sending vs receiving messages

## Broadcast alerts to every user who sends `/start`

The trading server sends Telegram alerts to **every chat ID** in this list:

1. **`data/telegram_subscribers.json`** — filled when users message your bot **`/start`** (handled by PM2 app **`telegram-poll-debug`**). Each user is stored once; trade/score notifications go to **all** of them.
2. **Optional legacy:** `telegram_chat_id` in config and/or **`TELEGRAM_CHAT_ID`** in `.env` — still merged in (deduplicated) if you want one fixed admin chat in addition to subscribers.

Flow:

1. Run **`telegram-poll-debug`** on the server (same machine as the trading servers so they share the `data/` folder — or set **`TELEGRAM_SUBSCRIBERS_FILE`** to the same path everywhere).
2. Each user opens your bot in Telegram and sends **`/start`**. They get a confirmation; their `chat.id` is saved.
3. **`/stop`** removes them from the list.
4. Restart or run **`service.server`** — it reads the file on each send and broadcasts.

Env:

| Variable | Meaning |
|----------|---------|
| `TELEGRAM_REGISTER_SUBSCRIBERS` | `1` (default) = poll script registers `/start`; `0` = log only |
| `TELEGRAM_SUBSCRIBERS_FILE` | Path to JSON list of chat ids (default `data/telegram_subscribers.json`) |

The `data/` directory is gitignored; subscriber IDs stay on your server.

---

## Why notifications work but `/start` seems “ignored”

This project (and many trading setups) only **sends** messages to Telegram:

- `POST https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=...`

Telegram delivers that to the user’s chat. No listener is required.

**Receiving** user messages (`/start`, text, etc.) is different: Telegram does **not** push them to your app unless you implement one of:

1. **Long polling** — your script repeatedly calls `getUpdates`
2. **Webhook** — you register an HTTPS URL; Telegram POSTs updates to your server

If you never run polling and never set a webhook, your code will never “see” `/start`. That is expected.

## Quick checks (no code)

1. **Remove webhook** (polling won’t work if a webhook is set):

   ```text
   https://api.telegram.org/bot<YOUR_TOKEN>/deleteWebhook
   ```

2. **After user taps Start**, fetch updates:

   ```text
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```

   You should see JSON with `message.text` = `/start` and `chat.id`.

## Debug script (polling, prints updates)

From project root, with `TELEGRAM_BOT_TOKEN` set:

```bash
export TELEGRAM_BOT_TOKEN="..."
python scripts/telegram_poll_debug.py
```

Press Ctrl+C to stop. With default settings it also **registers subscribers** on `/start` for broadcast alerts.

### Run with PM2 (from repo)

`TELEGRAM_BOT_TOKEN` should be in project `.env` (loaded by PM2). Then:

```bash
pm2 start ecosystem.config.cjs --only telegram-poll-debug
pm2 logs telegram-poll-debug
```

Optional one-time webhook clear (add to `.env`, restart once, then remove):

```bash
TELEGRAM_DELETE_WEBHOOK=1
```

### PM2 logs look empty / no `/start` appears

1. **Restart with env** after editing `.env`:

   ```bash
   pm2 restart telegram-poll-debug --update-env
   ```

2. **Read startup lines** in `pm2 logs telegram-poll-debug` — the script prints `OK: bot @YourBot` and either `OK: no webhook` or a **WARN: Webhook is set**. If a webhook URL is set, **`getUpdates` stays empty** until you run `deleteWebhook` (set `TELEGRAM_DELETE_WEBHOOK=1` once as above).

3. **Same bot** — Open the bot whose **@username** matches the line `OK: bot @…` in the logs. A different bot or wrong token will never show your `/start` here.

4. **Buffering** — The app uses `python -u` and `PYTHONUNBUFFERED=1` so lines should appear immediately in PM2.

## Production options

- **Webhook**: HTTPS URL, valid certificate, handler that parses Telegram’s JSON POST body (you’d implement the same subscriber file logic there).
- **Polling in a long-running process**: PM2 app **`telegram-poll-debug`** — required if you rely on `/start` subscribers instead of a single `TELEGRAM_CHAT_ID`.

**Trade/score Telegram** needs **`TELEGRAM_BOT_TOKEN`** (or config) plus **at least one recipient**: either subscribers from `/start` or `telegram_chat_id` / `TELEGRAM_CHAT_ID`.
