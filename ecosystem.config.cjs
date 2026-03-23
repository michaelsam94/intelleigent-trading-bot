/**
 * PM2 ecosystem — project root, venv Python: ./venv/bin/python
 *
 * PURPOSE (what each app does)
 * ----------------------------
 * server-btcusdc / server-btcusdc-5min / server-ethusdc / server-ethusdc-5min
 *   → `python -m service.server -c <config>`: live market data, ML predictions, signals,
 *      simulated trades (TP/SL). These are the “signal monitors” for each pair/timeframe.
 * btc-game → Binance BTC button watcher (optional).
 * telegram-poll-debug → Long-poll Telegram; /start registers chat IDs for broadcast alerts.
 *
 * After you edit .env, reload env into PM2:
 *   pm2 restart ecosystem.config.cjs --update-env
 *
 * --- Retrain + restart after each simulated trade closes (TP/SL) ---
 * Set in .env (see docs/PIPELINE_AFTER_TRADE_CLOSE.md). Child servers spawn
 * scripts/pipeline_then_pm2_restart.sh → run_pipeline_to_signals.sh (your configs) → pm2 restart …
 *
 *   PIPELINE_ON_TRADE_CLOSE=1
 *   # Space-separated jsonc paths (same as run_pipeline_to_signals.sh arguments):
 *   PIPELINE_CONFIGS="configs/config-5min-realtime.jsonc configs/config-5min-realtime-ethusdc.jsonc"
 *   # Comma-separated PM2 apps to restart after pipeline finishes (must match names below):
 *   PM2_RESTART_APPS=server-btcusdc-5min,server-ethusdc-5min
 *
 * Optional: PIPELINE_LOCK_DIR, PIPELINE_AFTER_CLOSE_LOG
 *
 * Other .env (do not commit):
 *   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
 *   TELEGRAM_REGISTER_SUBSCRIBERS=1   TELEGRAM_SUBSCRIBERS_FILE=...
 *   BINANCE_API_KEY=... BINANCE_API_SECRET=...
 *   BINANCE_BUTTON_SMTP_EMAIL=... (btc-game)
 *
 * pm2 start ecosystem.config.cjs
 * pm2 start ecosystem.config.cjs --only btc-game
 * pm2 start ecosystem.config.cjs --only telegram-poll-debug
 */
const path = require("path");
const fs = require("fs");
const projectRoot = __dirname;
const venvPython = path.join(projectRoot, "venv", "bin", "python");
const python = fs.existsSync(venvPython) ? venvPython : "python";

// Start from current process env, then overlay .env from project root (if present)
const env = { ...process.env };
const envPath = path.join(projectRoot, ".env");
if (fs.existsSync(envPath)) {
  const buf = fs.readFileSync(envPath, "utf8");
  buf.split("\n").forEach((line) => {
    line = line.replace(/#.*/, "").trim();
    if (!line) return;
    const eq = line.indexOf("=");
    if (eq > 0) {
      const key = line.slice(0, eq).trim();
      let val = line.slice(eq + 1).trim();
      if (val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1).replace(/\\"/g, '"');
      if (val.startsWith("'") && val.endsWith("'")) val = val.slice(1, -1).replace(/\\'/g, "'");
      env[key] = val;
    }
  });
}

module.exports = {
  apps: [
    {
      name: "server-btcusdc",
      // Live signals + trades (BTC 1m); honors PIPELINE_ON_TRADE_CLOSE from .env
      script: python,
      args: ["-m", "service.server", "-c", "configs/config-1min-realtime.jsonc"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env,
    },
    {
      name: "server-btcusdc-5min",
      script: python,
      args: ["-m", "service.server", "-c", "configs/config-5min-realtime.jsonc"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env,
    },
    {
      name: "server-ethusdc",
      script: python,
      args: ["-m", "service.server", "-c", "configs/config-1min-realtime-ethusdc.jsonc"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env,
    },
    {
      name: "server-ethusdc-5min",
      script: python,
      args: ["-m", "service.server", "-c", "configs/config-5min-realtime-ethusdc.jsonc"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env,
    },
    {
      name: "btc-game",
      script: python,
      args: [
        "scripts/binance_btc_button_watch.py",
        "-c", "data/binance_btc_button_cookies.json",
        "--auto-click", "--best-time", "54", "--one-shot", "--headless",
      ],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: false,
      watch: false,
      env,
    },
    {
      name: "telegram-poll-debug",
      script: python,
      // -u: unbuffered stdout/stderr so PM2 logs show lines immediately
      args: ["-u", "scripts/telegram_poll_debug.py"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env: { ...env, PYTHONUNBUFFERED: "1" },
    },
  ],
};
