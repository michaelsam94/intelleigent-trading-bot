const path = require("path");
const fs = require("fs");

const projectRoot = __dirname;
const venvPython = path.join(projectRoot, "venv", "bin", "python");
const python = fs.existsSync(venvPython) ? venvPython : "python";

// Load .env if exists
const env = { ...process.env };
const envPath = path.join(projectRoot, ".env");
if (fs.existsSync(envPath)) {
  let buf = fs.readFileSync(envPath, "utf8");
  if (buf.charCodeAt(0) === 0xfeff) buf = buf.slice(1); // remove BOM
  buf.split("\n").forEach((line) => {
    line = line.replace(/#.*/, "").trim();
    if (!line) return;
    const eq = line.indexOf("=");
    if (eq > 0) {
      const key = line.slice(0, eq).trim();
      let val = line.slice(eq + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      env[key] = val;
    }
  });
}

// Ensure venv is always used
env.VIRTUAL_ENV = path.join(projectRoot, "venv");
env.PATH = path.join(projectRoot, "venv", "bin") + ":" + process.env.PATH;

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
      args: ["-u", "scripts/eth_ta_telegram.py"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env: {
        ...env,
        PYTHONUNBUFFERED: "1",
        TA_TRADE_SIM:
          env.TA_TRADE_SIM != null && String(env.TA_TRADE_SIM).trim() !== ""
            ? env.TA_TRADE_SIM
            : env.TA_TRADE_SIM_ENABLED ?? env.TA_TRADE_ENABLED ?? "0",
        TA_USE_GEMINI: env.TA_USE_GEMINI ?? env.TA_GEMINI_ENABLED ?? "0",
        TA_PRESET: env.TA_PRESET ?? "high-win-rate",
        TA_SIGNAL_FILTERS: env.TA_SIGNAL_FILTERS ?? "1",
        TA_TP_PRICE_PCT: env.TA_TP_PRICE_PCT ?? "6",
        TA_SL_PRICE_PCT: env.TA_SL_PRICE_PCT ?? "2.5",
        TA_MIN_BARS_BETWEEN_TRADES: env.TA_MIN_BARS_BETWEEN_TRADES ?? "2",
        TA_DIGEST_LOG_FILE: env.TA_DIGEST_LOG_FILE ?? "data/eth_ta_ethusdc.log",
        VIRTUAL_ENV: path.join(projectRoot, "venv"),
        PATH: path.join(projectRoot, "venv", "bin") + ":" + process.env.PATH,
      },
    },
  ],
};