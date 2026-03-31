#!/usr/bin/env python3
from __future__ import annotations
"""
MTF Confluence Strategy Backtester — log file **or** Binance spot klines.

Usage:
  python scripts/mtf_backtest.py --binance
  python scripts/mtf_backtest.py --binance path/to/config.json
  python scripts/mtf_backtest.py
  python scripts/mtf_backtest.py your_logs.txt
  python scripts/mtf_backtest.py your_logs.txt path/to/override.json

  --binance  Pull OHLCV from Binance (public API; no key required). Uses the same
             TA scoring as scripts/eth_ta_telegram.py. Settings under "binance" in
             configs/config.json (symbol, klines_limit).

  --30-mar   Backtest TA_STRATEGY_30_MAR rules (eth_ta_telegram._evaluate_30_mar_entry).
             Strongly recommended with --binance (log digests usually lack MTF scores).

  "strategy": "30_mar" in config.json also selects 30_MAR (overridden by --30-mar).

With no log argument (and no --binance), reads data/eth_ta_ethusdc.log.

Loads strat/sim/binance from configs/config.json when present, merged with defaults.
"""

import importlib.util
import json
import os
import re
import sys
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = _ROOT / "configs" / "config.json"
DEFAULT_ETH_DIGEST_LOG = _ROOT / "data" / "eth_ta_ethusdc.log"

DEFAULT_STRAT: Dict[str, Any] = {
    "min_conf": 57,
    "min_score": 1.5,
    "daily_req": True,
    "mean_rev": True,
    "rsi_5m": 25,
    "rsi_d": 35,
    "wr_5m": -90,
    "wr_d": -85,
}
DEFAULT_SIM: Dict[str, Any] = {
    "capital": 1000,
    "leverage": 20,
    "fee_pct": 0.04,
    "tp_pct": 1.5,
    "sl_pct": 0.3,
    "max_hold": 180,
    "skip_candles": 5,
}
DEFAULT_BINANCE: Dict[str, Any] = {
    "symbol": "ETHUSDC",
    "klines_limit": 1000,
}

M31_PRESETS: Dict[str, Dict[str, Any]] = {
    # Fewer, higher-conviction setups.
    "strict": {
        "m31_bull_score_thr": 1.8,
        "m31_bear_score_thr": -1.8,
        "m31_long_adx_min": 27,
        "m31_short_adx_min": 22,
    },
    # Balanced defaults follow the new confluence strategy.
    "balanced": {
        "m31_bull_score_thr": 1.5,
        "m31_bear_score_thr": -1.5,
        "m31_long_adx_min": 25,
        "m31_short_adx_min": 20,
        "m31_mtf_bull_min": 2,
        "m31_mtf_bear_min": 2,
    },
    # More permissive to increase frequency.
    "loose": {
        "m31_bull_score_thr": 1.2,
        "m31_bear_score_thr": -1.2,
        "m31_long_adx_min": 22,
        "m31_short_adx_min": 18,
        "m31_mtf_bull_min": 2,
        "m31_mtf_bear_min": 2,
    },
}


def _merge_cfg(base: Dict[str, Any], overlay: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(base)
    if overlay:
        for k, v in overlay.items():
            if v is not None:
                out[k] = v
    return out


def _apply_31_mar_preset(strat_cfg: Dict[str, Any], preset: str | None) -> Dict[str, Any]:
    if not preset:
        return strat_cfg
    p = (preset or "").strip().lower()
    if p not in M31_PRESETS:
        return strat_cfg
    out = dict(strat_cfg)
    out.update(M31_PRESETS[p])
    return out


def _normalize_strategy_name(s: Any) -> str:
    if not isinstance(s, str):
        return "legacy"
    n = s.strip().lower().replace("-", "_")
    if n in ("30_mar", "30mar", "mar_30"):
        return "30_mar"
    if n in ("31_mar", "31mar", "mar_31"):
        return "31_mar"
    return "legacy"


def load_mtf_config(path: Optional[Path] = None) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str]:
    """
    Load strat + sim + binance + strategy from JSON. Merges onto defaults.
    If path is None, uses configs/config.json when it exists.
    """
    p = path or DEFAULT_CONFIG_PATH
    strat_o: Optional[Dict[str, Any]] = None
    sim_o: Optional[Dict[str, Any]] = None
    binance_o: Optional[Dict[str, Any]] = None
    strategy = "legacy"
    if p.is_file():
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        strat_o = raw.get("strat")
        sim_o = raw.get("sim")
        binance_o = raw.get("binance")
        strategy = _normalize_strategy_name(raw.get("strategy", "legacy"))
    return (
        _merge_cfg(DEFAULT_STRAT, strat_o),
        _merge_cfg(DEFAULT_SIM, sim_o),
        _merge_cfg(DEFAULT_BINANCE, binance_o),
        strategy,
    )


# ----------------- DATA CLASSES -----------------
@dataclass
class TFData:
    signal: str
    score: float
    rsi: Optional[float]
    williams_r: Optional[float]
    adx: Optional[float]
    macd_signal: Optional[str] = None
    stoch_k: Optional[float] = None
    ma_buy_count: Optional[int] = None
    ma_sell_count: Optional[int] = None


@dataclass
class Snapshot:
    ts: datetime
    price: float
    tf: Dict[str, TFData]
    pivots: Dict[str, float]
    score5m: float
    sig5m: str
    # Filled by Binance builder — required for accurate 30_MAR (ATR TP/SL, counter-trend path)
    df_5m_slice: Optional[pd.DataFrame] = None
    atr_5m: Optional[float] = None


@dataclass
class Trade:
    entry_ts: datetime
    entry_px: float
    direction: str
    tp: float
    sl: float
    lev: float
    exit_ts: Optional[datetime] = None
    exit_px: Optional[float] = None
    reason: Optional[str] = None
    pnl_pct: Optional[float] = None
    hold_min: Optional[float] = None


def _load_project_dotenv() -> None:
    """Merge project root .env into os.environ (same as eth_ta_telegram)."""
    p = _ROOT / ".env"
    if not p.is_file():
        return
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        eq = line.index("=")
        key = line[:eq].strip()
        val = line[eq + 1 :].strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1].replace("\\'", "'")
        if key:
            os.environ[key] = val


def _load_eth_ta():
    p = _ROOT / "scripts" / "eth_ta_telegram.py"
    name = "eth_ta_telegram_mtf_bt"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ensure_close_time(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "close_time" in out.columns and not pd.api.types.is_datetime64_any_dtype(out["close_time"]):
        out["close_time"] = pd.to_datetime(out["close_time"], unit="ms", utc=True)
    return out


def _vol_spike_5m(sl5: pd.DataFrame) -> bool:
    if len(sl5) < 20:
        return False
    vol = sl5["volume"].astype(float)
    sma = float(vol.iloc[-20:].mean())
    return bool(sma > 0 and float(vol.iloc[-1]) > 1.5 * sma)


def _snapshot_to_tasnapshot_30mar(eth: Any, snap: Snapshot) -> Any:
    """Build eth_ta_telegram.TASnapshot for _evaluate_30_mar_entry (no live API)."""
    t = snap.tf
    m: Dict[str, float] = {}
    for src, dst in (
        ("5m", "5m"),
        ("15m", "15m"),
        ("30m", "30m"),
        ("1h", "1h"),
        ("Daily", "1d"),
        ("Weekly", "1w"),
    ):
        if src in t:
            m[dst] = float(t[src].score)

    sl5 = snap.df_5m_slice
    mar_rsi: Dict[str, Optional[float]] = {}
    mar_willr: Dict[str, Optional[float]] = {}
    if "5m" in t:
        mar_rsi["5m"] = t["5m"].rsi
        mar_willr["5m"] = t["5m"].williams_r
    if "Daily" in t:
        mar_rsi["1d"] = t["Daily"].rsi
        mar_willr["1d"] = t["Daily"].williams_r
    if "Weekly" in t:
        mar_rsi["1w"] = t["Weekly"].rsi

    mar_adx_daily = t["Daily"].adx if "Daily" in t else None
    pivot = dict(snap.pivots) if snap.pivots else None
    m5 = float(m.get("5m", snap.score5m))
    lab5 = eth._tf_label(m5)
    vol_spike = _vol_spike_5m(sl5) if sl5 is not None and len(sl5) >= 20 else False

    return eth.TASnapshot(
        text="",
        banner=None,
        tf_scores=[m5],
        tf_labels=[lab5],
        mean_score=m5,
        score_5m=m5,
        score_for_entry=m5,
        entry_score_kind="5m",
        label_5m=lab5,
        df_5m=sl5 if sl5 is not None and len(sl5) else None,
        htf_scores={},
        mtf_30mar=m,
        mar_rsi=mar_rsi,
        mar_willr=mar_willr,
        mar_adx_daily=mar_adx_daily,
        mar_pivot=pivot,
        mar_vol_spike_5m=vol_spike,
    )


def _tf_data_from_slice(sl: pd.DataFrame, eth: Any) -> Optional[TFData]:
    if len(sl) < 60:
        return None
    sc, det = eth._analyze_ohlcv(sl)
    scf = float(sc)
    macd_sig: Optional[str] = None
    stoch_k: Optional[float] = None
    ma_buy: Optional[int] = None
    ma_sell: Optional[int] = None
    macd_raw = str(det.get("MACD", "")).strip().lower()
    if macd_raw in ("buy", "sell", "neutral"):
        macd_sig = macd_raw
    st_raw = str(det.get("STOCH", ""))
    m_st = re.search(r"([-+]?\d+(?:\.\d+)?)", st_raw)
    if m_st:
        try:
            stoch_k = float(m_st.group(1))
        except ValueError:
            stoch_k = None
    ma_raw = str(det.get("MA", ""))
    m_ma = re.search(r"\((\d+)\s+buy,\s*(\d+)\s+sell\)", ma_raw)
    if m_ma:
        try:
            ma_buy = int(m_ma.group(1))
            ma_sell = int(m_ma.group(2))
        except ValueError:
            ma_buy, ma_sell = None, None
    return TFData(
        signal=eth._tf_label(scf),
        score=scf,
        rsi=eth._scalar_rsi_df(sl),
        williams_r=eth._scalar_willr_df(sl),
        adx=eth._scalar_adx_df(sl),
        macd_signal=macd_sig,
        stoch_k=stoch_k,
        ma_buy_count=ma_buy,
        ma_sell_count=ma_sell,
    )


def _fetch_klines_paginated(
    client: Any,
    *,
    symbol: str,
    interval: str,
    limit: int,
    per_call_max: int = 1000,
) -> List[Any]:
    """
    Fetch up to `limit` klines using multiple Binance calls.
    Binance get_klines is capped per request (typically 1000), so larger requests
    must be paginated by walking backward via endTime.
    """
    target = max(1, int(limit))
    step_cap = max(1, int(per_call_max))
    chunks: List[List[Any]] = []
    remaining = target
    end_time: Optional[int] = None
    last_oldest_open: Optional[int] = None

    while remaining > 0:
        req = min(step_cap, remaining)
        kwargs: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": req}
        if end_time is not None:
            kwargs["endTime"] = int(end_time)
        kl = client.get_klines(**kwargs)
        if not kl:
            break
        chunks.append(kl)
        remaining -= len(kl)

        oldest_open = int(kl[0][0])
        if last_oldest_open is not None and oldest_open >= last_oldest_open:
            break
        last_oldest_open = oldest_open
        end_time = oldest_open - 1

        if len(kl) < req:
            break

    merged: List[Any] = []
    seen_open: set[int] = set()
    for chunk in reversed(chunks):
        for row in chunk:
            ot = int(row[0])
            if ot in seen_open:
                continue
            seen_open.add(ot)
            merged.append(row)

    if len(merged) > target:
        merged = merged[-target:]
    return merged


def build_snapshots_binance(binance_cfg: Dict[str, Any]) -> List[Snapshot]:
    """
    Walk 5m bars; for each bar end, recompute TA on all candles closed by then (no look-ahead).
    Same _analyze_ohlcv / labels as eth_ta_telegram.
    """
    _load_project_dotenv()
    eth = _load_eth_ta()
    symbol = str(binance_cfg.get("symbol", "ETHUSDC")).upper().strip()
    lim_5m = int(binance_cfg.get("klines_limit", 1000))
    client = eth._client()

    kl5 = _fetch_klines_paginated(client, symbol=symbol, interval="5m", limit=lim_5m)
    if not kl5 or len(kl5) < 61:
        return []
    df_5m = _ensure_close_time(eth._klines_to_df(kl5))

    # Higher timeframes: enough history for 60-bar TA on slower TFs
    fetch_plan: List[Tuple[str, int]] = [
        ("15m", min(1500, max(lim_5m, 500))),
        ("30m", min(1500, max(lim_5m, 500))),
        ("1h", min(1500, max(lim_5m, 500))),
        ("1d", max(220, lim_5m // 4)),
        ("1w", 120),
        ("1M", 80),
    ]
    snap_key = {"15m": "15m", "30m": "30m", "1h": "1h", "1d": "Daily", "1w": "Weekly", "1M": "Monthly"}
    frames: Dict[str, pd.DataFrame] = {}
    for iv, cap in fetch_plan:
        try:
            kl = _fetch_klines_paginated(client, symbol=symbol, interval=iv, limit=cap)
            if kl and len(kl) >= 60:
                frames[iv] = _ensure_close_time(eth._klines_to_df(kl))
        except Exception:
            continue

    snaps: List[Snapshot] = []
    min_start = 60
    for i in range(min_start, len(df_5m)):
        end_ts = df_5m["timestamp"].iloc[i] + pd.Timedelta(minutes=5)
        sl5 = df_5m.iloc[: i + 1].copy()
        t5 = _tf_data_from_slice(sl5, eth)
        if t5 is None:
            continue

        tfd: Dict[str, TFData] = {"5m": t5}
        for iv, sk in snap_key.items():
            dfc = frames.get(iv)
            if dfc is None:
                continue
            sl = dfc.loc[dfc["close_time"] <= end_ts]
            td = _tf_data_from_slice(sl, eth)
            if td is not None:
                tfd[sk] = td

        piv: Dict[str, float] = {}
        d1 = frames.get("1d")
        if d1 is not None:
            sl_d = d1.loc[d1["close_time"] <= end_ts]
            if len(sl_d) >= 2:
                pv = eth._pivot_classic(sl_d.iloc[-2])
                for k in ("R3", "R2", "R1", "P", "S1", "S2", "S3"):
                    piv[k] = float(pv[k])

        ts = df_5m["timestamp"].iloc[i]
        ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.utcfromtimestamp(ts.timestamp())
        if getattr(ts_py, "tzinfo", None) is not None:
            ts_py = ts_py.replace(tzinfo=None)

        price = float(df_5m["close"].iloc[i])
        atr_v = eth._atr_from_df(sl5)
        atr_f = float(atr_v) if atr_v is not None and float(atr_v) > 0 else None
        snaps.append(
            Snapshot(
                ts=ts_py,
                price=price,
                tf=tfd,
                pivots=piv,
                score5m=t5.score,
                sig5m=t5.signal,
                df_5m_slice=sl5.copy(),
                atr_5m=atr_f,
            )
        )

    return snaps


# ----------------- LOG PARSER -----------------
def parse_logs(text: str) -> List[Snapshot]:
    """Parse intelligent_bot TA digest logs into snapshots"""
    blocks = re.split(r'📊 TA digest — ETHUSDC', text)
    snaps = []
    for blk in blocks[1:]:
        # Timestamp
        tm = re.search(r'As of (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC', blk)
        if not tm: continue
        ts = datetime.strptime(tm.group(1), '%Y-%m-%d %H:%M')
        
        # Price (5m close)
        pm = re.search(r'──\s*5 Min.*?Close:\s*([\d,\.]+)', blk, re.DOTALL)
        px = float(pm.group(1).replace(',','')) if pm else 1990.0
        
        # Timeframes
        tfd = {}
        for label in ['5 Min','15 Min','30 Min','Hourly','Daily','Weekly','Monthly']:
            key = label.replace(' Min','m').replace('Hourly','1h')
            sec = re.search(rf'──\s*{re.escape(label)}\s*──[^\n]*\n([\s\S]*?)(?=──\s*\w|\n── Pivot|\Z)', blk)
            if sec:
                c = sec.group(1)
                sig = re.match(r'\s*([A-Z][a-z]+.*)', c.split('\n')[0])
                rsi = re.search(r'RSI\(14\):\s*([\d\.]+)', c)
                wr = re.search(r'WilliamsR:\s*([-?\d\.]+)', c) 
                adx = re.search(r'ADX\(14\):\s*([\d\.]+)', c)
                stoch = re.search(r'STOCH:\s*([-?\d\.]+)', c)
                macd = re.search(r'MACD:\s*(Buy|Sell|Neutral)', c, re.IGNORECASE)
                ma_line = re.search(r'MA:\s*.*?\((\d+)\s+buy,\s*(\d+)\s+sell\)', c, re.IGNORECASE)
                tfd[key] = TFData(
                    signal=sig.group(1).strip() if sig else 'Neutral',
                    score=0, rsi=float(rsi.group(1)) if rsi else None,
                    williams_r=float(wr.group(1)) if wr else None,
                    adx=float(adx.group(1)) if adx else None,
                    macd_signal=(macd.group(1).strip().lower() if macd else None),
                    stoch_k=float(stoch.group(1)) if stoch else None,
                    ma_buy_count=int(ma_line.group(1)) if ma_line else None,
                    ma_sell_count=int(ma_line.group(2)) if ma_line else None,
                )
        
        # Pivots
        piv = {}
        pv = re.search(r'── Pivot.*?──\n([\s\S]*?)(?=\n\n|\nSummary)', blk)
        if pv:
            for ln in pv.group(1).strip().split('\n'):
                if ':' in ln and any(k in ln for k in ['R3','R2','R1','P:','S1','S2','S3']):
                    k,v = ln.split(':'); piv[k.strip()] = float(v.strip().replace(',',''))
        
        # Summary
        sc = re.search(r'5m score:\s*([-?\d\.]+)', blk)
        sg = re.search(r'Entry signal \(5m TF\):\s*(\w+(?:\s+\w+)?)', blk)

        # 5m ATR for ATR-based exits (useful for non-Binance/log backtests)
        atr5 = None
        sec5 = re.search(r'──\s*5 Min\s*──[^\n]*\n([\s\S]*?)(?=──\s*\w|\n── Pivot|\Z)', blk)
        if sec5:
            m_atr = re.search(r'ATR\(14\):\s*([\d\.]+)', sec5.group(1))
            if m_atr:
                try:
                    atr5 = float(m_atr.group(1))
                except ValueError:
                    atr5 = None

        snaps.append(Snapshot(ts, px, tfd, piv, 
                             float(sc.group(1)) if sc else 0,
                             sg.group(1) if sg else 'Neutral',
                             None,
                             atr5))
    return sorted(snaps, key=lambda s: s.ts)

# ----------------- STRATEGY -----------------
def mtf_signal(snap: Snapshot, cfg: Dict) -> Dict:
    """Generate signal based on MTF confluence rules"""
    t5, tD = snap.tf.get('5m'), snap.tf.get('Daily')
    if not t5 or not tD:
        return {'sig': 'WAIT', 'reason': 'Missing data'}

    # Log parser leaves TF score at 0; Binance path fills scores. Prefer numeric 5m from snapshot when needed.
    sc5 = t5.score if abs(t5.score) > 1e-9 else float(snap.score5m)

    # Count aligned timeframes (simplified confluence)
    bearish = sum(1 for t in snap.tf.values() if t.signal in ['Sell', 'Strong Sell'])
    bullish = sum(1 for t in snap.tf.values() if t.signal in ['Buy', 'Strong Buy'])
    conf_pct = max(bearish, bullish) / len(snap.tf) * 100 if snap.tf else 0

    # SHORT conditions
    if (
        sc5 <= -cfg['min_score']
        and bearish >= 4
        and conf_pct >= cfg['min_conf']
        and (not cfg['daily_req'] or tD.signal in ['Sell', 'Strong Sell'])
    ):
        return {'sig': 'SHORT', 'conf': 'HIGH' if conf_pct >= 70 else 'MED', 'reason': f'Bearish MTF ({conf_pct:.0f}%)'}

    # LONG conditions (trend-following)
    if (
        sc5 >= cfg['min_score']
        and bullish >= 4
        and conf_pct >= cfg['min_conf']
        and (not cfg['daily_req'] or tD.signal not in ['Strong Sell', 'Sell'])
    ):
        return {'sig': 'LONG', 'conf': 'HIGH' if conf_pct >= 70 else 'MED', 'reason': f'Bullish MTF ({conf_pct:.0f}%)'}
    
    # Mean-reversion LONG (counter-trend, strict)
    if (cfg['mean_rev'] and t5.rsi and tD.rsi and t5.williams_r and tD.williams_r and
        t5.rsi <= cfg['rsi_5m'] and tD.rsi <= cfg['rsi_d'] and 
        t5.williams_r <= cfg['wr_5m'] and tD.williams_r <= cfg['wr_d']):
        sup = min([snap.pivots.get(k,float('inf')) for k in ['S1','S2','S3'] if k in snap.pivots], default=float('inf'))
        if snap.price <= sup * 1.01:  # Near support
            return {'sig':'LONG','conf':'LOW','reason':'Mean-rev: oversold+support'}
    
    return {'sig': 'WAIT', 'reason': f'No edge: conf={conf_pct:.0f}%,5m={sc5:.2f}'}


def _evaluate_31_mar_entry(
    snap: Snapshot,
    cfg: Dict[str, Any],
    prev: Snapshot | None = None,
) -> tuple[str | None, str, float | None, float | None]:
    """
    31_MAR ruleset (from full 15-day digest analysis):
    LONG:
      5m Buy/Strong Buy, score >= threshold, MTF bull count >= min,
      1h bullish, Daily not Strong Sell, ADX(5m) > long threshold,
      MACD(5m)=Buy, RSI(5m) in [40,65], STOCH(5m) <= 80,
      Weekly not opposing.
    SHORT:
      5m Sell/Strong Sell, score <= threshold, MTF bear count >= min,
      1h bearish, Daily not Strong Buy, ADX(5m) > short threshold,
      MACD(5m)=Sell, MA sell-count >= 7, RSI(5m) > 30, STOCH(5m) >= 20,
      Weekly not opposing.
    Returns (side, reason, tp_atr_mult, sl_atr_mult).
    """
    t = snap.tf
    req = ("5m", "15m", "30m", "1h", "Daily", "Weekly")
    if any(k not in t for k in req):
        return None, "31_MAR: missing TF data", None, None

    def _score(sn: Snapshot, k: str) -> float:
        v = float(sn.tf[k].score)
        if k == "5m" and abs(v) <= 1e-9:
            return float(sn.score5m)
        return v

    s5 = _score(snap, "5m")
    s15 = _score(snap, "15m")
    s30 = _score(snap, "30m")
    s1h = _score(snap, "1h")
    sd = _score(snap, "Daily")
    sw = _score(snap, "Weekly")

    sig5 = (t["5m"].signal or "").strip().lower()
    sig1h = (t["1h"].signal or "").strip().lower()
    sigd = (t["Daily"].signal or "").strip().lower()
    sigw = (t["Weekly"].signal or "").strip().lower()
    rsi5 = t["5m"].rsi
    adx5 = t["5m"].adx
    adx_d = t["Daily"].adx
    macd5 = (t["5m"].macd_signal or "").strip().lower()
    stoch5 = t["5m"].stoch_k
    ma_sell_5 = t["5m"].ma_sell_count

    bull_count = sum(1 for v in (s5, s15, s30, s1h, sd) if v >= 2.0)
    bear_count = sum(1 for v in (s5, s15, s30, s1h, sd) if v <= -2.0)
    bull_min = int(cfg.get("m31_mtf_bull_min", 2))
    bear_min = int(cfg.get("m31_mtf_bear_min", 2))

    # Avoid choppy conflict windows where both sides have strong confluence.
    if bull_count >= bull_min and bear_count >= bear_min:
        return None, "31_MAR: conflict (bull>=2 and bear>=2)", None, None

    use_daily_adx_guard = str(cfg.get("m31_use_daily_adx_guard", "0")).strip().lower() in ("1", "true", "yes", "on")
    adx_d_min = float(cfg.get("m31_daily_adx_min", 20.0))
    if use_daily_adx_guard and adx_d is not None and float(adx_d) < adx_d_min:
        return None, f"31_MAR: daily ADX guard ({adx_d:.1f} < {adx_d_min:.1f})", None, None

    long_thr = float(cfg.get("m31_bull_score_thr", 1.5))
    short_thr = float(cfg.get("m31_bear_score_thr", -1.5))
    long_adx_min = float(cfg.get("m31_long_adx_min", 25.0))
    short_adx_min = float(cfg.get("m31_short_adx_min", 20.0))

    long_signal_ok = sig5 in ("buy", "strong buy")
    short_signal_ok = sig5 in ("sell", "strong sell")
    h1_bull_ok = sig1h in ("buy", "strong buy")
    h1_bear_ok = sig1h in ("sell", "strong sell")
    daily_strong_sell = sigd == "strong sell"
    daily_strong_buy = sigd == "strong buy"
    weekly_strong_sell = sigw in ("sell", "strong sell") or sw <= -0.8
    weekly_strong_buy = sigw in ("buy", "strong buy") or sw >= 0.8
    rsi_long_ok = rsi5 is not None and 40.0 <= float(rsi5) <= 65.0
    rsi_short_ok = rsi5 is not None and float(rsi5) > 30.0
    stoch_long_ok = stoch5 is None or float(stoch5) <= 80.0
    stoch_short_ok = stoch5 is None or float(stoch5) >= 20.0
    adx_long_ok = adx5 is not None and float(adx5) > long_adx_min
    adx_short_ok = adx5 is not None and float(adx5) > short_adx_min
    macd_long_ok = macd5 == "buy"
    macd_short_ok = macd5 == "sell"
    ma_short_ok = ma_sell_5 is not None and int(ma_sell_5) >= int(cfg.get("m31_short_ma_sell_min", 7))

    if (
        short_signal_ok
        and s5 <= short_thr
        and bear_count >= bear_min
        and h1_bear_ok
        and not daily_strong_buy
        and not weekly_strong_buy
        and adx_short_ok
        and macd_short_ok
        and ma_short_ok
        and rsi_short_ok
        and stoch_short_ok
    ):
        return (
            "SHORT",
            "31_MAR short: confluence + ADX/MACD/MA",
            float(cfg.get("m31_tp_atr_short", 1.5)),
            float(cfg.get("m31_sl_atr_short", 0.75)),
        )

    if (
        long_signal_ok
        and s5 >= long_thr
        and bull_count >= bull_min
        and h1_bull_ok
        and not daily_strong_sell
        and not weekly_strong_sell
        and adx_long_ok
        and macd_long_ok
        and rsi_long_ok
        and stoch_long_ok
    ):
        return (
            "LONG",
            "31_MAR long: confluence + ADX/MACD",
            float(cfg.get("m31_tp_atr_long", 1.5)),
            float(cfg.get("m31_sl_atr_long", 1.0)),
        )

    return None, "31_MAR: no confluence setup", None, None


# ----------------- SIMULATOR -----------------
def simulate(
    snaps: List[Snapshot],
    strat_cfg: Dict,
    sim_cfg: Dict,
    *,
    eth: Any = None,
    strategy: str = "legacy",
) -> Dict:
    """Run backtest simulation (legacy mtf_signal or 30_MAR via eth._evaluate_30_mar_entry)."""
    if not snaps:
        return {
            'signals': 0,
            'completed': 0,
            'wins': 0,
            'win_rate': 0.0,
            'avg_pnl': 0.0,
            'profit_factor': float('inf'),
            'total_pnl': 0.0,
            'final_equity': sim_cfg['capital'],
            'trades': [],
            'equity_curve': [{'t': datetime.utcnow(), 'e': sim_cfg['capital']}],
        }

    trades, equity = [], [{'t': snaps[0].ts, 'e': sim_cfg['capital']}]
    eq, i = sim_cfg['capital'], 0

    while i < len(snaps):
        snap = snaps[i]
        if strategy == "30_mar":
            if eth is None:
                raise ValueError("simulate(..., strategy='30_mar') requires eth module")
            tas = _snapshot_to_tasnapshot_30mar(eth, snap)
            side, mar_reason, tpm, slm = eth._evaluate_30_mar_entry(tas)
            if side not in ("LONG", "SHORT"):
                sig: Dict[str, Any] = {"sig": "WAIT", "reason": mar_reason}
            else:
                sig = {
                    "sig": side,
                    "reason": mar_reason,
                    "tp_atr_mult": tpm,
                    "sl_atr_mult": slm,
                }
        elif strategy == "31_mar":
            prev = snaps[i - 1] if i > 0 else None
            side, mar_reason, tpm, slm = _evaluate_31_mar_entry(snap, strat_cfg, prev=prev)
            if side not in ("LONG", "SHORT"):
                sig = {"sig": "WAIT", "reason": mar_reason}
            else:
                sig = {
                    "sig": side,
                    "reason": mar_reason,
                    "tp_atr_mult": tpm,
                    "sl_atr_mult": slm,
                }
        else:
            sig = mtf_signal(snap, strat_cfg)

        if sig["sig"] != "WAIT":
            entry_px = snap.price
            direction = str(sig["sig"])
            atr = snap.atr_5m
            tpm = sig.get("tp_atr_mult")
            slm = sig.get("sl_atr_mult")
            if (
                strategy in ("30_mar", "31_mar")
                and atr is not None
                and atr > 0
                and tpm is not None
                and slm is not None
            ):
                tpm_f, slm_f = float(tpm), float(slm)
                if direction == "SHORT":
                    tp, sl = entry_px - tpm_f * atr, entry_px + slm_f * atr
                else:
                    tp, sl = entry_px + tpm_f * atr, entry_px - slm_f * atr
                if strategy == "31_mar" and str(strat_cfg.get("m31_tp_use_pivot", "1")).strip().lower() in ("1", "true", "yes", "on"):
                    # Use nearer daily pivot as TP if it comes before ATR TP.
                    if direction == "LONG":
                        cands = [float(snap.pivots[k]) for k in ("R1", "R2") if k in snap.pivots and float(snap.pivots[k]) > entry_px]
                        if cands:
                            tp = min(tp, min(cands))
                    else:
                        cands = [float(snap.pivots[k]) for k in ("S1", "S2") if k in snap.pivots and float(snap.pivots[k]) < entry_px]
                        if cands:
                            tp = max(tp, max(cands))
            else:
                if direction == "SHORT":
                    tp = entry_px * (1 - sim_cfg["tp_pct"] / 100)
                    sl = entry_px * (1 + sim_cfg["sl_pct"] / 100)
                else:
                    tp = entry_px * (1 + sim_cfg["tp_pct"] / 100)
                    sl = entry_px * (1 - sim_cfg["sl_pct"] / 100)
            exited = False
            hold_min: Optional[float] = None
            exit_px = entry_px
            reason = ''
            future = snap
            max_hold_min = float(sim_cfg['max_hold'])
            if strategy == "31_mar":
                max_hold_min = float(strat_cfg.get("m31_max_hold_min", max_hold_min))
            for j in range(i + 1, len(snaps)):
                future = snaps[j]
                hold_min = (future.ts - snap.ts).total_seconds() / 60

                if hold_min > max_hold_min:
                    exit_px, reason = future.price, 'TIME'
                    exited = True
                    break

                # 31_MAR special exits: take profit as score mean-reverts toward zero.
                if strategy == "31_mar":
                    f5 = future.tf.get("5m")
                    if f5 is not None:
                        f5s = float(f5.score) if abs(float(f5.score)) > 1e-9 else float(future.score5m)
                        if direction == 'LONG':
                            # Early exit when momentum fully reverses on 5m.
                            if f5s < 0 and f5s <= float(strat_cfg.get("m31_exit_long_score", 0.5)):
                                exit_px, reason = future.price, 'SIG_EXIT'
                                exited = True
                                break
                        else:
                            if f5s > 0 and f5s >= float(strat_cfg.get("m31_exit_short_score", -0.5)):
                                exit_px, reason = future.price, 'SIG_EXIT'
                                exited = True
                                break

                if direction == 'SHORT':
                    if future.price <= tp:
                        exit_px, reason = tp, 'TP'
                        exited = True
                        break
                    if future.price >= sl:
                        exit_px, reason = sl, 'SL'
                        exited = True
                        break
                else:
                    if future.price >= tp:
                        exit_px, reason = tp, 'TP'
                        exited = True
                        break
                    if future.price <= sl:
                        exit_px, reason = sl, 'SL'
                        exited = True
                        break

            if exited:
                pc = (exit_px - entry_px) / entry_px
                if direction == 'SHORT':
                    pc = -pc
                pnl_pct = pc * 100 * sim_cfg['leverage'] - sim_cfg['fee_pct'] * 2
                trades.append(
                    Trade(
                        snap.ts,
                        entry_px,
                        direction,
                        tp,
                        sl,
                        sim_cfg['leverage'],
                        future.ts,
                        exit_px,
                        reason,
                        pnl_pct,
                        hold_min,
                    )
                )
                eq += pnl_pct / 100 * sim_cfg['capital']
                equity.append({'t': future.ts, 'e': eq})
            
            i += sim_cfg['skip_candles']  # Avoid overlapping trades
            continue
        i += 1
    
    # Generate report
    comp = [t for t in trades if t.exit_px]
    wins = [t for t in comp if t.pnl_pct and t.pnl_pct > 0]
    pnl = [t.pnl_pct for t in comp if t.pnl_pct is not None]
    
    return {
        'signals': len(trades), 'completed': len(comp), 'wins': len(wins),
        'win_rate': len(wins)/len(comp)*100 if comp else 0,
        'avg_pnl': np.mean(pnl) if pnl else 0,
        'profit_factor': abs(sum(t.pnl_pct for t in wins)/sum(t.pnl_pct for t in comp if t.pnl_pct and t.pnl_pct<0)) if any(t.pnl_pct and t.pnl_pct<0 for t in comp) else float('inf'),
        'total_pnl': sum(pnl) if pnl else 0,
        'final_equity': eq,
        'trades': [asdict(t) for t in trades],
        'equity_curve': equity
    }

# ----------------- MAIN -----------------
def run_backtest(
    log_file: Optional[str] = None,
    strat_cfg: Optional[Dict[str, Any]] = None,
    sim_cfg: Optional[Dict[str, Any]] = None,
    *,
    use_binance: bool = False,
    binance_cfg: Optional[Dict[str, Any]] = None,
    strategy: str = "legacy",
    m31_preset: str | None = None,
) -> Dict:
    """Run backtest from a digest log file or from Binance OHLCV."""
    _load_project_dotenv()
    strat_cfg = _merge_cfg(DEFAULT_STRAT, strat_cfg)
    if strategy == "31_mar":
        strat_cfg = _apply_31_mar_preset(strat_cfg, m31_preset)
    sim_cfg = _merge_cfg(DEFAULT_SIM, sim_cfg)
    binance_cfg = _merge_cfg(DEFAULT_BINANCE, binance_cfg)
    sym_env = (os.environ.get("TA_SYMBOL") or "").strip().upper()
    if sym_env and use_binance:
        binance_cfg = dict(binance_cfg)
        binance_cfg["symbol"] = sym_env

    if strategy == "30_mar":
        print(
            "📐 Strategy: 30_MAR — same entry rules as TA_STRATEGY_30_MAR in eth_ta_telegram.py "
            "(TA_30_MAR_* env apply).",
            flush=True,
        )
        if not use_binance:
            print(
                "⚠ 30_MAR backtest on log text: higher-TF scores are often missing → few/no trades. "
                "Use --binance for OHLCV replay.",
                flush=True,
            )
    elif strategy == "31_mar":
        if m31_preset:
            print(f"📌 31_MAR preset: {m31_preset}", flush=True)
        print(
            "📐 Strategy: 31_MAR — 5m score + 1h/MTF confluence + daily guard + ATR/pivot exits.",
            flush=True,
        )

    if use_binance:
        print(
            f"📥 Binance spot: {binance_cfg.get('symbol', 'ETHUSDC')} "
            f"(5m limit={binance_cfg.get('klines_limit', 1000)})..."
        )
        snaps = build_snapshots_binance(binance_cfg)
        print(f"✓ Built {len(snaps)} snapshots (no look-ahead on higher TFs)")
    else:
        if not log_file:
            raise ValueError("log_file required unless use_binance=True")
        print(f"📥 Loading {log_file}...")
        with open(log_file, encoding="utf-8") as f:
            text = f.read()
        snaps = parse_logs(text)
        print(f"✓ Parsed {len(snaps)} snapshots")

    print("🎲 Running simulation...")
    eth_mod = _load_eth_ta() if strategy == "30_mar" else None
    report = simulate(snaps, strat_cfg, sim_cfg, eth=eth_mod, strategy=strategy)

    if strategy == "30_mar":
        title = "30_MAR BACKTEST RESULTS"
    elif strategy == "31_mar":
        title = "31_MAR BACKTEST RESULTS"
    else:
        title = "BACKTEST RESULTS"
    print(f"\n📊 {title}")
    print(f"   Trades: {report['completed']} | Win Rate: {report['win_rate']:.1f}%")
    print(f"   Avg P/L: {report['avg_pnl']:+.2f}% | Total P/L: {report['total_pnl']:+.2f}%")
    print(f"   Profit Factor: {report['profit_factor']:.2f} | Final Equity: ${report['final_equity']:.2f}")
    
    if report['trades']:
        print(f"\n📋 Last 5 trades:")
        for t in report['trades'][-5:]:
            pnl = f"{t['pnl_pct']:+.2f}%" if t['pnl_pct'] is not None else "Open"
            print(f"   {t['entry_ts']} {t['direction']:5s} @{t['entry_px']:.2f} → {t['reason'] or 'Open':4s} | {pnl}")
    
    return report

if __name__ == "__main__":
    raw = sys.argv[1:]
    if not raw or raw[0] in ("-h", "--help"):
        print("Usage:")
        print("  python scripts/mtf_backtest.py --binance [--30-mar|--31-mar] [--strict|--balanced|--loose] [config.json]")
        print("  python scripts/mtf_backtest.py [--30-mar|--31-mar] [--strict|--balanced|--loose] [log_file.txt] [config.json]")
        print('\n  --binance   Binance spot OHLCV replay')
        print('  --30-mar    Entry rules = eth_ta_telegram 30_MAR (prefer with --binance)')
        print('  --31-mar    New regime strategy from this history (HTF bear + LTF bull-trap fade)')
        print('  --strict    31_MAR stricter filter preset')
        print('  --balanced  31_MAR balanced preset')
        print('  --loose     31_MAR looser filter preset')
        print(f'\n  "strategy": "30_mar" or "31_mar" in {DEFAULT_CONFIG_PATH.name} also enables it.')
        print(f"\nDefault log (if omitted): {DEFAULT_ETH_DIGEST_LOG}")
        print(f"Default config (if present): {DEFAULT_CONFIG_PATH}")
        print("  TA_SYMBOL in .env overrides binance.symbol when using --binance.")
        sys.exit(0)

    use_30_mar = "--30-mar" in raw
    use_31_mar = "--31-mar" in raw
    m31_preset: str | None = None
    if "--strict" in raw:
        m31_preset = "strict"
    elif "--balanced" in raw:
        m31_preset = "balanced"
    elif "--loose" in raw:
        m31_preset = "loose"
    raw = [a for a in raw if a not in ("--30-mar", "--31-mar", "--strict", "--balanced", "--loose")]
    use_binance = "--binance" in raw
    pos = [a for a in raw if a != "--binance"]

    cfg_path: Optional[Path] = None
    log_path: Optional[Path] = None

    if use_binance:
        if pos:
            cfg_path = Path(pos[0]).expanduser()
            if not cfg_path.is_file():
                print(f"Error: config file not found: {cfg_path}", file=sys.stderr)
                sys.exit(1)
        strat_cfg, sim_cfg, binance_cfg, strat_from_cfg = load_mtf_config(cfg_path)
        strategy = "31_mar" if use_31_mar else ("30_mar" if use_30_mar else strat_from_cfg)
        if cfg_path is None and DEFAULT_CONFIG_PATH.is_file():
            print(f"📋 Using config: {DEFAULT_CONFIG_PATH}")
        elif cfg_path is not None:
            print(f"📋 Using config: {cfg_path.resolve()}")
        run_backtest(
            None,
            strat_cfg,
            sim_cfg,
            use_binance=True,
            binance_cfg=binance_cfg,
            strategy=strategy,
            m31_preset=m31_preset,
        )
        sys.exit(0)

    log_path = Path(pos[0]).expanduser() if pos else DEFAULT_ETH_DIGEST_LOG
    if len(pos) > 1:
        cfg_path = Path(pos[1]).expanduser()
        if not cfg_path.is_file():
            print(f"Error: config file not found: {cfg_path}", file=sys.stderr)
            sys.exit(1)

    if not log_path.is_file():
        print(f"Error: log file not found: {log_path}", file=sys.stderr)
        print(
            "  Use: python scripts/mtf_backtest.py --binance   (no log file), or collect digests via eth-ta-telegram.",
            file=sys.stderr,
        )
        sys.exit(1)

    strat_cfg, sim_cfg, _binance_cfg, strat_from_cfg = load_mtf_config(cfg_path)
    strategy = "31_mar" if use_31_mar else ("30_mar" if use_30_mar else strat_from_cfg)
    if cfg_path is None and DEFAULT_CONFIG_PATH.is_file():
        print(f"📋 Using config: {DEFAULT_CONFIG_PATH}")
    elif cfg_path is not None:
        print(f"📋 Using config: {cfg_path.resolve()}")
    if not pos:
        print(f"📥 Using default digest log: {log_path}")

    run_backtest(str(log_path), strat_cfg, sim_cfg, strategy=strategy, m31_preset=m31_preset)