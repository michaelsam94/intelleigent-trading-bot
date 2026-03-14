/**
 * PM2 ecosystem file. Start both servers:
 *   pm2 start ecosystem.config.cjs
 *
 * From project root. Uses venv Python if present: ./venv/bin/python
 */
const path = require("path");
const projectRoot = __dirname;
const venvPython = path.join(projectRoot, "venv", "bin", "python");
const python = require("fs").existsSync(venvPython) ? venvPython : "python";

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
    },
    {
      name: "server-ethusdc",
      script: python,
      args: ["-m", "service.server", "-c", "configs/config-1min-realtime-ethusdc.jsonc"],
      interpreter: "none",
      cwd: projectRoot,
      autorestart: true,
      watch: false,
    },
  ],
};
