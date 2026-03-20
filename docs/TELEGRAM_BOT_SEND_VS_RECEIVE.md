# Telegram bot: sending vs receiving messages

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

Press Ctrl+C to stop. This is for **testing/debugging**, not a production bot.

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

## Production options

- **Webhook**: HTTPS URL, valid certificate, handler that parses Telegram’s JSON POST body.
- **Polling in a long-running process**: e.g. systemd/PM2 running a small Python service (not included in the trading bot’s core flow).

Your **trade notifier** does not need `/start` to work; it only needs `chat_id` + `token` to send alerts.
