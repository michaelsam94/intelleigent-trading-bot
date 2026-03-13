# Deployment: 1h Timeframe + Telegram Bot

This guide covers:
1. Using the **1h** config instead of 1 minute
2. **Deployment steps** (install, data, train, run server)
3. **Binding to your Telegram bot** (token, chat ID, testing)

---

## 1. Use 1h config

The project includes a ready-to-use **1h config** with Telegram placeholders:

- **Config file:** `configs/config-1h-telegram.jsonc`
- **Timeframe:** `freq: "1h"` (one candle per hour; server runs every hour)
- **Labels:** 24-bar horizon (≈ 1 day ahead)
- **Outputs:** Score notifications and diagram notifications sent to Telegram

**On a server (fresh clone):** `configs/my-1h.jsonc` is in `.gitignore` and is **not** in the repo. Use either:

- **Option 1 (recommended):** Use the template that is in the repo and set credentials via **environment variables** (see §2.5). In all commands use:
  ```bash
  -c configs/config-1h-telegram.jsonc
  ```
- **Option 2:** Create your local config on the server, then use it:
  ```bash
  cp configs/config-1h-telegram.jsonc configs/my-1h.jsonc
  # Edit configs/my-1h.jsonc: api_key, api_secret, telegram_bot_token, telegram_chat_id, data_folder
  ```
  Then use `-c configs/my-1h.jsonc` in all commands.

In the steps below, `CONFIG` means either `configs/config-1h-telegram.jsonc` (with env vars) or `configs/my-1h.jsonc` (after you create it).

---

## 2. Deployment steps

### 2.1 Prerequisites

- **Python 3.10+** (3.11 or 3.12 recommended)
- **TA-Lib** (C library): install via your OS package manager or [ta-lib](https://ta-lib.github.io/ta-lib-python/) instructions
- **Binance API** keys (read-only is enough for signals; enable trading only if you use the live trader)

### 2.2 Clone and virtual environment

```bash
cd /path/to/intelligent-trading-bot
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2.3 Install dependencies

```bash
pip install -r requirements.txt
```

If TA-Lib fails to install via pip, install the C library first (e.g. `brew install ta-lib` on macOS, or use conda).

### 2.4 Configure

1. Copy the 1h config and set **data folder** (where klines, matrix, models will be stored):

   ```json
   "data_folder": "/path/to/your/data"
   ```

2. Set **Binance** keys (optional for download/training; required for live server):

   ```json
   "api_key": "your-binance-api-key",
   "api_secret": "your-binance-api-secret"
   ```

3. Set **Telegram** (see Section 3):

   ```json
   "telegram_bot_token": "123456:ABC-DEF...",
   "telegram_chat_id": "-1001234567890"
   ```

Use `CONFIG` (see §1) in the next steps.

### 2.5 Where to add credentials on the server

You should **never** put real API keys or tokens in a file that is committed to Git. On the server you have two options:

#### Option A: Config file on the server (not in Git)

1. On the server, create a config that is **not** in the repo, e.g. `configs/my-1h.jsonc`.
2. Put your real values there:
   - `api_key`, `api_secret` (Binance)ç
   - `telegram_bot_token`, `telegram_chat_id`
   - `data_folder` (e.g. `/var/lib/itb/data`)
3. Run the server with that file:
   ```bash
   python -m service.server -c configs/my-1h.jsonc
   ```
4. Ensure `configs/my-1h.jsonc` is in `.gitignore` (it is), so it is never committed.

You can copy the template from the repo and edit it on the server:
```bash
cp configs/config-1h-telegram.jsonc configs/my-1h.jsonc
# edit configs/my-1h.jsonc with your credentials
```

#### Option B: Environment variables (recommended on server)

The app reads these **environment variables** and overrides the config when they are set:

| Env var | Config key |
|--------|------------|
| `BINANCE_API_KEY` | `api_key` |
| `BINANCE_API_SECRET` | `api_secret` |
| `TELEGRAM_BOT_TOKEN` | `telegram_bot_token` |
| `TELEGRAM_CHAT_ID` | `telegram_chat_id` |

On the server you can:

1. Use the **template** config from the repo (with placeholders); do not put secrets in it.
2. Set the env vars before starting the server.

**Example (systemd):** in your service file:

```ini
[Service]
Environment="BINANCE_API_KEY=your-key"
Environment="BINANCE_API_SECRET=your-secret"
Environment="TELEGRAM_BOT_TOKEN=your-token"
Environment="TELEGRAM_CHAT_ID=your-chat-id"
ExecStart=.../python -m service.server -c configs/config-1h-telegram.jsonc
```

**Example (shell):**

```bash
export BINANCE_API_KEY="your-key"
export BINANCE_API_SECRET="your-secret"
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python -m service.server -c configs/config-1h-telegram.jsonc
```

**Example (file not in Git):** create `/etc/itb/env` (or `~/.itb-env`) with the exports, `chmod 600`, then:

```bash
source /etc/itb/env
python -m service.server -c configs/config-1h-telegram.jsonc
```

With Option B, the config in the repo stays safe to commit and credentials live only in the environment.

### 2.6 One-time batch pipeline (download → train → models)

Run from the project root. These steps build the matrix, train models, and produce the files the server needs.

| Step | Command | Purpose |
|------|---------|---------|
| 1 | `python -m scripts.download -c CONFIG` | Download 1h klines from Binance |
| 2 | `python -m scripts.merge -c CONFIG` | Merge into one time series |
| 3 | `python -m scripts.features -c CONFIG` | Generate features |
| 4 | `python -m scripts.labels -c CONFIG` | Generate labels |
| 5 | `python -m scripts.train -c CONFIG` | Train models (SVC etc.) |
| 6 | `python -m scripts.predict -c CONFIG` | Optional: run prediction and see scores |

Use `configs/config-1h-telegram.jsonc` for CONFIG if you rely on env vars; use `configs/my-1h.jsonc` only after creating it with `cp configs/config-1h-telegram.jsonc configs/my-1h.jsonc`.

After step 5, the **MODELS** directory under your `data_folder`/symbol will contain the trained models. The server loads these on startup.

### 2.7 Run the server (online / 1h schedule)

The server fetches new 1h klines, runs the analyzer, and sends notifications (e.g. to Telegram) every **1 hour**:

```bash
python -m service.server -c configs/config-1h-telegram.jsonc
```

(Or `-c configs/my-1h.jsonc` if you created that file.)

- Runs until you stop it (Ctrl+C).
- Logs to `server.log` in the current directory.
- For production: use a process manager (systemd, supervisor, or Docker) and run the same command; see Section 2.7.

### 2.8 Production run (optional)

**PM2 (recommended for keeping the server running):**

Run the **server** (not the train script) so it stays up and fetches Binance data every 1 hour.

**Important:** If you use environment variables for credentials, export them **in the same shell** before starting PM2, or use an ecosystem file (see below). Otherwise the process won’t see them.

```bash
cd /path/to/intelleigent-trading-bot
source venv/bin/activate

# Export credentials so the server (and PM2) can see them
export BINANCE_API_KEY="your-key"
export BINANCE_API_SECRET="your-secret"
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"

pm2 start python3 --name "itb-server" -- -m service.server -c configs/config-1h-telegram.jsonc
pm2 save
pm2 startup
```

**Alternative – PM2 ecosystem file (env in file):** create `ecosystem.config.cjs` in the project root:

```javascript
module.exports = {
  apps: [{
    name: "itb-server",
    script: "python3",
    args: "-m service.server -c configs/config-1h-telegram.jsonc",
    cwd: "/home/michaelsamuelmichael/intelleigent-trading-bot",
    interpreter: "none",
    env: {
      BINANCE_API_KEY: "your-key",
      BINANCE_API_SECRET: "your-secret",
      TELEGRAM_BOT_TOKEN: "your-bot-token",
      TELEGRAM_CHAT_ID: "your-chat-id"
    }
  }]
};
```

Then run: `pm2 start ecosystem.config.cjs` (and add `ecosystem.config.cjs` to `.gitignore` if it contains secrets).

- **Do not** run `scripts.train` under PM2 as a long-running process. Train is a one-off batch job: it runs once, writes models, then exits. Use it once (or on a schedule, e.g. cron weekly) to create/refresh models.
- The **server** is what runs continuously, pulls new 1h data from Binance each hour, and sends Telegram alerts using the models that train already produced.

**systemd (Linux)** — create `/etc/systemd/system/itb-1h.service`:

```ini
[Unit]
Description=Intelligent Trading Bot 1h
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/intelligent-trading-bot
Environment="PATH=/path/to/intelligent-trading-bot/.venv/bin"
ExecStart=/path/to/intelligent-trading-bot/.venv/bin/python -m service.server -c configs/my-1h.jsonc
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable itb-1h
sudo systemctl start itb-1h
sudo systemctl status itb-1h
```

---

## 3. Bind to your Telegram bot

The bot sends messages to a **Telegram chat** (private, group, or channel) using your **Bot Token** and **Chat ID**.

### 3.1 Create a bot and get the token

1. Open Telegram and search for **@BotFather**.
2. Send: `/newbot`.
3. Follow the prompts (name and username, e.g. `My Trading Signals` and `my_trading_signals_bot`).
4. BotFather replies with a **token** like:
   ```
   7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
5. Copy this; this is your **`telegram_bot_token`**. Do not share it.

### 3.2 Get your Chat ID

You need the **chat id** of where the bot will send messages (e.g. yourself, a group, or a channel).

**Option A — Send to yourself (private chat)**

1. Start a chat with your bot: tap “Start” or send any message to the bot.
2. In the browser, open (replace `YOUR_BOT_TOKEN` with your token):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
3. In the JSON, find `"chat":{"id": 123456789, ...}`. That number is your **`telegram_chat_id`** (e.g. `123456789`).

**Option B — Send to a group**

1. Add the bot to the group and make it an admin (if you want it to post in a channel-like way).
2. Send a message in the group.
3. Visit the same URL:
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
4. Find `"chat":{"id": -1001234567890, ...}`. Group IDs are usually negative (e.g. `-1001234567890`).

**Option C — Send to a channel**

1. Add the bot as an admin of the channel.
2. Post something in the channel.
3. Use `getUpdates` as above; the `chat.id` for the channel is your **`telegram_chat_id`** (often like `-1001234567890`).

### 3.3 Put token and chat ID in config

Edit your 1h config (e.g. `configs/my-1h.jsonc`):

```json
"telegram_bot_token": "7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
"telegram_chat_id": "123456789"
```

- Use **string or number** for `telegram_chat_id` (e.g. `"-1001234567890"` or `-1001234567890`).
- No quotes in the token; the whole value is one string.

### 3.4 What gets sent to Telegram

With the default **output_sets** in `config-1h-telegram.jsonc`:

- **score_notification_model**  
  Sends a message when the trade score enters a band (e.g. BUY ZONE / SELL ZONE) or at a configured time interval.  
  Implemented in `outputs/notifier_scores.py`; uses `telegram_bot_token` and `telegram_chat_id` from config.

- **diagram_notification_model**  
  Can send a chart (e.g. price + score over the last 168 hours).  
  Implemented in `outputs/notifier_diagram.py`; uses the same token and chat ID.

So once the server is running with a valid token and chat ID, it will send those notifications to the chat you configured.

### 3.5 Test the connection

1. Start the server:
   ```bash
   python -m service.server -c configs/my-1h.jsonc
   ```
2. Wait for the next 1h candle to close (or for a band change / diagram interval). You should see a message in the configured Telegram chat.
3. Check `server.log` for errors (e.g. `Error in output function` or Telegram API errors).

Quick sanity check without waiting: temporarily set a very loose band so the first run sends a notification, or call the Telegram API manually:

```bash
# Replace TOKEN and CHAT_ID
curl -s "https://api.telegram.org/botTOKEN/sendMessage?chat_id=CHAT_ID&text=Test%20from%20ITB"
```

If you get a message in Telegram, the token and chat ID are correct.

---

## 4. Summary checklist

- [ ] Python 3.10+ and venv created
- [ ] `pip install -r requirements.txt` (and TA-Lib installed)
- [ ] Config copied to e.g. `configs/my-1h.jsonc`
- [ ] `data_folder` set
- [ ] Binance `api_key` / `api_secret` set (if using download or live data)
- [ ] Telegram bot created with BotFather; **token** copied
- [ ] **Chat ID** obtained via getUpdates and set in config
- [ ] Batch pipeline run: download → merge → features → labels → train
- [ ] Server started: `python -m service.server -c configs/my-1h.jsonc`
- [ ] Telegram chat received at least one notification (after one 1h close or a test)

You’re then running the bot on **1h** with notifications bound to **your Telegram bot**.
