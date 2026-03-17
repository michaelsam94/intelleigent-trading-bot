# Weekly retrain (cron)

No new code needed. Use a config with `"train": true` and run the pipeline (or a retrain-only script), then restart the server so it loads the new models.

## 1. Config for retrain

Copy your realtime config and set `"train": true`:

- `configs/config-1min-realtime.jsonc` → `configs/config-1min-realtime-retrain.jsonc`
- `configs/config-1min-realtime-ethusdc.jsonc` → `configs/config-1min-realtime-ethusdc-retrain.jsonc`

In each copy, set `"train": true`. Use these only for the retrain job, not for the live server.

## 2. Crontab

Run once per week (e.g. Sunday 00:00). Adjust paths and config names.

**Single config (e.g. BTCUSDC only):**

```cron
0 0 * * 0 cd /home/ubuntu/intelleigent-trading-bot && source venv/bin/activate && ./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime-retrain.jsonc 2>&1 | tee -a logs/retrain.log
```

**Both BTCUSDC and ETHUSDC (two invocations):**

```cron
0 0 * * 0 cd /home/ubuntu/intelleigent-trading-bot && source venv/bin/activate && ./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime-retrain.jsonc 2>&1 | tee -a logs/retrain-btcusdc.log
5 0 * * 0 cd /home/ubuntu/intelleigent-trading-bot && source venv/bin/activate && ./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime-ethusdc-retrain.jsonc 2>&1 | tee -a logs/retrain-ethusdc.log
```

## 3. Restart server after retrain

So PM2 loads the new models:

```bash
pm2 restart all
# or
pm2 restart server-btcusdc server-ethusdc
```

## 4. Optional: retrain-only script

If you prefer to run only download → merge (train) → features → labels → train (no signals), add a small script that calls the pipeline up to the train step, or use `run_pipeline_to_signals.sh` with the retrain config (it trains and then runs signals once; the server keeps running with the newly written model files after you restart).
