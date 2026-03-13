import os
import sys
from datetime import timedelta, datetime

import asyncio
import requests

import pandas as pd
import pandas.api.types as ptypes

from service.App import *
from common.utils import *
from common.model_store import *

import logging
log = logging.getLogger('notifier')


def _is_placeholder(val):
    """Treat config placeholders as missing."""
    if not val or not isinstance(val, str):
        return True
    s = val.strip()
    return "<" in s or ">" in s or s.lower().startswith("your-") or s == ""


async def send_score_notification(df, model: dict, config: dict, model_store: ModelStore):
    symbol = config["symbol"]
    freq = config["freq"]
    time_column = config["time_column"]

    score_column_names = model.get("score_column_names")
    if not score_column_names:
        log.error(f"Empty list of score columns in score notifier. At least one column name with a score has to be provided in config. Ignore")
        return

    row = df.iloc[-1]  # Last row stores the latest values we need

    interval_length = pd.Timedelta(freq).to_pytimedelta()

    if ptypes.is_datetime64_any_dtype(df.index):  # Alternatively df.index.inferred_type == "datetime64"
        close_time = row.name
    elif time_column in df.columns and ptypes.is_datetime64_any_dtype(df[time_column]):
        close_time = row[time_column]
    else:
        raise ValueError(f"Neither index nor time columns '{time_column}' are of datetime type")
    close_time += interval_length  # Add interval length because timestamp is start of the interval

    close_price = row["close"]
    trade_scores = [row[col] for col in score_column_names]
    trade_score_primary = trade_scores[0]
    trade_score_secondary = trade_scores[1] if len(trade_scores) > 1 else None

    #
    # Determine the band for the current score
    #
    band_no, band = _find_score_band(trade_score_primary, model)

    #
    # To message or not to message depending on score value and time
    #

    # Determine if the band was changed since the last time. Essentially, this means absolute signal strength increased
    # We store the previous band no as the model attribute
    prev_band_no = model.get("prev_band_no")
    if prev_band_no is not None:
        band_up = abs(band_no) > abs(prev_band_no)  # Examples: 0 -> 1, 1 -> 2, -1 -> 2
        band_dn = abs(band_no) < abs(prev_band_no)  # Examples: -2 -> 0, 2 -> -1, -2 -> -1
    else:
        band_up = True
        band_dn = True
    model["prev_band_no"] = band_no  # Store for the next time as an additional run-time attribute

    if band and band.get("frequency"):
        new_to_time_interval = close_time.minute % band.get("frequency") == 0
    else:
        new_to_time_interval = False

    # Send only if one of these conditions is true or entered new time interval (current time)
    notify_every_run = model.get("notify_every_run", False)
    notification_is_needed = (
        notify_every_run
        or (model.get("notify_band_up") and band_up)
        or (model.get("notify_band_dn") and band_dn)
        or new_to_time_interval
    )

    if not notification_is_needed:
        log.debug("Telegram skipped: no notification needed this run (band_no=%s, notify_every_run=%s)", band_no, model.get("notify_every_run"))
        return

    log.info("Telegram notifier: preparing message (score=%.3f, band_no=%s)", trade_score_primary, band_no)

    #
    # Build a message with parameters from the current band
    #

    # Crypto Currency Symbols: https://github.com/yonilevy/crypto-currency-symbols
    if symbol == "BTCUSDT":
        symbol_char = "₿"
    elif symbol == "ETHUSDT":
        symbol_char = "Ξ"
    else:
        symbol_char = symbol

    if band_up:
        band_change_char = "↑"
    elif band_dn:
        band_change_char = "↓"
    else:
        band_change_char = ""

    primary_score_str = f"{trade_score_primary:+.2f} {band_change_char} "
    secondary_score_str = f"{trade_score_secondary:+.2f}" if trade_score_secondary is not None else ''

    if band:
        message = f"{band.get('sign', '')} {symbol_char} {int(close_price):,} Indicator: {primary_score_str} {secondary_score_str} {band.get('text', '')} {freq}"
        if band.get("bold"):
            message = "*" + message + "*"
    else:
        # Default message if the score in the neutral (very weak) zone which is not covered by the config bands
        message = f"{symbol_char} {int(close_price):,} Indicator: {primary_score_str} {secondary_score_str} {freq}"

    message = message.replace("+", "%2B")  # For Telegram to display plus sign

    #
    # Send notification
    #
    bot_token = (config.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (config.get("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID") or "")
    chat_id = str(chat_id).strip()

    if _is_placeholder(bot_token) or _is_placeholder(chat_id):
        log.error(
            "Telegram not sent: telegram_bot_token or telegram_chat_id is missing or still a placeholder. "
            "Set real values in config or export TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before starting the server (e.g. before pm2 start)."
        )
        return

    try:
        url = 'https://api.telegram.org/bot' + bot_token + '/sendMessage?chat_id=' + chat_id + '&parse_mode=markdown&text=' + message
        response = requests.get(url)
        response_json = response.json()
        if not response_json.get('ok'):
            log.error(
                "Telegram API error: %s (full response: %s)",
                response_json.get("description", "unknown"),
                response_json,
            )
        else:
            log.info("Telegram notification sent to chat_id=%s", chat_id[:8] + "..." if len(chat_id) > 8 else chat_id)
    except Exception as e:
        log.error("Error sending Telegram notification: %s", e)


def _find_score_band(score_value, model):
    """
    Find band number and the band object given two lists with thresholds.

    The first list specifies lower bounds for the score and the function returns the first largest band which is less
    than or equal to the score. Band number is positive: 1, 2,...

    The second list specifies upper bounds for the score and the function returns the first smallest band which greater
    than the score. Band number is negative: -1, -2,---

    If the score does not fit into any band, then band number is 0 and None for the band object are returned.
    """

    # First, check if the score falls within some positive thresholds (with greater than condition)
    bands = model.get("positive_bands", [])
    bands = sorted(bands, key=lambda x: x.get("edge"), reverse=True)  # Large thresholds first
    # Find first entry with the edge equal or less than the score
    band_no, band = next(((i, x) for i, x in enumerate(bands) if score_value >= x.get("edge")), (len(bands), None))
    band_no = len(bands) - band_no
    if not band:  # Score is too small - smaller than all thresholds
        bands = model.get("negative_bands", [])
        bands = sorted(bands, key=lambda x: x.get("edge"), reverse=False)  # Small thresholds first
        band_no, band = next(((i, x) for i, x in enumerate(bands) if score_value < x.get("edge")), (len(bands), None))
        band_no = -(len(bands) - band_no)

    return band_no, band
