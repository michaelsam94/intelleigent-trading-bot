import click
import requests

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from binance import Client

from common.types import Venue
from common.telegram_broadcast import broadcast_telegram_markdown, recipient_chat_ids
from common.generators import output_feature_set
from common.analyzer import Analyzer

from inputs import get_collector_functions

from outputs.notifier_trades import *
from outputs.notifier_scores import *
from outputs.notifier_diagram import *
from outputs import get_trader_functions

import logging
import sys

log = logging.getLogger('server')

_fmt = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(
    filename="server.log",
    level=logging.DEBUG,
    format=_fmt,
)
# Also log to stdout so PM2 logs and terminals show output
_root = logging.getLogger()
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter(_fmt))
_root.addHandler(_h)

# Get the collector functions based on the collector type

#
# Main procedure
#
async def main_task():
    """This task will be executed regularly according to the schedule"""

    #
    # 1. Execute input adapters to receive new data from data source(s)
    #
    try:
        res = await main_collector_task()  # Retrieve raw data, merge, convert to data frame and append
    except Exception as e:
        log.error(f"Error in main_collector_task function: {e}")
        return
    if res:
        log.error(f"Error in main_collector_task function: {res}")
        return res

    # TODO: Validation
    #last_kline_ts = App.analyzer.get_last_kline_ts(symbol)
    #if last_kline_ts + 60_000 != startTime:
    #    log.error(f"Problem during analysis. Last kline end ts {last_kline_ts + 60_000} not equal to start of current interval {startTime}.")

    #
    # 2. Apply transformations and generate derived columns for the appended data
    #
    try:
        analyze_task = await App.loop.run_in_executor(None, App.analyzer.analyze)
    except Exception as e:
        log.error(f"Error in analyze function: {e}")
        return

    #
    # 3. Execute output adapter which send the results of analysis to consumers
    #
    output_sets = App.config.get("output_sets", [])
    for os in output_sets:
        try:
            await output_feature_set(App.analyzer.df, os, App.config, App.model_store)
        except Exception as e:
            log.error(f"Error in output function: {e}")
            return

    return

async def main_collector_task():
    """
    Retrieve raw data from venue-specific data sources and append to the main data frame
    """
    venue = App.config.get("venue")
    venue = Venue(venue)
    fetch_klines_fn, health_check_fn = get_collector_functions(venue)

    symbol = App.config["symbol"]
    freq = App.config["freq"]
    start_ts, end_ts = pandas_get_interval(freq)
    now_ts = now_timestamp()

    log.info(f"===> Start collector task. Timestamp {now_ts}. Interval [{start_ts},{end_ts}].")

    #
    # 1. Check server state (if necessary)
    #
    if data_provider_problems_exist():
        await health_check_fn()
        if data_provider_problems_exist():
            log.error(f"Problems with the data provider server found. No signaling, no trade. Will try next time.")
            return 1

    #
    # 2. Get how much data is missing and request it
    #
    # Ask analyzer what is the timestamp of its last available row
    last_kline_dt = App.analyzer.get_last_kline_dt()

    # Request data starting from this time (with certain overlap)
    dfs = await fetch_klines_fn(App.config, last_kline_dt)
    if dfs is None:
        log.error(f"Problem getting data from the server. Will try next time.")
        return 1

    #
    # 3. Append data to the analyzer for further processing (my also creating a common index and merging)
    #
    try:
        App.analyzer.append_data(dfs)
    except Exception as e:
        log.error(f"Error appending data to the analyzer. Exception: {e}")
        return 1

    log.info(f"<=== End collector task.")
    return 0


async def process_ws_kline(dfs: dict):
    """On WebSocket kline close: append row, analyze, run outputs (Telegram etc.)."""
    symbol = App.config.get("symbol", "")
    try:
        App.analyzer.append_data(dfs)
    except Exception as e:
        log.error("Error appending WebSocket kline: %s", e)
        return
    try:
        await App.loop.run_in_executor(None, App.analyzer.analyze)
    except Exception as e:
        import traceback
        log.error("Error in analyze (WebSocket): %s", e)
        log.error(traceback.format_exc())
        return
    output_sets = App.config.get("output_sets", [])
    for os in output_sets:
        try:
            await output_feature_set(App.analyzer.df, os, App.config, App.model_store)
        except Exception as e:
            log.error("Error in output function (WebSocket): %s", e)
    # So PM2 logs show each realtime tick
    last_ts = App.analyzer.df.index[-1] if len(App.analyzer.df) else None
    log.info("Realtime %s kline processed → analyze + outputs done. Last row: %s", symbol, last_ts)


def _send_telegram_startup_message(symbol: str, freq: str):
    """Send a one-line confirmation to all Telegram recipients on each server restart."""
    import os
    token = (App.config.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or "<" in token or "your-" in token.lower():
        log.info("Telegram startup message skipped: token missing/placeholder.")
        return
    chats = recipient_chat_ids(App.config)
    if not chats:
        log.info(
            "Telegram startup message skipped: no recipients (subscribers file empty and no telegram_chat_id / TELEGRAM_CHAT_ID)."
        )
        return
    text = f"✅ ITB server started. {symbol} @ {freq} — Telegram connected."
    try:
        n = broadcast_telegram_markdown(token, text, App.config)
        if n:
            log.info("Telegram startup message sent to %s chat(s).", n)
            print(f"Telegram startup message sent to {n} chat(s).", flush=True)
        else:
            log.warning("Telegram startup message failed for all chats.")
            print("Telegram startup message failed for all chats.", flush=True)
    except Exception as e:
        log.warning("Telegram startup message error: %s", e)
        print(f"Telegram startup message error: {e}", flush=True)


@click.command()
@click.option('--config_file', '-c', type=click.Path(), default='', help='Configuration file name')
def start_server(config_file):

    load_config(config_file)

    App.config["train"] = False  # Server does not train - it only predicts therefore explicitly disable train mode

    # Log Telegram config status (to server.log and stdout so it appears in pm2 logs)
    t_token = (App.config.get("telegram_bot_token") or "").strip()
    t_chat = str(App.config.get("telegram_chat_id") or "").strip().replace("\n", "").replace("\r", "")
    n_sub = len(recipient_chat_ids(App.config))
    if t_token and "<" not in t_token and "your-" not in t_token.lower():
        legacy_preview = (t_chat[:12] + "...") if len(t_chat) > 12 else (t_chat or "(empty)")
        msg = (
            f"Telegram: token set (len={len(t_token)}), {n_sub} recipient(s) "
            f"(subscribers file + config/env chat_id). Config telegram_chat_id preview={legacy_preview}"
        )
        log.info(msg)
        print(msg, flush=True)
    else:
        msg = (
            "Telegram: token missing/placeholder. Notifications need TELEGRAM_BOT_TOKEN plus recipients "
            "(/start → data/telegram_subscribers.json and/or TELEGRAM_CHAT_ID / telegram_chat_id)."
        )
        log.warning(msg)
        print(msg, flush=True)

    symbol = App.config["symbol"]
    freq = App.config["freq"]
    venue = App.config.get("venue")
    try:
        if venue is not None:
            venue = Venue(venue)
    except ValueError as e:
        log.error(f"Invalid venue specified in config: {venue}. Error: {e}. Currently these values are supported: {[e.value for e in Venue]}")
        return
    
    fetch_klines_fn, health_check_fn = get_collector_functions(venue)
    trader_funcs = get_trader_functions(venue)
    
    log.info(f"Initializing server. Venue: {venue.value}. Trade pair: {symbol}. Frequency: {freq}")
    
    #getcontext().prec = 8

    #
    # Validation
    #

    #
    # Connect to the server and update/initialize the system state
    #
    if venue == Venue.BINANCE:
        # Prepare binance-specific parameters
        client_params = {}
        if App.config.get("append_overlap_records"):
            client_params["append_overlap_records"] = App.config["append_overlap_records"]
        if App.config.get("binance_futures") is not None:
            client_params["binance_futures"] = App.config["binance_futures"]
        # Prepare binance-specific client arguments
        client_args = dict(
            api_key = App.config.get("api_key"),
            api_secret = App.config.get("api_secret")
        )
        client_args = client_args | App.config.get("client_args", {})
        # Initialize client
        from inputs.collector_binance import init_client
        init_client(client_params, client_args)

    if venue == Venue.MT5:
        # Prepare mt5-specific parameters
        client_params = {}
        # Prepare mt5-specific client arguments
        client_args = dict(
            mt5_account_id=int(App.config.get("mt5_account_id")),
            mt5_password=str(App.config.get("mt5_password")),
            mt5_server=str(App.config.get("mt5_server"))
        )
        client_args = client_args | App.config.get("client_args", {})
        # Initialize client
        from inputs.collector_mt5 import init_client
        init_client(client_params, client_args)

    App.model_store = ModelStore(App.config)
    App.model_store.load_models()
    App.analyzer = Analyzer(App.config, App.model_store)

    # Load latest transaction and (simulated) trade state
    App.transaction = load_last_transaction()
    reset_trade_state_on_startup(App.config)

    #App.loop = asyncio.get_event_loop()  # In Python 3.12: DeprecationWarning: There is no current event loop
    App.loop = asyncio.new_event_loop()

    # Cold start: load initial data, do complete analysis
    try:
        App.loop.run_until_complete(main_collector_task())
        # The very first call (cold start) may take some time because of big initial size and hence we make the second call to get the (possible) newest klines
        App.loop.run_until_complete(main_collector_task())

        # Analyze all received data (not only last few rows) so that we have full history
        App.analyzer.analyze()
    except Exception as e:
        log.error(f"Problems during initial data collection. {e}")

    if data_provider_problems_exist():
        log.error(f"Problems during initial data collection.")
        return

    log.info(f"Finished initial data collection.")

    # TODO: Only for binance output and if it has been defined
    # Initialize trade status (account, balances, orders etc.) in case we are going to really execute orders
    if App.config.get("trade_model", {}).get("trader_binance"):
        try:
            App.loop.run_until_complete(trader_funcs['update_trade_status']())
        except Exception as e:
            log.error(f"Problems trade status sync. {e}")

        if data_provider_problems_exist():
            log.error(f"Problems trade status sync.")
            return

        log.info(f"Finished trade status sync (account, balances etc.)")
        log.info(f"Balance: {App.config['base_asset']} = {str(App.account_info.base_quantity)}")
        log.info(f"Balance: {App.config['quote_asset']} = {str(App.account_info.quote_quantity)}")

    #
    # Realtime (WebSocket) or scheduled (cron)
    #

    use_websocket = venue == Venue.BINANCE and App.config.get("use_websocket", False)

    if use_websocket:
        log.info("Realtime mode: Binance WebSocket kline stream (no fixed schedule).")
        print("Realtime mode: Binance WebSocket kline stream.", flush=True)
        from inputs.collector_binance_ws import run_klines_websocket
        use_futures = App.config.get("binance_futures", False)
        def _start_ws():
            App.ws_task = App.loop.create_task(run_klines_websocket(symbol, freq, process_ws_kline, futures=use_futures))
        App.loop.call_soon(_start_ws)
        App.sched = None
    else:
        App.sched = AsyncIOScheduler()
        logging.getLogger('apscheduler').setLevel(logging.WARNING)
        trigger = freq_to_CronTrigger(freq)
        App.sched.add_job(main_task, trigger=trigger, id='main_task')
        App.sched._eventloop = App.loop
        App.sched.start()
        log.info(f"Scheduler started (fixed {freq}).")

    # Send a one-time confirmation to Telegram on each restart
    _send_telegram_startup_message(symbol, freq)

    #
    # Start event loop and scheduler
    #
    try:
        App.loop.run_forever()  # Blocking. Run until stop() is called
    except KeyboardInterrupt:
        log.info(f"KeyboardInterrupt.")
    finally:
        log.info("Shutting down...")
        # Graceful shutdown
        if App.sched is not None and App.sched.running:
             App.sched.shutdown()
             log.info(f"Scheduler shutdown.")
        # Stop the loop if it's still running (e.g., if shutdown initiated by signal other than KeyboardInterrupt)
        if App.loop.is_running():
             App.loop.stop()
             log.info("Event loop stop requested.")
        # Cancel WebSocket first so it closes cleanly and avoids "Task was destroyed but it is pending"
        if getattr(App, "ws_task", None) and not App.ws_task.done():
            App.ws_task.cancel()
            try:
                App.loop.run_until_complete(asyncio.wait_for(asyncio.shield(App.ws_task), timeout=3.0))
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        pending = [t for t in asyncio.all_tasks(App.loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            try:
                App.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        App.loop.close()
        log.info(f"Event loop closed.")
        if venue == Venue.BINANCE:
            from inputs.collector_binance import close_client
            close_client()
        if venue == Venue.MT5:
            from inputs.collector_mt5 import close_client
            close_client()
        log.info("Connection closed.")

    return 0


if __name__ == "__main__":
    start_server()
