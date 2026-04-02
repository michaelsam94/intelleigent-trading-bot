# Deploy `eth_ta_telegram` on Railway

This is a **long-running worker** (loop + Binance + Telegram). It is **not** an HTTP web app.

## Repo files

| File | Purpose |
|------|---------|
| `Dockerfile.railway-eth-ta` | Builds upstream TA-Lib C lib from tarball (Debian slim has no `libta-lib` apt), then minimal pip deps |
| `requirements-railway-eth-ta.txt` | Only what `scripts/eth_ta_telegram.py` needs |
| `railway.toml` | Docker build + start command |

## Railway setup

1. **New project** → deploy this repository.
2. **Variables** (minimum — copy from your local `.env`; never commit secrets):

   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` (or use subscribers file + volume; see below)
   - `BINANCE_API_KEY` / `BINANCE_API_SECRET` if you use authenticated endpoints or live trading
   - `TA_TRADE_SIM`, `TA_REAL_TRADING`, `TA_REAL_CONFIRM`, `TA_FUTURES_SYMBOL`, etc., as you already use locally

   **Direct outbound (default on Railway):** do **not** set `SOCKS5_PROXY_*`, `BINANCE_HTTPS_PROXY`, or `HTTPS_PROXY` unless you have a specific tunnel. Railway allows normal HTTPS to `api.binance.com` and `api.telegram.org`; SOCKS was mainly for PythonAnywhere.

   If you previously added proxy variables in the Railway dashboard, **delete them** so nothing forces SOCKS timeouts.

3. **Binance API key and IP (`-2015`)**  
   If logs show `Invalid API-key, IP, or permissions` with a **request ip** (e.g. `34.x.x.x`), your key is **IP-restricted** and Railway’s outbound IP is not on the list. Fix in [Binance API Management](https://www.binance.com/en/my/settings/api-management): add that IP to the key’s whitelist, or relax restriction (only if you accept the risk). Railway IPs can **change** when you redeploy; for stable IP, use your hoster’s static-egress option or a small VPS with a fixed IP.  
   **Futures:** enable **Futures** permission on the key if `TA_REAL_TRADING=1` / `futures_klines`.  
   **Digest-only without your key’s IP:** you can use **no** `BINANCE_API_KEY` / `BINANCE_API_SECRET` for public klines only — but then **disable** `TA_REAL_TRADING` (live orders need a whitelisted key).

   **Log egress IP in app logs:** set `TA_LOG_PUBLIC_IP_ON_START=1`. On startup the worker prints the public IP seen from the same outbound path as Binance (uses your Binance proxy env if set). Use with `-2015` debugging; it does **not** fix geo blocks.

4. **Binance “restricted location” / Eligibility**  
   If the error mentions **restricted location** or **b. Eligibility** in Binance terms, Binance.com is **blocking the datacenter region** (common for some US cloud IPs). **IP whitelist does not fix this.** You need a host in an allowed jurisdiction, [Binance.US](https://www.binance.us/) where applicable, or a different compliant venue — not “add IP in Binance.”

5. **Service type**: treat as a **worker** (no public URL required). Railway will keep the process running.

6. **Persistence** (optional): default TA-SIM state lives under `data/ta_sim/`. Without a volume, **redeploys reset** that data. Add a **volume** mounted at **`/app/data`** if you want `position.json`, `balance.json`, and `data/telegram_subscribers.json` to survive.

7. **Logs**: stdout/stderr appear in Railway’s **Deployments → Logs**.

## Local Docker test

```bash
docker build -f Dockerfile.railway-eth-ta -t eth-ta-telegram .
docker run --rm -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... -e TA_TRADE_SIM=1 eth-ta-telegram
```

## Notes

- Full `requirements.txt` is **not** installed (TensorFlow etc. are excluded on purpose).
- The script handles **SIGTERM** so deploy restarts can stop cleanly between sleep ticks (~1s granularity).
- Set `TA_PRESET=none` in Railway Variables if you do **not** want the default high-win-rate preset (the script uses `setdefault` for `TA_PRESET`).
