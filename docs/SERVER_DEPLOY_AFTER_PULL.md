# Server: steps after `git pull`

Run these on the server after pulling the latest code.

---

## 1. Install dependencies

```bash
cd /path/to/intelligent-trading-bot
source venv/bin/activate   # or: . venv/bin/activate
pip install -r requirements.txt
```

This installs/updates **xgboost** (and any other new deps). If you don’t use a venv, run `pip install -r requirements.txt` with the Python that runs the bot.

---

## 2. Retrain once (new XGB + Bollinger features)

The config now has 3 algorithms (lc, gb, xgb) and extra features (Bollinger Bands). You must retrain so the server has models and scalers that match.

**Option A – use main config with `train: true`**

1. Edit the config and set `"train": true`.
2. Run the pipeline through the **train** step:

   ```bash
   python -m scripts.download -c configs/config-1min-realtime.jsonc
   python -m scripts.merge -c configs/config-1min-realtime.jsonc --train
   python -m scripts.features -c configs/config-1min-realtime.jsonc
   python -m scripts.labels -c configs/config-1min-realtime.jsonc
   python -m scripts.train -c configs/config-1min-realtime.jsonc
   ```

3. Set `"train": false` again in the config (so the server doesn’t try to retrain on every run).
4. Repeat for ETHUSDC if you use it:

   ```bash
   python -m scripts.download -c configs/config-1min-realtime-ethusdc.jsonc
   python -m scripts.merge -c configs/config-1min-realtime-ethusdc.jsonc --train
   python -m scripts.features -c configs/config-1min-realtime-ethusdc.jsonc
   python -m scripts.labels -c configs/config-1min-realtime-ethusdc.jsonc
   python -m scripts.train -c configs/config-1min-realtime-ethusdc.jsonc
   ```
   Then set `"train": false` in the ETHUSDC config.

**Option B – use the full pipeline script**

1. Set `"train": true` in the config(s).
2. Run:

   ```bash
   ./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime.jsonc
   ./scripts/run_pipeline_to_signals.sh configs/config-1min-realtime-ethusdc.jsonc
   ```

3. Set `"train": false` again in both configs.

---

## 3. Restart the app

So the server loads the new models and code:

```bash
pm2 restart all
```

If you run the bot without PM2, stop the current process and start it again with your usual command (e.g. `python -m service.server -c configs/config-1min-realtime.jsonc`).

---

## 4. Optional: walk-forward validation

To run walk-forward validation (rolling train/predict) you need the **matrix** file (features + labels). After a full pipeline through **labels** you already have it. Then:

```bash
python -m scripts.predict_rolling -c configs/config-1min-realtime.jsonc
```

No need to add config: `matrix_file_name` and `rolling_predict` are already in the 1min configs.

---

## 5. Optional: weekly retrain (cron)

To retrain every week and then restart the server:

1. Create retrain configs (copies with `"train": true`), e.g.  
   `configs/config-1min-realtime-retrain.jsonc` and  
   `configs/config-1min-realtime-ethusdc-retrain.jsonc`.

2. Add to crontab (`crontab -e`), e.g. Sunday 00:00:

   ```cron
   0 0 * * 0 cd /path/to/intelligent-trading-bot && ./scripts/weekly_retrain.sh configs/config-1min-realtime-retrain.jsonc configs/config-1min-realtime-ethusdc-retrain.jsonc && pm2 restart all
   ```

   Replace `/path/to/intelligent-trading-bot` with the real repo path on the server.

---

## 6. PM2 start on reboot

To have PM2 bring up all services automatically after a server reboot:

1. **Save the current process list** (run from project root after you have started your apps with `pm2 start ecosystem.config.cjs` or equivalent):

   ```bash
   pm2 save
   ```

2. **Generate and install the startup script** (run the command that PM2 prints; it usually needs `sudo`):

   ```bash
   pm2 startup
   ```

   PM2 will print something like:
   `sudo env PATH=... pm2 startup systemd -u ubuntu --hp /home/ubuntu`
   Copy and run that exact line.

3. After that, every reboot will run `pm2 resurrect` and restore the saved processes. To change what gets restored, adjust your apps, then run `pm2 save` again.

---

## Checklist

| Step | Command / action |
|------|-------------------|
| 1. Pull | `git pull` |
| 2. Deps | `pip install -r requirements.txt` (in venv) |
| 3. Retrain | Set `train: true`, run pipeline through **train**, set `train: false` (for each config you use) |
| 4. Restart | `pm2 restart all` (or restart your server process) |
| 5. (Optional) Walk-forward | `python -m scripts.predict_rolling -c configs/config-1min-realtime.jsonc` |
| 6. (Optional) Cron | Add weekly retrain + `pm2 restart all` to crontab |
| 7. (Optional) Reboot | `pm2 save` then `pm2 startup` (run the printed sudo command) so services start after reboot |
