# Intelligent Trading Bot

## Overview
A Python-based framework for developing and deploying automated trading strategies using machine learning. It supports both offline batch processing (data collection, feature engineering, model training) and online real-time execution (streaming data, predictions, signals, trades).

## Tech Stack
- **Language:** Python 3.12
- **Package Manager:** pip (requirements.txt)
- **ML:** scikit-learn, lightgbm, xgboost, tensorflow/keras, optuna, shap
- **Data:** pandas, numpy, pyarrow, numba
- **Technical Analysis:** ta-lib (requires native ta-lib system library)
- **APIs:** python-binance (Binance), google-genai / google-generativeai (Gemini AI), websockets
- **Scheduling:** apscheduler
- **Process Manager (production):** PM2 (ecosystem.config.cjs)

## Project Structure
```
common/      - Core ML classifiers, feature/label generators, utilities
service/     - Online server (server.py, App.py)
scripts/     - Batch processing: download, merge, train, backtest
inputs/      - Data collectors (Binance, Yahoo, MT5, WebSockets)
outputs/     - Output handlers: Telegram notifier, exchange traders
configs/     - JSONC config files for bot behaviors
docs/        - Documentation
tests/       - Unit tests
```

## Running the Server
The server requires a config file and API credentials:

```bash
python3 -m service.server --config_file configs/config-sample-1min.jsonc
```

### Required Environment Variables
- `BINANCE_API_KEY` - Binance API key
- `BINANCE_API_SECRET` - Binance API secret
- `TELEGRAM_BOT_TOKEN` - Telegram bot token (optional, for notifications)
- `TELEGRAM_CHAT_ID` - Telegram chat ID (optional, for notifications)

## Workflow
The "Start application" workflow runs `python3 -m service.server` as a console service.
To use it with a specific config: update the workflow command to include `--config_file configs/<your-config>.jsonc`.

## Configuration
Config files are in `configs/`. Available examples:
- `config-sample-1min.jsonc` - 1-minute interval sample
- `config-sample-1h.jsonc` - 1-hour interval sample
- `config-1min-realtime.jsonc` - 1-minute realtime (WebSocket)
- `config-5min-realtime.jsonc` - 5-minute realtime
- `config-1h-telegram.jsonc` - 1-hour with Telegram notifications
- `config-mt5-sample-1h.jsonc` - MetaTrader5 sample

## System Dependencies
- `ta-lib` (Nix system package) - Required for the ta-lib Python wrapper

## Execution Workflow (Typical Usage)
1. Download historical data: `python3 -m scripts.download`
2. Merge data: `python3 -m scripts.merge`
3. Generate features: `python3 -m scripts.features`
4. Generate labels: `python3 -m scripts.labels`
5. Train models: `python3 -m scripts.train`
6. Run server: `python3 -m service.server --config_file configs/<config>.jsonc`
