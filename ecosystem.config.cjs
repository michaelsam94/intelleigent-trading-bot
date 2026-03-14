/**
 * PM2 ecosystem file. Start both servers:
 *   pm2 start ecosystem.config.cjs
 *
 * From project root. Uses venv Python if present: ./venv/bin/python
 *
 * Telegram/Binance: set env vars before starting so they are passed to the app:
 *   export TELEGRAM_BOT_TOKEN="..."
 *   export TELEGRAM_CHAT_ID="..."
 *   export BINANCE_API_KEY="..." BINANCE_API_SECRET="..."  # if not in config
 *   pm2 start ecosystem.config.cjs
 */
const path = require("path");
const projectRoot = __dirname;
const venvPython = path.join(projectRoot, "venv", "bin", "python");
const python = require("fs").existsSync(venvPython) ? venvPython : "python";

// Pass through current env so TELEGRAM_* and BINANCE_* set before "pm2 start" reach the app
const env = { ...process.env };

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
      name: "server-ethusdc",
      script: python,
      args: ["-m", "service.server", "-c", "configs/config-1min-realtime-ethusdc.jsonc"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
      env,
    },
  ],
};
