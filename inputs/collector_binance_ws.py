"""
Binance WebSocket kline stream for real-time updates.
Subscribes to symbol@kline_<interval>; on each kline close appends one row and runs the analysis pipeline.
"""
import asyncio
import json
import logging
from typing import Callable, Awaitable

from inputs.collector_binance import klines_to_df
from inputs.utils_binance import binance_freq_from_pandas

log = logging.getLogger("binance.ws")


def _kline_event_to_row(event: dict) -> list:
    """Convert Binance WebSocket kline event to one kline row (same format as REST API)."""
    k = event["k"]
    # REST order: timestamp, open, high, low, close, volume, close_time, quote_av, trades, tb_base_av, tb_quote_av, ignore
    t = int(k["t"])
    T = int(k["T"])
    o = float(k["o"])
    h = float(k["h"])
    l_ = float(k["l"])
    c = float(k["c"])
    v = float(k["v"])
    q = float(k.get("q", 0))
    n = int(k.get("n", 0))
    V = float(k.get("V", 0))
    Q = float(k.get("Q", 0))
    B = float(k.get("B", 0))
    return [t, o, h, l_, c, v, T, q, n, V, Q, B]


async def run_klines_websocket(
    symbol: str,
    pandas_freq: str,
    on_kline_close: Callable[[dict], Awaitable[None]],
    futures: bool = False,
) -> None:
    """
    Connect to Binance kline WebSocket and call on_kline_close(symbol -> df) when a kline closes.
    Runs until the connection is closed or cancelled.
    futures: if True, use USDT-M/USDC-M futures stream (fstream.binance.com).
    """
    stream_symbol = symbol.lower()
    binance_interval = binance_freq_from_pandas(pandas_freq)
    if futures:
        url = f"wss://fstream.binance.com/ws/{stream_symbol}@kline_{binance_interval}"
    else:
        url = f"wss://stream.binance.com:9443/ws/{stream_symbol}@kline_{binance_interval}"

    try:
        import websockets
    except ImportError:
        raise RuntimeError("Install websockets: pip install websockets")

    log.info("WebSocket connecting to %s (realtime klines, %s)", url, "futures" if futures else "spot")

    async for ws in websockets.connect(
        url,
        ping_interval=20,
        ping_timeout=10,
        close_timeout=5,
    ):
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("e") != "kline":
                    continue
                k = msg.get("k")
                if not k or not k.get("x"):
                    continue
                # Kline closed: build one-row df and notify
                row = _kline_event_to_row(msg)
                df = klines_to_df([row])
                dfs = {symbol: df}
                log.info("WebSocket kline close: %s %s", symbol, df.index[0])
                await on_kline_close(dfs)
        except asyncio.CancelledError:
            log.info("WebSocket kline task cancelled")
            break
        except Exception as e:
            log.warning("WebSocket error: %s. Reconnecting in 5s.", e)
            await asyncio.sleep(5)
