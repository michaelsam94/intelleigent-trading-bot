"""
Gemini API helper for TA-driven trade decisions. Optional dependency: google-generativeai.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any


def _to_float_or_none(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _strip_json_from_response(text: str) -> str | None:
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if m:
            return m.group(1).strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    return m.group(0).strip() if m else None


def build_system_prompt(symbol: str, quote_close: float) -> str:
    use_master = os.environ.get("TA_GEMINI_MASTER_PROMPT", "0").strip().lower() in ("1", "true", "yes", "on")
    if use_master:
        return f"""Role: You are a Senior Crypto Quantitative Analyst specializing in Order Flow and Multi-Timeframe Technical Analysis.
Asset: {symbol}. Latest 5m reference close: {quote_close:,.6f}.

Objective:
- Perform confluence check across multi-timeframe TA + external market context.
- Produce a high-probability directional setup with practical execution levels.

Important:
- If live external data cannot be reliably confirmed, be explicit in rationale and reduce conviction.
- Always prioritize risk control and clear invalidation.

You MUST respond with ONLY a single JSON object (no markdown, no extra text).
Schema:
{{
  "action": "LONG" | "SHORT" | "HOLD",
  "direction": "Long" | "Short" | "Neutral",
  "conviction_score": <integer 1-10>,
  "entry_low": <number or null>,
  "entry_high": <number or null>,
  "take_profit": <number or null>,
  "stop_loss": <number or null>,
  "tp1": <number or null>,
  "tp2": <number or null>,
  "risk_warning": "<string>",
  "invalidation_point": "<string>",
  "confidence": <integer 0-100>,
  "rationale": "<short explanation>"
}}

Rules:
- LONG => stop_loss < reference close and take_profit > reference close.
- SHORT => take_profit < reference close and stop_loss > reference close.
- HOLD => set price fields to null where applicable.
- Keep numbers realistic and in quote units.
- Ensure risk/reward is reasonable and avoid overconfident outputs in conflicting conditions."""

    return f"""You are an expert cryptocurrency technical analyst and risk manager.
Symbol: {symbol} (spot). Latest reference close (5m): {quote_close:,.6f}.

You MUST respond with ONLY a single JSON object (no markdown fences, no commentary outside JSON).
Schema:
{{
  "action": "LONG" | "SHORT" | "HOLD",
  "take_profit": <number or null>,
  "stop_loss": <number or null>,
  "confidence": <integer 0-100>,
  "rationale": "<one or two sentences>"
}}

Rules:
- LONG: you expect price to rise; stop_loss must be BELOW the reference close; take_profit ABOVE the reference close.
- SHORT: you expect price to fall; take_profit must be BELOW the reference close; stop_loss ABOVE the reference close.
- HOLD: no clear edge; set take_profit and stop_loss to null.
- Use realistic absolute prices (same units as the quote). Risk/reward at least 1:1.2 when action is LONG or SHORT.
- If data is conflicting or choppy, prefer HOLD."""


def build_user_prompt(
    symbol: str,
    ta_digest_text: str,
    tf_scores: list[float],
    tf_labels: list[str],
    aggregate_score: float,
    *,
    aggregate_score_label: str = "Mean score",
) -> str:
    scores_line = ", ".join(f"{s:+.3f}" for s in tf_scores) if tf_scores else "n/a"
    labels_line = ", ".join(tf_labels) if tf_labels else "n/a"
    use_master = os.environ.get("TA_GEMINI_MASTER_PROMPT", "0").strip().lower() in ("1", "true", "yes", "on")
    if use_master:
        return f"""[Master Technical Analysis Prompt Execution]

Step 1: TA Breakdown
- Trend alignment (5m, 15m, 1h, Daily): phase vs conflict
- Overbought/oversold cross-check using RSI and WilliamsR context from digest
- Volatility assessment using ATR for stop calibration

Step 2: External data integration (attempt current context synthesis)
- Order book depth / liquidity pockets
- Liquidation clusters / heatmap-style liquidity zones
- Sentiment and recent news context

If external live context is uncertain or stale, state that clearly in rationale and lower conviction.

Provided TA Digest:
{ta_digest_text}

Numeric summary:
- Timeframe scores (5m,15m,30m,1h,1d,1w,1M): [{scores_line}]
- Timeframe labels: {labels_line}
- {aggregate_score_label}: {aggregate_score:+.4f}
- Symbol: {symbol}

Output strictly as JSON using the required schema."""

    return f"""=== Multi-timeframe technical analysis (computed) ===

{ta_digest_text}

=== Numeric summary ===
Timeframe scores (order: 5m,15m,30m,1h,1d,1w,1M): [{scores_line}]
Timeframe labels: {labels_line}
{aggregate_score_label}: {aggregate_score:+.4f}
Symbol: {symbol}

Based on ALL of the above, output the JSON decision."""


def parse_gemini_trade_json(raw: str) -> dict[str, Any] | None:
    s = _strip_json_from_response(raw)
    if not s:
        return None
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    action = str(data.get("action", "HOLD")).upper().strip()
    if action not in ("LONG", "SHORT", "HOLD"):
        action = "HOLD"
    tp = data.get("take_profit")
    sl = data.get("stop_loss")
    try:
        tp_f = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        tp_f = None
    try:
        sl_f = float(sl) if sl is not None else None
    except (TypeError, ValueError):
        sl_f = None
    return {
        "action": action,
        "take_profit": tp_f,
        "stop_loss": sl_f,
        "confidence": int(data.get("confidence", 0) or 0),
        "rationale": str(data.get("rationale", "") or ""),
        "direction": str(data.get("direction", "") or ""),
        "conviction_score": int(data.get("conviction_score", 0) or 0),
        "entry_low": _to_float_or_none(data.get("entry_low")),
        "entry_high": _to_float_or_none(data.get("entry_high")),
        "tp1": _to_float_or_none(data.get("tp1")),
        "tp2": _to_float_or_none(data.get("tp2")),
        "risk_warning": str(data.get("risk_warning", "") or ""),
        "invalidation_point": str(data.get("invalidation_point", "") or ""),
    }


def gemini_trade_decision(
    full_user_prompt: str,
    *,
    system_instruction: str | None = None,
) -> dict[str, Any] | None:
    """
    Call Gemini and return parsed trade dict or None on failure.
    """
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None
    timeout_s = float(os.environ.get("TA_GEMINI_TIMEOUT_SEC", "20") or 20.0)
    model_name = (os.environ.get("GEMINI_MODEL") or "gemini-1.5-flash").strip() or "gemini-1.5-flash"

    def _run_with_timeout(fn):
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(fn)
            try:
                return fut.result(timeout=timeout_s)
            except FuturesTimeoutError as e:
                raise RuntimeError(f"Gemini request timed out after {timeout_s:.1f}s") from e

    # Prefer new SDK, then fall back to deprecated SDK for compatibility.
    try:
        from google import genai as genai_new  # type: ignore

        def _call_new_sdk():
            client = genai_new.Client(api_key=api_key)
            cfg = {
                "temperature": 0.2,
                "max_output_tokens": 1024,
            }
            if system_instruction:
                cfg["system_instruction"] = system_instruction
            resp = client.models.generate_content(
                model=model_name,
                contents=full_user_prompt,
                config=cfg,
            )
            return (getattr(resp, "text", "") or "").strip()

        text = _run_with_timeout(_call_new_sdk)
        if text:
            parsed = parse_gemini_trade_json(text)
            if parsed is not None:
                return parsed
    except Exception:
        pass

    try:
        import google.generativeai as genai  # type: ignore
    except ImportError:
        raise RuntimeError("Install google-genai (preferred) or google-generativeai") from None

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name, system_instruction=system_instruction or "You output only valid JSON.")
    try:
        def _call_old_sdk():
            resp = model.generate_content(
                full_user_prompt,
                generation_config={
                    "temperature": 0.2,
                    "max_output_tokens": 1024,
                },
            )
            return (resp.text or "").strip()
        text = _run_with_timeout(_call_old_sdk)
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}") from e

    if not text:
        return None
    return parse_gemini_trade_json(text)


def run_gemini_decision(
    symbol: str,
    quote_close: float,
    ta_digest_text: str,
    tf_scores: list[float],
    tf_labels: list[str],
    aggregate_score: float,
    *,
    aggregate_score_label: str = "Mean score",
) -> dict[str, Any] | None:
    """Full prompt + Gemini call; returns parsed trade dict or None."""
    sys_p = build_system_prompt(symbol, quote_close)
    usr_p = build_user_prompt(
        symbol,
        ta_digest_text,
        tf_scores,
        tf_labels,
        aggregate_score,
        aggregate_score_label=aggregate_score_label,
    )
    return gemini_trade_decision(usr_p, system_instruction=sys_p)


def validate_tp_sl(
    action: str,
    entry: float,
    tp: float | None,
    sl: float | None,
) -> tuple[float | None, float | None]:
    """Return validated (tp, sl) or (None, None) if invalid."""
    if action == "HOLD" or tp is None or sl is None:
        return None, None
    if action == "LONG":
        if not (sl < entry < tp):
            return None, None
    elif action == "SHORT":
        if not (tp < entry < sl):
            return None, None
    else:
        return None, None
    return tp, sl
