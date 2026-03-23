# Pipeline + PM2 restart after each trade close

The **`server-*`** PM2 apps run `service.server`: they **monitor** the market, compute scores/signals, and run the **simulated** trader (TP/SL). When a position **closes**, you can optionally run **`scripts/run_pipeline_to_signals.sh`** with the **configs you choose**, then **restart** PM2 so servers load new models.

Flow: **edit `.env`** → **`pm2 restart … --update-env`** so child processes see `PIPELINE_ON_TRADE_CLOSE`, `PIPELINE_CONFIGS`, etc.

## Enable

In **`.env`** at the project root (loaded by `ecosystem.config.cjs`):

```bash
PIPELINE_ON_TRADE_CLOSE=1
PIPELINE_CONFIGS="configs/config-5min-realtime.jsonc configs/config-5min-realtime-ethusdc.jsonc"
PM2_RESTART_APPS=server-btcusdc-5min,server-ethusdc-5min
```

Adjust paths and PM2 **app names** to match `ecosystem.config.cjs`. Then reload env:

```bash
pm2 restart ecosystem.config.cjs --update-env
# or one server:
pm2 restart server-btcusdc-5min --update-env
```

## What runs

1. **`scripts/pipeline_then_pm2_restart.sh`** is spawned in the **background** (does not block the trading server event loop).
2. It runs **`scripts/run_pipeline_to_signals.sh`** with the configs you choose (see below).
3. After the pipeline finishes, it **restarts PM2 apps** that exist (skips names not running).

Logs: **`logs/pipeline_after_close.log`** (under project root).

## Lock

Only **one** pipeline run at a time. If a trade closes while a pipeline is already running, the new run is **skipped** (message in the log).

## Optional environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `PIPELINE_ON_TRADE_CLOSE` | (off) | Set `1` / `true` / `yes` / `on` to enable |
| `PIPELINE_CONFIGS` | `config-1min-realtime.jsonc` + `config-1min-realtime-ethusdc.jsonc` | Space-separated config paths |
| `PM2_RESTART_APPS` | `server-btcusdc,server-btcusdc-5min,server-ethusdc,server-ethusdc-5min` | Comma-separated PM2 app names |
| `PIPELINE_LOCK_DIR` | `/tmp/itb_pipeline_lock` | Directory used as mutex (created/deleted) |
| `PIPELINE_AFTER_CLOSE_LOG` | `logs/pipeline_after_close.log` | Log file for the wrapper script |

Example including 5m configs in the pipeline:

```bash
PIPELINE_ON_TRADE_CLOSE=1
PIPELINE_CONFIGS="configs/config-5min-realtime.jsonc configs/config-5min-realtime-ethusdc.jsonc"
PM2_RESTART_APPS="server-btcusdc-5min,server-ethusdc-5min"
```

## Warnings

- The pipeline can take **a long time** (download, train, etc.) and uses **CPU/RAM**.
- While it runs, PM2 servers are still on old models until the script reaches the **restart** step at the end.
- **`telegram-poll-debug`** is **not** restarted by default (only the four `server-*` apps). Add it to `PM2_RESTART_APPS` if needed.

## Manual run

```bash
./scripts/pipeline_then_pm2_restart.sh
```
