import os
import sys
import json
from datetime import timedelta, datetime
from pathlib import Path

import asyncio

import pandas as pd
import pandas.api.types as ptypes

import requests

from service.App import *
from common.utils import *
from common.model_store import *

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

    # --- 1) We have an open position: check TP/SL on this bar (high/low) ---
    if position and position.get("open"):
        side = position.get("side")  # "LONG" or "SHORT"
        entry = position.get("entry_price")
        tp_price = position.get("tp_price")
        sl_price = position.get("sl_price")
        entry_time = position.get("entry_time")

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

            # Append close to transaction log (same format as before for stats)
            transaction_path.parent.mkdir(parents=True, exist_ok=True)
            t_line = f"{close_time},{exit_price:.2f},{profit:.2f},{status_close}\n"
            with open(transaction_path, "a") as f:
                f.write(t_line)

            clear_position()
            App.transaction = dict(
                timestamp=str(close_time), price=exit_price, profit=profit, status=status_close
            )

            log.info(f"Position closed: {side} @ {entry} -> {exit_price} ({'TP' if hit_tp else 'SL'}) PnL: {profit:.2f} ({profit_pct:.2f}%)")

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
            }
        # Position still open: do not send a new signal
        return None

    # --- 2) No position: open on BUY/SELL signal with TP/SL ---
    if signal_side not in ("BUY", "SELL"):
        return None

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


def _send_telegram(bot_token, chat_id, text):
    try:
        import urllib.parse
        url = "https://api.telegram.org/bot" + bot_token + "/sendMessage?chat_id=" + str(chat_id).strip() + "&parse_mode=markdown&text=" + urllib.parse.quote(text)
        r = requests.get(url, timeout=10)
        if not r.json().get("ok"):
            log.error("Telegram send failed: %s", r.text)
    except Exception as e:
        log.error("Error sending Telegram: %s", e)


async def send_transaction_message(transaction, config):
    bot_token = (config.get("telegram_bot_token") or "").strip()
    chat_id = str(config.get("telegram_chat_id") or "").strip().replace("\n", "").replace("\r", "")
    if not bot_token or not chat_id:
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
        _send_telegram(bot_token, chat_id, msg)
        return

    # --- Position closed (TP or SL): send P&L and stats ---
    if status == "CLOSED":
        side = transaction.get("side", "")
        exit_reason = transaction.get("exit_reason", "")
        profit = transaction.get("profit", 0)
        profit_pct = transaction.get("profit_percent", 0)
        win = transaction.get("win", False)
        entry_price = transaction.get("entry_price", 0)
        exit_price = transaction.get("exit_price", 0)

        res = "✅ TP" if win else "❌ SL"
        msg = f"🔒 *{side} closed ({res})*\nEntry: {entry_price:,.2f} → Exit: {exit_price:,.2f}\nP&L: {profit:+,.2f} ({profit_pct:+.2f}%)"
        _send_telegram(bot_token, chat_id, msg)

        # Stats from transaction file (4 weeks): wins, losses, total P&L
        try:
            _, _, profit_descr, profit_percent_descr = await generate_transaction_stats()
            n = int(profit_percent_descr.get("count", 0))
            if n > 0:
                # Win/loss counts from file (same type as this close: SELL=long, BUY=short)
                tx_path = get_transaction_path()
                tdf = pd.read_csv(tx_path, header=None, names=["timestamp", "price", "profit", "status"])
                tdf["profit"] = pd.to_numeric(tdf["profit"], errors="coerce")
                tdf = tdf.dropna(subset=["profit"])
                recent = tdf.tail(4 * 7 * 24 * 2)  # rough 4 weeks of 1m bars, 2 trades/day max
                same_side = recent[recent["status"] == ("SELL" if side == "LONG" else "BUY")]
                wins = int((same_side["profit"] > 0).sum())
                losses = int((same_side["profit"] < 0).sum())
                total_pnl = same_side["profit"].sum()
                msg2 = "📊 *Stats (4w)*\n"
                msg2 += f"Wins: {wins} | Losses: {losses} | Total P&L: {total_pnl:+,.2f}\n"
                msg2 += f"Mean: {profit_percent_descr.get('mean', 0):.2f}% | Min: {profit_percent_descr.get('min', 0):.2f}% | Max: {profit_percent_descr.get('max', 0):.2f}%"
                _send_telegram(bot_token, chat_id, msg2)
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
        _send_telegram(bot_token, chat_id, message)
        if status == "SELL":
            msg2 = "↗ *LONG stats (4w)*\n"
        else:
            msg2 = "↘ *SHORT stats (4w)*\n"
        msg2 += f"count={int(profit_percent_descr['count'])} mean={profit_percent_descr['mean']:.2f}% min={profit_percent_descr['min']:.2f}% max={profit_percent_descr['max']:.2f}%"
        _send_telegram(bot_token, chat_id, msg2)
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
