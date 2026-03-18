from pathlib import Path
from datetime import timedelta
import click
from tqdm import tqdm

import numpy as np
import pandas as pd

from sklearn.metrics import (precision_recall_curve, PrecisionRecallDisplay, RocCurveDisplay)
from sklearn.model_selection import ParameterGrid

from service.App import *
from common.utils import *
from common.model_store import *
from common.backtesting import *
from common.generators import generate_feature_set

"""
The script is intended for finding best trade parameters for a certain trade algorithm
by executing trade simulation (backtesting) for all specified parameters.
It performs exhaustive search in the space of all specified parameters by computing 
trade performance and then choosing the parameters with the highest profit (or maybe
using other selection criteria like stability of the results or minimum allowed losses etc.)

Notes:
- The optimization is based on certain trade algorithm. This means that a trade algorithm
is a parameter for this script. Different trade algorithms have different trade logics and 
also have different parameters. Currently, the script works with a very simple threshold-based
trade algorithm: if some score is higher than the threshold (parameter) then buy, if it is lower
than another threshold then sell. There is also a version with two thresholds for two scores.
- The script consumes the results of signal script but it then varies parameters of one entry
responsible for generation of trade signals. It then measures performance.
"""


def _prompt_float(prompt_text, default=None):
    """Prompt for a float; use default if user presses Enter."""
    while True:
        s = input(prompt_text).strip()
        if not s and default is not None:
            return float(default)
        if not s:
            continue
        try:
            return float(s)
        except ValueError:
            print("  Enter a number.")

def _prompt_int(prompt_text, default=None):
    """Prompt for an int; use default if user presses Enter."""
    while True:
        s = input(prompt_text).strip()
        if not s and default is not None:
            return int(default)
        if not s:
            continue
        try:
            return int(s)
        except ValueError:
            print("  Enter a whole number.")


@click.command()
@click.option('--config_file', '-c', type=click.Path(), default='', help='Configuration file name')
@click.option('--days', '-d', type=int, default=None, help='Use only the last N days of data for backtest (overrides simulate_model.data_days)')
@click.option('--interactive', '-i', is_flag=True, help='Prompt for initial investment, leverage, and backtest days')
@click.option('--apply-best', is_flag=True, help='After simulation, update config with best thresholds (best gain %)')
def main(config_file, days, interactive, apply_best):
    load_config(config_file)
    config = App.config

    App.model_store = ModelStore(config)
    App.model_store.load_models()

    time_column = config["time_column"]

    now = datetime.now()

    symbol = config["symbol"]
    data_path = Path(config["data_folder"]) / symbol

    # Resolve defaults for interactive prompts (from simulate_model or trader_simulation)
    sim_cfg = config.get("simulate_model", {})
    _days_default = days if days is not None else sim_cfg.get("data_days")
    _lev = sim_cfg.get("leverage") or next(
        (o["config"].get("leverage") for o in config.get("output_sets", []) if o.get("generator") == "trader_simulation" and isinstance(o.get("config"), dict)),
        None
    )
    _bal = sim_cfg.get("starting_balance") or next(
        (o["config"].get("starting_balance") for o in config.get("output_sets", []) if o.get("generator") == "trader_simulation" and isinstance(o.get("config"), dict)),
        None
    )

    if interactive:
        print("\n--- Backtest parameters ---")
        _sb = _prompt_float("Initial investment (USD): ", _bal)
        _lev_p = _prompt_float("Leverage: ", _lev)
        days = _prompt_int("Number of backtest days: ", _days_default if _days_default is not None else 14)
        config["_simulate_interactive"] = {"starting_balance": _sb, "leverage": _lev_p, "data_days": days}
        print(f"  Using: balance=${_sb}, leverage={_lev_p}, days={days}\n")

    #
    # Load data with (rolling) label point-wise predictions and signals generated
    #
    file_path = data_path / config.get("signal_file_name")
    if not file_path.exists():
        print(f"ERROR: Input file does not exist: {file_path}")
        return

    print(f"Loading signals from input file: {file_path}")
    if file_path.suffix == ".parquet":
        df = pd.read_parquet(file_path)
    elif file_path.suffix == ".csv":
        df = pd.read_csv(file_path, parse_dates=[time_column], date_format="ISO8601")
    else:
        print(f"ERROR: Unknown extension of the input file '{file_path.suffix}'. Only 'csv' and 'parquet' are supported")
        return

    print(f"Signals loaded. Length: {len(df)}. Width: {len(df.columns)}")

    #
    # Limit the source data
    #
    simulate_config = config["simulate_model"]
    interactive_overrides = config.pop("_simulate_interactive", None)

    data_start = simulate_config.get("data_start", None)
    data_end = simulate_config.get("data_end", None)
    data_days = days if days is not None else simulate_config.get("data_days", None)
    if interactive_overrides and "data_days" in interactive_overrides:
        data_days = interactive_overrides["data_days"]

    if data_start:
        if isinstance(data_start, str):
            df = df[ df[time_column] >= data_start ]
        elif isinstance(data_start, int):
            df = df.iloc[data_start:]

    if data_end:
        if isinstance(data_end, str):
            df = df[ df[time_column] < data_end ]
        elif isinstance(data_end, int):
            df = df.iloc[:-data_end]

    if data_days is not None and data_days > 0:
        # Keep only the last N days (by timestamp)
        cutoff = df[time_column].max() - timedelta(days=int(data_days))
        df = df[df[time_column] >= cutoff].reset_index(drop=True)

    df = df.reset_index(drop=True)

    print(f"Input data size {len(df)} records. Range: [{df.iloc[0][time_column]}, {df.iloc[-1][time_column]}]")

    #
    # Load signal train parameters
    #
    parameter_grid = simulate_config.get("grid")
    direction = simulate_config.get("direction", "")
    if direction not in ['long', 'short']:
        raise ValueError(f"Unknown value of {direction} in signal train model. Only 'long' or 'short' are possible.")
    topn_to_store = simulate_config.get("topn_to_store", 10)

    # Evaluate strings to produce lists with ranges of parameters
    if isinstance(parameter_grid.get("buy_signal_threshold"), str):
        parameter_grid["buy_signal_threshold"] = eval(parameter_grid.get("buy_signal_threshold"))
    if isinstance(parameter_grid.get("buy_signal_threshold_2"), str):
        parameter_grid["buy_signal_threshold_2"] = eval(parameter_grid.get("buy_signal_threshold_2"))
    if isinstance(parameter_grid.get("sell_signal_threshold"), str):
        parameter_grid["sell_signal_threshold"] = eval(parameter_grid.get("sell_signal_threshold"))
    if isinstance(parameter_grid.get("sell_signal_threshold_2"), str):
        parameter_grid["sell_signal_threshold_2"] = eval(parameter_grid.get("sell_signal_threshold_2"))

    # If necessary, disable sell parameters in grid search - they will be set from the buy parameters
    if simulate_config.get("buy_sell_equal"):
        parameter_grid["sell_signal_threshold"] = [None]
        parameter_grid["sell_signal_threshold_2"] = [None]

    months_in_simulation = (df[time_column].iloc[-1] - df[time_column].iloc[0]) / timedelta(days=365/12)

    # Optional: fees/leverage/balance (same as realtime trader_simulation) for backtest
    fee_bps_per_side = simulate_config.get("fee_bps_per_side")
    leverage = simulate_config.get("leverage")
    starting_balance = simulate_config.get("starting_balance")
    if fee_bps_per_side is None or leverage is None:
        trader_out = next((o for o in config.get("output_sets", []) if o.get("generator") == "trader_simulation"), None)
        if trader_out and isinstance(trader_out.get("config"), dict):
            fee_bps_per_side = fee_bps_per_side if fee_bps_per_side is not None else trader_out["config"].get("fee_bps_per_side")
            leverage = leverage if leverage is not None else trader_out["config"].get("leverage")
            starting_balance = starting_balance if starting_balance is not None else trader_out["config"].get("starting_balance")
    if interactive_overrides:
        if "leverage" in interactive_overrides:
            leverage = interactive_overrides["leverage"]
        if "starting_balance" in interactive_overrides:
            starting_balance = interactive_overrides["starting_balance"]
    fee_bps_per_side = float(fee_bps_per_side) if fee_bps_per_side is not None else 0
    leverage = float(leverage) if leverage is not None else 1.0
    starting_balance = float(starting_balance) if starting_balance is not None else None

    #
    # Find the generator, the parameters of which will be varied
    #
    generator_name = simulate_config.get("signal_generator")
    signal_generator = next((ss for ss in config.get("signal_sets", []) if ss.get('generator') == generator_name), None)
    if not signal_generator:
        raise ValueError(f"Signal generator '{generator_name}' not found among all 'signal_sets'")

    performances = list()
    for parameters in tqdm(ParameterGrid([parameter_grid]), desc="MODELS"):

        #
        # If equal parameters, then derive the sell parameter from the buy parameter
        #
        if simulate_config.get("buy_sell_equal"):
            parameters["sell_signal_threshold"] = -parameters["buy_signal_threshold"]
            #signal_model["sell_slope_threshold"] = -signal_model["buy_slope_threshold"]
            if parameters.get("buy_signal_threshold_2") is not None:
                parameters["sell_signal_threshold_2"] = -parameters["buy_signal_threshold_2"]

        #
        # Set new parameters of the signal generator
        #
        signal_generator["config"]["parameters"].update(parameters)

        #
        # Execute the signal generator with new parameters by producing new signal columns
        #
        df, new_features = generate_feature_set(df, signal_generator, config, App.model_store, last_rows=0)

        #
        # Simulate trade and compute performance using close price and two boolean signals
        # Add a pair of two dicts: performance dict and model parameters dict
        #

        # These boolean columns are used for performance measurement. Alternatively, they are in trade_signal_model
        buy_signal_column = signal_generator["config"]["names"][0]
        sell_signal_column = signal_generator["config"]["names"][1]

        # Perform backtesting (with optional fees/leverage/balance from simulate_model or trader_simulation)
        performance, long_performance, short_performance = simulated_trade_performance(
            df,
            buy_signal_column, sell_signal_column,
            'close',
            fee_bps_per_side=fee_bps_per_side,
            leverage=leverage,
            starting_balance=starting_balance,
            direction=direction,
        )

        if direction == "long":
            performance = long_performance
        elif direction == "short":
            performance = short_performance

        # Add monthly numbers
        performance["#transactions/M"] = round(performance["#transactions"] / months_in_simulation, 2)
        performance["profit/M"] = round(performance["profit"] / months_in_simulation, 2)
        performance["%profit/M"] = round(performance["%profit"] / months_in_simulation, 2)

        performances.append(dict(
            model=parameters,
            performance=performance,
            #performance={k: performance[k] for k in ['profit_percent_per_month', 'profitable', 'profit_percent_per_transaction', 'transaction_no_per_month']},
        ))

    #
    # Flatten and sort by best gain: total_return_pct when available (balance run), else %profit/M
    #
    def _best_gain_key(x):
        p = x["performance"]
        if p.get("total_return_pct") is not None:
            return (p["total_return_pct"], p.get("%profit/M", 0))
        return (p.get("%profit/M", 0), p.get("%profit", 0))

    performances = sorted(performances, key=_best_gain_key, reverse=True)
    performances = performances[:topn_to_store]

    # Column names (from one record)
    keys = list(performances[0]['model'].keys()) + \
           list(performances[0]['performance'].keys())

    lines = []
    for p in performances:
        record = list(p['model'].values()) + \
                 list(p['performance'].values())
                 #list(p['long_performance'].values()) + \
                 #list(p['short_performance'].values())
        #record = [f"{v:.2f}" if isinstance(v, float) else str(v) for v in record]
        record_str = ",".join(str(v) for v in record)
        lines.append(record_str)

    #
    # Store simulation parameters and performance
    #
    out_file_name = config.get("signal_models_file_name")
    out_path = (data_path / out_file_name).with_suffix(".txt").resolve()

    if out_path.is_file():
        add_header = False
    else:
        add_header = True
    with open(out_path, "a+") as f:
        if add_header:
            f.write(",".join(keys) + "\n")
        #f.writelines(lines)
        f.write("\n".join(lines) + "\n\n")

    print(f"Simulation results stored in: {out_path}. Lines: {len(lines)}.")

    # Print end investment and max drawdown for the best run (top of list)
    if performances:
        best = performances[0]
        perf = best["performance"]
        print("\n--- Best run (by gain: total return % or %profit/M) ---")
        if perf.get("balance_after") is not None and starting_balance is not None:
            print(f"  End investment: ${perf['balance_after']:,.2f}  (start: ${starting_balance:,.2f})")
            print(f"  Total return: {perf.get('total_return_pct', 0):+.1f}%")
        else:
            print(f"  Profit: ${perf.get('profit', 0):,.2f}  ({perf.get('%profit', 0):.1f}%)")
        if perf.get("max_drawdown") is not None:
            print(f"  Max drawdown: ${perf['max_drawdown']:,.2f}  ({perf.get('max_drawdown_pct', 0):.1f}%)")
        print(f"  Params: {best['model']}")

    elapsed = datetime.now() - now
    print(f"\nFinished simulation in {str(elapsed).split('.')[0]}")

    if apply_best and performances:
        import subprocess
        import sys
        print("\n--- Applying best parameters to config ---")
        r = subprocess.call(
            [sys.executable, "-m", "scripts.apply_best_simulation", "-c", config_file],
            cwd=Path(__file__).resolve().parent.parent,
        )
        if r != 0:
            print("WARNING: apply_best_simulation failed. Run: python -m scripts.apply_best_simulation -c <config>")


if __name__ == '__main__':
    main()
