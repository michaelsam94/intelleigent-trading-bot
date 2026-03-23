"""
Gemini API helper for TA-driven trade decisions. Optional dependency: google-generativeai.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any


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


def build_user_prompt(symbol: str, ta_digest_text: str, tf_scores: list[float], tf_labels: list[str], mean_score: float) -> str:
    scores_line = ", ".join(f"{s:+.3f}" for s in tf_scores) if tf_scores else "n/a"
    labels_line = ", ".join(tf_labels) if tf_labels else "n/a"
    return f"""=== Multi-timeframe technical analysis (computed) ===

{ta_digest_text}

=== Numeric summary ===
Timeframe scores (order: 5m,15m,30m,1h,1d,1w,1M): [{scores_line}]
Timeframe labels: {labels_line}
Mean score: {mean_score:+.4f}
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

    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError("Install google-generativeai: pip install google-generativeai") from None

    model_name = (os.environ.get("GEMINI_MODEL") or "gemini-1.5-flash").strip() or "gemini-1.5-flash"
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_instruction or "You output only valid JSON.",
    )

    try:
        resp = model.generate_content(
            full_user_prompt,
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 1024,
            },
        )
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}") from e

    text = (resp.text or "").strip()
    if not text:
        return None
    parsed = parse_gemini_trade_json(text)
    return parsed


def run_gemini_decision(
    symbol: str,
    quote_close: float,
    ta_digest_text: str,
    tf_scores: list[float],
    tf_labels: list[str],
    mean_score: float,
) -> dict[str, Any] | None:
    """Full prompt + Gemini call; returns parsed trade dict or None."""
    sys_p = build_system_prompt(symbol, quote_close)
    usr_p = build_user_prompt(symbol, ta_digest_text, tf_scores, tf_labels, mean_score)
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
