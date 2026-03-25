/**
 * PM2 ecosystem file. Start both servers:
 *   pm2 start ecosystem.config.cjs
 *
 * From project root. Uses venv Python if present: ./venv/bin/python
 *
 * Env: merge .env from project root (if present) so you can keep secrets out of the shell.
 * Create .env with (do not commit):
 *   TELEGRAM_BOT_TOKEN=...
 *   TELEGRAM_CHAT_ID=...   # optional legacy single chat; subscribers from /start also used
 *   TELEGRAM_REGISTER_SUBSCRIBERS=1   # telegram-poll-debug: save /start to data/telegram_subscribers.json (default on)
 *   TELEGRAM_SUBSCRIBERS_FILE=...     # optional path to subscriber JSON (default data/telegram_subscribers.json)
 *   PIPELINE_ON_TRADE_CLOSE=1         # optional: after each TP/SL close, run pipeline_then_pm2_restart.sh (see docs/PIPELINE_AFTER_TRADE_CLOSE.md)
 *   PIPELINE_CONFIGS=... PM2_RESTART_APPS=...     # optional overrides for that script
 *   BINANCE_API_KEY=... BINANCE_API_SECRET=...  # if not in config
 *   BINANCE_BUTTON_SMTP_EMAIL=... BINANCE_BUTTON_SMTP_PASSWORD=... BINANCE_BUTTON_EMAIL_TO=...  # for btc-game
 *   TA_SYMBOL=ETHUSDC TA_INTERVAL_SEC=300   # eth-ta-telegram: multi-TF TA digest to Telegram
 *   TA_TRADE_SIM=1   or   TA_TRADE_SIM_ENABLED=1   or   TA_TRADE_ENABLED=1   # optional: TA paper trades — docs/ETH_TA_TELEGRAM.md
 *   TA_STARTING_BALANCE=10 TA_LEVERAGE=20
 *   TA_USE_GEMINI=0|1   or   TA_GEMINI_ENABLED=0|1   # optional Gemini for eth-ta-telegram entries (default off); GEMINI_API_KEY=... GEMINI_MODEL=...
 *   TA_OPEN_EVERY_DIGEST=1 TA_DIGEST_5M_ONLY=1 TA_TP_PRICE_PCT=5 TA_SL_PRICE_PCT=3   # optional: one TA-SIM open per digest when flat (5m sign), fixed % TP/SL
 *   TA_RESET_BALANCE_ON_RESTART=1   # optional: reset TA-sim balance on pm2 restart (same as TA_RESET_ON_START)
 * Then: pm2 start ecosystem.config.cjs  or  pm2 restart <app> --update-env
 *
 * Start only btc-game:  pm2 start ecosystem.config.cjs --only btc-game
 * Start only Telegram poll debug:  pm2 start ecosystem.config.cjs --only telegram-poll-debug
 * Start only ETH TA digest:  pm2 start ecosystem.config.cjs --only eth-ta-telegram
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
  let buf = fs.readFileSync(envPath, "utf8");
  // Strip UTF-8 BOM so first key is not "\ufeffTA_..."
  if (buf.charCodeAt(0) === 0xfeff) {
    buf = buf.slice(1);
  }
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
    {
      name: "eth-ta-telegram",
      script: python,
      // TA digest → Telegram; optional TA_TRADE_SIM=1 paper trades ($10/20x/ATR TP-SL/fees) in data/ta_sim/; TA_ENTRY_ON_SIGNAL_BANNER=1 aligns opens with 📌 BULLISH/BEARISH banner (see docs/ETH_TA_TELEGRAM.md)
      args: ["-u", "scripts/eth_ta_telegram.py"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env: {
        ...env,
        PYTHONUNBUFFERED: "1",
        // Paper trades: non-empty TA_TRADE_SIM wins; else TA_TRADE_SIM_ENABLED or TA_TRADE_ENABLED; else "0"
        TA_TRADE_SIM:
          env.TA_TRADE_SIM != null && String(env.TA_TRADE_SIM).trim() !== ""
            ? env.TA_TRADE_SIM
            : env.TA_TRADE_SIM_ENABLED ?? env.TA_TRADE_ENABLED ?? "0",
        // Gemini for TA paper entries: default off; set TA_USE_GEMINI=1 or TA_GEMINI_ENABLED=1 in .env (+ GEMINI_API_KEY)
        TA_USE_GEMINI: env.TA_USE_GEMINI ?? env.TA_GEMINI_ENABLED ?? "0",
        // TA-SIM: align with scripts/backtest_ta_signals.py defaults; override in .env. Grid-tune: scripts/optimize_ta_backtest.py
        TA_SIGNAL_FILTERS: env.TA_SIGNAL_FILTERS ?? "1",
        TA_TP_PRICE_PCT: env.TA_TP_PRICE_PCT ?? "6",
        TA_SL_PRICE_PCT: env.TA_SL_PRICE_PCT ?? "2.5",
        TA_MIN_BARS_BETWEEN_TRADES: env.TA_MIN_BARS_BETWEEN_TRADES ?? "2",
      },
    },
  ],
};
