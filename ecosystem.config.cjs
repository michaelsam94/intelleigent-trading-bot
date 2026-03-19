/**
 * PM2 ecosystem file. Start both servers:
 *   pm2 start ecosystem.config.cjs
 *
 * From project root. Uses venv Python if present: ./venv/bin/python
 *
 * Env: merge .env from project root (if present) so you can keep secrets out of the shell.
 * Create .env with (do not commit):
 *   TELEGRAM_BOT_TOKEN=...
 *   TELEGRAM_CHAT_ID=...
 *   BINANCE_API_KEY=... BINANCE_API_SECRET=...  # if not in config
 *   BINANCE_BUTTON_SMTP_EMAIL=... BINANCE_BUTTON_SMTP_PASSWORD=... BINANCE_BUTTON_EMAIL_TO=...  # for btc-game
 * Then: pm2 start ecosystem.config.cjs  or  pm2 restart <app> --update-env
 *
 * Start only btc-game:  pm2 start ecosystem.config.cjs --only btc-game
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
  ],
};
