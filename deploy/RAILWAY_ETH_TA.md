# Deploy `eth_ta_telegram` on Railway

This is a **long-running worker** (loop + Binance + Telegram). It is **not** an HTTP web app.

## Repo files

| File | Purpose |
|------|---------|
| `Dockerfile.railway-eth-ta` | Slim image with TA-Lib OS packages + minimal pip deps |
| `requirements-railway-eth-ta.txt` | Only what `scripts/eth_ta_telegram.py` needs |
| `railway.toml` | Docker build + start command |

## Railway setup

1. **New project** → deploy this repository.
2. **Variables** (minimum — copy from your local `.env`; never commit secrets):

   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` (or use subscribers file + volume; see below)
   - `BINANCE_API_KEY` / `BINANCE_API_SECRET` if you use authenticated endpoints or live trading
   - `TA_TRADE_SIM`, `TA_REAL_TRADING`, `TA_REAL_CONFIRM`, `TA_FUTURES_SYMBOL`, etc., as you already use locally
   - Optional proxy: `SOCKS5_PROXY_*` or `BINANCE_HTTPS_PROXY`

3. **Service type**: treat as a **worker** (no public URL required). Railway will keep the process running.

4. **Persistence** (optional): default TA-SIM state lives under `data/ta_sim/`. Without a volume, **redeploys reset** that data. Add a **volume** mounted at **`/app/data`** if you want `position.json`, `balance.json`, and `data/telegram_subscribers.json` to survive.

5. **Logs**: stdout/stderr appear in Railway’s **Deployments → Logs**.

## Local Docker test

```bash
docker build -f Dockerfile.railway-eth-ta -t eth-ta-telegram .
docker run --rm -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... -e TA_TRADE_SIM=1 eth-ta-telegram
```

## Notes

- Full `requirements.txt` is **not** installed (TensorFlow etc. are excluded on purpose).
- The script handles **SIGTERM** so deploy restarts can stop cleanly between sleep ticks (~1s granularity).
- Set `TA_PRESET=none` in Railway Variables if you do **not** want the default high-win-rate preset (the script uses `setdefault` for `TA_PRESET`).
