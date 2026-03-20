import os
import sys
import json
from datetime import timedelta, datetime
from pathlib import Path

import asyncio

import pandas as pd
import pandas.api.types as ptypes

from service.App import *
from common.utils import *
from common.model_store import *
from common.telegram_broadcast import broadcast_telegram_markdown, recipient_chat_ids

import logging
log = logging.getLogger('notifier')

logging.getLogger('PIL').setLevel(logging.WARNING)
logging.getLogger('matplotlib').setLevel(logging.WARNING)


async def trader_simulation(df, model: dict, config: dict, model_store: ModelStore):
    try:
        transaction = await generate_trader_transaction(df, model, config)
    except Exception as e:
        log.error(f"Error in trader_simulation function: {e}")
        return
    if not transaction:
        return

    try:
        await send_transaction_message(transaction, config)
    except Exception as e:
        log.error(f"Error in send_transaction_message function: {e}")
        return


async def generate_trader_transaction(df, model: dict, config: dict):
    """
    Trade with TP/SL: open on signal, close only when TP or SL is hit. No new signal until position closed.
    TP/SL are ATR-based (or percentage fallback) from config tp_sl.
    """
    transaction_path = get_transaction_path()
    buy_signal_column = model.get("buy_signal_column")
    sell_signal_column = model.get("sell_signal_column")
    tp_sl_cfg = model.get("tp_sl") or {}

    signal = get_signal(df, buy_signal_column, sell_signal_column)
    signal_side = signal.get("side")
    close_price = signal.get("close_price")
    close_time = signal.get("close_time")

    row = df.iloc[-1]
    high_price = float(row.get("high", close_price))
    low_price = float(row.get("low", close_price))

    position = load_position()

    # --- 1) We have an open position: check TP/SL (and trailing stop) on this bar (high/low) ---
    if position and position.get("open"):
        side = position.get("side")  # "LONG" or "SHORT"
        entry = position.get("entry_price")
        tp_price = position.get("tp_price")
        sl_price = position.get("sl_price")
        entry_time = position.get("entry_time")
        atr_at_entry = position.get("atr_at_entry") or 0.0

        # Trailing stop: lock profits as price moves in our favor (optional, enabled via config)
        trailing_enabled = tp_sl_cfg.get("trailing_stop_enabled", False)
        trailing_mult = tp_sl_cfg.get("trailing_atr_mult")
        if trailing_enabled and trailing_mult is not None and atr_at_entry > 0:
            trail_dist = float(trailing_mult) * atr_at_entry
            best = position.get("best_price")
            if side == "LONG":
                best = max(best if best is not None else entry, high_price)
                trailing_sl = best - trail_dist
                sl_price = max(sl_price, trailing_sl)  # effective SL
                position["best_price"] = best
            else:  # SHORT
                best = min(best if best is not None else entry, low_price)
                trailing_sl = best + trail_dist
                sl_price = min(sl_price, trailing_sl)  # effective SL
                position["best_price"] = best
            save_position(position)

        hit_tp, hit_sl = False, False
        exit_price = close_price
        if side == "LONG":
            if high_price >= tp_price:
                hit_tp = True
                exit_price = tp_price
            elif low_price <= sl_price:
                hit_sl = True
                exit_price = sl_price
        else:  # SHORT
            if low_price <= tp_price:
                hit_tp = True
                exit_price = tp_price
            elif high_price >= sl_price:
                hit_sl = True
                exit_price = sl_price

        if hit_tp or hit_sl:
            if side == "LONG":
                profit = exit_price - entry
                status_close = "SELL"
            else:
                profit = entry - exit_price
                status_close = "BUY"
            profit_pct = 100.0 * profit / entry if entry else 0.0

            # Leverage and fees (margin-based stats)
            leverage = float(model.get("leverage", 20))
            fee_bps = float(model.get("fee_bps_per_side", 4))
            balance_before, starting_balance = load_balance(model)
            leveraged_pnl_pct = profit_pct * leverage
            fee_margin_pct = 2 * (fee_bps / 10000.0) * leverage * 100.0  # fee as % of margin (open+close on notional)
            balance_after = balance_before * (1.0 + leveraged_pnl_pct / 100.0 - fee_margin_pct / 100.0)
            balance_after = max(0.01, balance_after)  # avoid zero
            save_balance(balance_after, starting_balance)
            _update_daily_after_close(model, balance_after)
            fee_usd = balance_before * (fee_margin_pct / 100.0)
            total_return_pct = 100.0 * (balance_after - starting_balance) / starting_balance if starting_balance else 0.0

            # Append close to transaction log (same format as before for stats)
            transaction_path.parent.mkdir(parents=True, exist_ok=True)
            t_line = f"{close_time},{exit_price:.2f},{profit:.2f},{status_close}\n"
            with open(transaction_path, "a") as f:
                f.write(t_line)

            clear_position()
            _save_last_close_time(close_time)
            App.transaction = dict(
                timestamp=str(close_time), price=exit_price, profit=profit, status=status_close
            )

            log.info(f"Position closed: {side} @ {entry} -> {exit_price} ({'TP' if hit_tp else 'SL'}) PnL: {profit:.2f} ({profit_pct:.2f}%) | Balance: ${balance_after:.2f}")

            return {
                "status": "CLOSED",
                "side": side,
                "exit_reason": "TP" if hit_tp else "SL",
                "entry_price": entry,
                "entry_time": entry_time,
                "exit_price": exit_price,
                "close_time": close_time,
                "profit": profit,
                "profit_percent": profit_pct,
                "win": hit_tp,
                "leveraged_pnl_pct": leveraged_pnl_pct,
                "fee_margin_pct": fee_margin_pct,
                "fee_usd": fee_usd,
                "balance_before": balance_before,
                "balance_after": balance_after,
                "starting_balance": starting_balance,
                "total_return_pct": total_return_pct,
            }
        # Position still open: do not send a new signal
        return None

    # --- 2) No position: open on BUY/SELL signal with TP/SL ---
    if signal_side not in ("BUY", "SELL"):
        return None

    if _is_drawdown_paused(model):
        log.warning("Skipping open: daily drawdown limit reached (paused). No new trades until next day or increase daily_drawdown_limit_pct.")
        return None

    min_bars = model.get("min_bars_between_trades")
    if min_bars is not None and int(min_bars) > 0:
        last_close = _load_last_close_time()
        if last_close is not None:
            try:
                if hasattr(close_time, "timestamp"):
                    now_ts = close_time.timestamp()
                else:
                    now_ts = pd.Timestamp(close_time).timestamp()
                last_ts = pd.Timestamp(last_close).timestamp()
                bars_since = (now_ts - last_ts) / 60.0
                if bars_since < int(min_bars):
                    log.debug("Skipping open: cooldown (%.0f bars since last close, need %s).", bars_since, min_bars)
                    return None
            except Exception:
                pass

    if signal_side == "SELL":
        trend_col = model.get("short_trend_filter_column")
        if trend_col and trend_col in row.index:
            try:
                val = float(row[trend_col])
                if val > 0:
                    log.debug("Skipping SHORT: trend filter %s=%.4f > 0 (uptrend).", trend_col, val)
                    return None
            except (TypeError, ValueError):
                pass

    atr_col = tp_sl_cfg.get("atr_column")
    tp_mult = float(tp_sl_cfg.get("tp_atr_mult", 2.0))
    sl_mult = float(tp_sl_cfg.get("sl_atr_mult", 1.5))
    tp_pct = float(tp_sl_cfg.get("tp_pct_fallback", 0.5)) / 100.0
    sl_pct = float(tp_sl_cfg.get("sl_pct_fallback", 0.3)) / 100.0

    atr = None
    if atr_col and atr_col in row.index:
        try:
            atr = float(row[atr_col])
        except (TypeError, ValueError):
            pass
    if atr is None or atr <= 0:
        atr = close_price * (tp_pct + sl_pct) / 2  # fallback distance

    if signal_side == "BUY":
        side = "LONG"
        tp_price = close_price + atr * tp_mult
        sl_price = close_price - atr * sl_mult
    else:
        side = "SHORT"
        tp_price = close_price - atr * tp_mult
        sl_price = close_price + atr * sl_mult

    save_position({
        "open": True,
        "side": side,
        "entry_price": close_price,
        "entry_time": str(close_time),
        "tp_price": tp_price,
        "sl_price": sl_price,
        "atr_at_entry": atr,
    })

    log.info(f"Position opened: {side} @ {close_price} TP={tp_price:.2f} SL={sl_price:.2f}")

    return {
        "status": "OPEN_LONG" if side == "LONG" else "OPEN_SHORT",
        "side": side,
        "price": close_price,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "close_time": close_time,
    }


def _is_placeholder(val):
    """Treat config placeholders as missing so we fall back to env vars."""
    if not val or not isinstance(val, str):
        return True
    s = val.strip()
    return "<" in s or ">" in s or s.lower().startswith("your-") or s == ""


def _send_telegram(bot_token, text, config):
    """Broadcast to all /start subscribers + legacy telegram_chat_id / TELEGRAM_CHAT_ID."""
    n = broadcast_telegram_markdown(bot_token, text, config)
    if n == 0:
        log.error("Telegram broadcast failed for all recipients (0 ok). Check token and chat_ids.")


async def send_transaction_message(transaction, config):
    # Prefer config values, but treat placeholders as missing and fall back to env vars.
    cfg_token = (config.get("telegram_bot_token") or "").strip()
    bot_token = cfg_token if not _is_placeholder(cfg_token) else ""
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    targets = recipient_chat_ids(config)
    if not bot_token or not targets:
        log.error(
            "Telegram not sent: missing bot token or no chat recipients. "
            "Set TELEGRAM_BOT_TOKEN. Recipients: data/telegram_subscribers.json (users send /start; run telegram-poll-debug) "
            "and/or telegram_chat_id / TELEGRAM_CHAT_ID."
        )
        return

    status = transaction.get("status")

    # --- Position opened: send TP/SL ---
    if status in ("OPEN_LONG", "OPEN_SHORT"):
        side = transaction.get("side", "LONG")
        price = transaction.get("price", 0)
        tp = transaction.get("tp_price", 0)
        sl = transaction.get("sl_price", 0)
        emoji = "📈" if side == "LONG" else "📉"
        msg = f"{emoji} *{side} opened*\nPrice: {price:,.2f}\nTP: {tp:,.2f}\nSL: {sl:,.2f}"
        _send_telegram(bot_token, msg, config)
        return

    # --- Position closed (TP or SL): send P&L with leverage/fees and stats ---
    if status == "CLOSED":
        side = transaction.get("side", "")
        exit_reason = transaction.get("exit_reason", "")
        profit = transaction.get("profit", 0)
        profit_pct = transaction.get("profit_percent", 0)
        win = transaction.get("win", False)
        entry_price = transaction.get("entry_price", 0)
        exit_price = transaction.get("exit_price", 0)
        leveraged_pnl_pct = transaction.get("leveraged_pnl_pct", profit_pct)
        fee_usd = transaction.get("fee_usd", 0)
        balance_after = transaction.get("balance_after", 0)
        starting_balance = transaction.get("starting_balance", 0)
        total_return_pct = transaction.get("total_return_pct", 0)

        res = "✅ TP" if win else "❌ SL"
        msg = f"🔒 *{side} closed ({res})*\nEntry: {entry_price:,.2f} → Exit: {exit_price:,.2f}\n"
        msg += f"Price P&L: {profit_pct:+.2f}% → Margin: {leveraged_pnl_pct:+.2f}% | Fee: ${fee_usd:.2f}\n"
        msg += f"Balance: ${balance_after:.2f} | Total return: {total_return_pct:+.1f}%"
        _send_telegram(bot_token, msg, config)

        # Stats: wins, losses, total P&L $, total return %
        try:
            tx_path = get_transaction_path()
            if tx_path.is_file():
                tdf = pd.read_csv(tx_path, header=None, names=["timestamp", "price", "profit", "status"])
                tdf["profit"] = pd.to_numeric(tdf["profit"], errors="coerce")
                tdf = tdf.dropna(subset=["profit"])
                recent = tdf.tail(500)  # last N closed trades
                wins = int((recent["profit"] > 0).sum())
                losses = int((recent["profit"] < 0).sum())
                total_pnl_usd = recent["profit"].sum()  # price P&L; for margin we use balance
                msg2 = "📊 *Session stats*\n"
                msg2 += f"Wins: {wins} | Losses: {losses}\n"
                msg2 += f"Balance: ${balance_after:.2f} (start ${starting_balance:.2f})\n"
                msg2 += f"Total return: {total_return_pct:+.1f}%"
                _send_telegram(bot_token, msg2, config)
        except Exception as e:
            log.debug("Stats for Telegram skipped: %s", e)
        return

    # Legacy: BUY/SELL without TP/SL (e.g. old config)
    if status == "SELL":
        message = "⚡💰 *SOLD: "
    elif status == "BUY":
        message = "⚡💰 *BOUGHT: "
    else:
        return

    try:
        profit, profit_percent, profit_descr, profit_percent_descr = await generate_transaction_stats()
        message += f" Profit: {profit_percent:.2f}% {profit:.2f}₮*"
        _send_telegram(bot_token, message, config)
        if status == "SELL":
            msg2 = "↗ *LONG stats (4w)*\n"
        else:
            msg2 = "↘ *SHORT stats (4w)*\n"
        msg2 += f"count={int(profit_percent_descr['count'])} mean={profit_percent_descr['mean']:.2f}% min={profit_percent_descr['min']:.2f}% max={profit_percent_descr['max']:.2f}%"
        _send_telegram(bot_token, msg2, config)
    except Exception as e:
        log.error("Error building legacy stats: %s", e)


async def generate_transaction_stats():
    """Here we assume that the latest transaction is saved in the file and this function computes various properties."""
    transaction_path = get_transaction_path()

    df = pd.read_csv(transaction_path, parse_dates=[0], header=None, names=["timestamp", "close", "profit", "status"], date_format="ISO8601")

    mask = (df['timestamp'] >= (datetime.now() - timedelta(weeks=4)))
    df = df[max(mask.idxmax()-1, 0):]  # We add one previous row to use the previous close

    df["prev_close"] = df["close"].shift()
    df["profit_percent"] = df.apply(lambda x: (100.0 * x["profit"] / x["prev_close"]) if x["prev_close"] else 0.0, axis=1)

    df = df.iloc[1:]  # Remove the first row which was added to compute relative profit

    long_df = df[df["status"] == "SELL"]
    short_df = df[df["status"] == "BUY"]

    #
    # Determine properties of the latest transaction
    #

    # Sample output:
    # BTC, LONG or SHORT
    # sell price 24,000 (now), buy price (datetime) 23,000
    # profit abs: 1,000.00,
    # profit rel: 3.21%

    last_transaction = df.iloc[-1]
    transaction_dt = last_transaction["timestamp"]
    transaction_type = last_transaction["status"]
    profit = last_transaction["profit"]
    profit_percent = last_transaction["profit_percent"]

    #
    # Properties of last period of trade
    #

    if transaction_type == "SELL":
        df2 = long_df
    elif transaction_type == "BUY":
        df2 = short_df

    # Sample output for abs profit
    # sum 1,200.00, mean 400.00, median 450.00, std 250.00, min -300.0, max 1200.00

    profit_sum = df2["profit"].sum()
    profit_descr = df2["profit"].describe()  # count, mean, std, min, 50% max

    profit_percent_sum = df2["profit_percent"].sum()
    profit_percent_descr = df2["profit_percent"].describe()  # count, mean, std, min, 50% max

    return profit, profit_percent, profit_descr, profit_percent_descr


def get_signal(df, buy_signal_column, sell_signal_column):
    """From the last row, produce and return an object with parameters important for trading."""
    freq = App.config["freq"]

    row = df.iloc[-1]  # Last row stores the latest values we need

    interval_length = pd.Timedelta(freq).to_pytimedelta()

    if not ptypes.is_datetime64_any_dtype(df.index):  # Alternatively df.index.inferred_type == "datetime64"
        raise ValueError(f"Index of the data frame must be of datetime type.")
    close_time = row.name + interval_length  # Add interval length because timestamp is start of the interval

    close_price = row["close"]

    buy_signal = row[buy_signal_column]
    sell_signal = row[sell_signal_column]

    if buy_signal and sell_signal:  # Both signals are true - should not happen
        signal_side = "BOTH"
    elif buy_signal:
        signal_side = "BUY"
    elif sell_signal:
        signal_side = "SELL"
    else:
        signal_side = ""

    signal = {"side": signal_side, "close_price": close_price, "close_time": close_time}

    return signal


def load_last_transaction():
    transaction_path = get_transaction_path()

    t_dict = dict(timestamp=str(datetime.now()), price=0.0, profit=0.0, status="")
    if transaction_path.is_file():
        with open(transaction_path, "r") as f:
            line = ""
            for line in f:
                pass
        if line:
            t_dict = dict(zip("timestamp,price,profit,status".split(","), line.strip().split(",")))
            t_dict["timestamp"] = pd.to_datetime(t_dict["timestamp"], utc=True)
            t_dict["price"] = float(t_dict["price"])
            t_dict["profit"] = float(t_dict["profit"])
            #t_dict = json.loads(line)
    else:  # Create file with header
        transaction_path.parent.mkdir(parents=True, exist_ok=True)
        with open(transaction_path, 'a+') as f:
            #f.write("timestamp,price,profit,status\n")
            f.write("2020-01-01 00:00:00,0.0,0.0,SELL\n")
    return t_dict


def load_all_transactions():
    transaction_path = get_transaction_path()
    df = pd.read_csv(transaction_path, names="timestamp,price,profit,status".split(","), header=None)
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='ISO8601', utc=True)
    df = df.astype({'timestamp': 'datetime64[ns, UTC]', 'price': 'float64', 'profit': 'float64', 'status': 'str'})
    return df


def get_transaction_path():
    return Path(App.config["data_folder"]) / App.config["symbol"] / "transactions.txt"


def get_position_path():
    return Path(App.config["data_folder"]) / App.config["symbol"] / "position.json"


def load_position():
    """Load open position state (entry, tp, sl). Returns None if no position."""
    path = get_position_path()
    if not path.is_file():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not data.get("open"):
            return None
        return data
    except Exception:
        return None


def save_position(data):
    path = get_position_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=0)


def clear_position():
    path = get_position_path()
    if path.is_file():
        path.unlink()


def get_last_close_path():
    return Path(App.config["data_folder"]) / App.config["symbol"] / "last_close.json"


def _save_last_close_time(close_time) -> None:
    """Record time of last position close for min_bars_between_trades cooldown."""
    path = get_last_close_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"last_close_time": str(close_time)}, f)


def _load_last_close_time():
    """Return last position close time string, or None."""
    path = get_last_close_path()
    if not path.is_file():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f).get("last_close_time")
    except Exception:
        return None


def get_balance_path():
    return Path(App.config["data_folder"]) / App.config["symbol"] / "balance.json"


def get_daily_state_path():
    return Path(App.config["data_folder"]) / App.config["symbol"] / "daily_state.json"


def load_daily_state():
    path = get_daily_state_path()
    if not path.is_file():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_daily_state(data: dict):
    path = get_daily_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=0)


def _is_drawdown_paused(model: dict) -> bool:
    """Return True if daily drawdown limit hit and we should not open new trades. Resets state for new day."""
    limit_pct = model.get("daily_drawdown_limit_pct")
    if limit_pct is None or float(limit_pct) <= 0:
        return False
    limit_pct = float(limit_pct)
    today = datetime.now().strftime("%Y-%m-%d")
    state = load_daily_state()
    balance_now, starting_balance = load_balance(model)
    if not state or state.get("date") != today:
        state = {"date": today, "daily_start_balance": balance_now, "daily_pnl_pct": 0.0, "paused": False}
        save_daily_state(state)
    if state.get("paused"):
        return True
    return False


def _update_daily_after_close(model: dict, balance_after: float):
    """Update daily state after a close; set paused if daily drawdown limit exceeded."""
    limit_pct = model.get("daily_drawdown_limit_pct")
    if limit_pct is None or float(limit_pct) <= 0:
        return
    limit_pct = float(limit_pct)
    state = load_daily_state()
    if not state:
        return
    daily_start = state.get("daily_start_balance") or balance_after
    state["daily_pnl_pct"] = 100.0 * (balance_after - daily_start) / daily_start if daily_start else 0.0
    if state["daily_pnl_pct"] <= -limit_pct:
        state["paused"] = True
        log.warning("Daily drawdown limit (%.1f%%) hit. Pausing new trades until next day.", limit_pct)
    save_daily_state(state)


def load_balance(model: dict):
    """Current margin balance. If no file, init from config starting_balance."""
    path = get_balance_path()
    start = float(model.get("starting_balance", 10.0))
    if not path.is_file():
        return start, start
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return float(data.get("balance", start)), float(data.get("starting_balance", start))
    except Exception:
        return start, start


def save_balance(balance: float, starting_balance: float):
    path = get_balance_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"balance": balance, "starting_balance": starting_balance}, f)


def reset_trade_state_on_startup(config: dict):
    """
    On server restart: clear position, reset balance to starting_balance, and clear
    transaction history (so session stats show 0 wins / 0 losses) when
    trader_simulation has reset_balance_on_restart true (default).
    """
    for out in config.get("output_sets", []):
        if out.get("generator") != "trader_simulation":
            continue
        model = out.get("config") or {}
        if model.get("reset_balance_on_restart", True) is False:
            continue
        clear_position()
        start = float(model.get("starting_balance", 10.0))
        save_balance(start, start)
        # Reset daily drawdown state so paused flag does not persist across restarts
        daily_path = get_daily_state_path()
        if daily_path.is_file():
            daily_path.unlink()
        # Clear transactions so session stats (wins/losses) start from zero
        tx_path = get_transaction_path()
        tx_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tx_path, "w") as f:
            f.write("2020-01-01 00:00:00+00:00,0.0,0.0,SELL\n")
        log.info("Trade state reset on restart: position cleared, balance=%s, transactions cleared", start)
        return
