import numpy as np
import pandas as pd

"""
Backtesting and trade performance using trade simulation.
Optional fee_bps_per_side and leverage align with realtime trader_simulation (margin P&L after fees).
"""


def _margin_pct_after_fees(price_return_pct, leverage, fee_bps_per_side):
    """Net margin return %: price_return * leverage minus round-trip fee as % of margin."""
    leveraged_pct = price_return_pct * leverage
    fee_margin_pct = 2.0 * (fee_bps_per_side / 10000.0) * leverage * 100.0
    return leveraged_pct - fee_margin_pct


def simulated_trade_performance(
    df,
    buy_signal_column,
    sell_signal_column,
    price_column,
    fee_bps_per_side=0,
    leverage=1,
    starting_balance=None,
    direction=None,
):
    """
    Simulates trades over time from buy/sell signals and price.
    Optional fee_bps_per_side and leverage match realtime trader_simulation:
    per-trade margin return = price_return_pct * leverage - 2 * (fee_bps/1e4) * leverage * 100.
    If starting_balance is set, tracks balance after each trade and adds balance_after, total_return_pct.
    direction: "long" | "short" | None. When set and starting_balance is set, only that side updates balance.
    """
    is_buy_mode = True
    fee_bps = float(fee_bps_per_side)
    lev = float(leverage)
    use_fees = fee_bps > 0 or lev != 1.0
    balance = float(starting_balance) if starting_balance is not None else None
    balance_history = [balance] if balance is not None else None

    long_profit = 0
    long_profit_percent = 0
    long_net_margin_pct = 0.0
    long_transactions = 0
    long_profitable = 0
    longs = list()

    short_profit = 0
    short_profit_percent = 0
    short_net_margin_pct = 0.0
    short_transactions = 0
    short_profitable = 0
    shorts = list()

    df = df[[sell_signal_column, buy_signal_column, price_column]]
    for (index, sell_signal, buy_signal, price) in df.itertuples(name=None):
        if not price or pd.isnull(price):
            continue
        if is_buy_mode:
            if buy_signal:
                previous_price = shorts[-1][2] if len(shorts) > 0 else 0.0
                profit = (previous_price - price) if previous_price > 0 else 0.0
                profit_percent = 100.0 * profit / previous_price if previous_price > 0 else 0.0
                net_margin = _margin_pct_after_fees(profit_percent, lev, fee_bps) if use_fees else profit_percent
                short_profit += profit
                short_profit_percent += profit_percent
                short_net_margin_pct += net_margin
                short_transactions += 1
                if (net_margin > 0) if use_fees else (profit > 0):
                    short_profitable += 1
                if balance is not None and direction != "long":
                    balance = max(0.01, balance * (1.0 + net_margin / 100.0))
                    balance_history.append(balance)
                shorts.append((index, previous_price, price, profit, profit_percent))
                is_buy_mode = False
        else:
            if sell_signal:
                previous_price = longs[-1][2] if len(longs) > 0 else 0.0
                profit = (price - previous_price) if previous_price > 0 else 0.0
                profit_percent = 100.0 * profit / previous_price if previous_price > 0 else 0.0
                net_margin = _margin_pct_after_fees(profit_percent, lev, fee_bps) if use_fees else profit_percent
                long_profit += profit
                long_profit_percent += profit_percent
                long_net_margin_pct += net_margin
                long_transactions += 1
                if (net_margin > 0) if use_fees else (profit > 0):
                    long_profitable += 1
                if balance is not None and direction != "short":
                    balance = max(0.01, balance * (1.0 + net_margin / 100.0))
                    balance_history.append(balance)
                longs.append((index, previous_price, price, profit, profit_percent))
                is_buy_mode = True

    # Report %profit as net margin % when using fees/leverage so it matches realtime
    long_pct = round(long_net_margin_pct, 1) if use_fees else round(long_profit_percent, 1)
    short_pct = round(short_net_margin_pct, 1) if use_fees else round(short_profit_percent, 1)

    long_performance = {
        "#transactions": long_transactions,
        "profit": round(long_profit, 2),
        "%profit": long_pct,
        "#profitable": long_profitable,
        "%profitable": round(100.0 * long_profitable / long_transactions, 1) if long_transactions else 0.0,
        "profit/T": round(long_profit / long_transactions, 2) if long_transactions else 0.0,
        "%profit/T": round(long_net_margin_pct / long_transactions, 1) if use_fees and long_transactions else (round(long_profit_percent / long_transactions, 1) if long_transactions else 0.0),
    }
    short_performance = {
        "#transactions": short_transactions,
        "profit": round(short_profit, 2),
        "%profit": short_pct,
        "#profitable": short_profitable,
        "%profitable": round(100.0 * short_profitable / short_transactions, 1) if short_transactions else 0.0,
        "profit/T": round(short_profit / short_transactions, 2) if short_transactions else 0.0,
        "%profit/T": round(short_net_margin_pct / short_transactions, 1) if use_fees and short_transactions else (round(short_profit_percent / short_transactions, 1) if short_transactions else 0.0),
    }

    total_net_margin_pct = long_net_margin_pct + short_net_margin_pct
    profit_percent = total_net_margin_pct if use_fees else (long_profit_percent + short_profit_percent)
    profit = long_profit + short_profit
    transaction_no = long_transactions + short_transactions
    profitable = (long_profitable + short_profitable) / transaction_no if transaction_no else 0.0

    performance = {
        "#transactions": transaction_no,
        "profit": round(profit, 2),
        "%profit": round(profit_percent, 1),
        "profitable": profitable,
        "profitable_percent": round(100.0 * profitable / transaction_no, 1) if transaction_no else 0.0,
        "profit/T": round(profit / transaction_no, 2) if transaction_no else 0.0,
        "%profit/T": round(profit_percent / transaction_no, 1) if transaction_no else 0.0,
    }
    if balance is not None and starting_balance and starting_balance > 0:
        balance_after = round(balance, 2)
        total_return_pct = round(100.0 * (balance - starting_balance) / starting_balance, 1)
        # Max drawdown: largest peak-to-trough drop in balance
        if balance_history and len(balance_history) > 1:
            peak = balance_history[0]
            max_dd = 0.0
            max_dd_pct = 0.0
            for b in balance_history:
                if b > peak:
                    peak = b
                dd = peak - b
                dd_pct = 100.0 * dd / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
            max_drawdown = round(max_dd, 2)
            max_drawdown_pct = round(max_dd_pct, 1)
        else:
            max_drawdown = 0.0
            max_drawdown_pct = 0.0
        performance["balance_after"] = balance_after
        performance["total_return_pct"] = total_return_pct
        performance["max_drawdown"] = max_drawdown
        performance["max_drawdown_pct"] = max_drawdown_pct
        # When only one side updates balance, attach to that side's dict for simulate direction=long/short
        if direction == "long":
            long_performance["balance_after"] = balance_after
            long_performance["total_return_pct"] = total_return_pct
            long_performance["max_drawdown"] = max_drawdown
            long_performance["max_drawdown_pct"] = max_drawdown_pct
        elif direction == "short":
            short_performance["balance_after"] = balance_after
            short_performance["total_return_pct"] = total_return_pct
            short_performance["max_drawdown"] = max_drawdown
            short_performance["max_drawdown_pct"] = max_drawdown_pct

    return performance, long_performance, short_performance
