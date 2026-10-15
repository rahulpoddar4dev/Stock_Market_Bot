# loss_reason_analyzer.py - Explain why a losing trade likely failed

from __future__ import annotations


LOSS_REASON_PRIORITY = [
    "LOW_LIQUIDITY",
    "SPREAD_LOSS",
    "OPTION_OVEREXTENDED",
    "LATE_ENTRY",
    "WRONG_STRIKE",
    "SIDEWAYS_MARKET",
    "NEWS_REVERSAL",
    "BAD_EXIT",
]


def analyze_loss_reason(trade: dict) -> dict:
    pnl = _to_float(trade.get("pnl"))
    if pnl >= 0:
        return {
            "loss_reason": "",
            "loss_reason_detail": "Trade was not a loss.",
            "loss_reason_tags": "",
        }

    tags = []
    details = []

    if _to_float(trade.get("bid_ask_spread_percent")) > 1:
        tags.append("SPREAD_LOSS")
        details.append(f"Spread was high: {trade.get('bid_ask_spread_percent')}%.")

    if str(trade.get("top_depth_sufficient")).lower() == "false" or _to_float(trade.get("ask_qty")) <= 0:
        tags.append("LOW_LIQUIDITY")
        details.append("Top depth/liquidity was weak.")

    if _to_float(trade.get("potential_left_percent")) < 8 and trade.get("potential_left_percent") not in {None, ""}:
        tags.append("OPTION_OVEREXTENDED")
        details.append(f"Potential left was low: {trade.get('potential_left_percent')}%.")

    entry_mode = str(trade.get("entry_mode") or trade.get("entry_logic") or "")
    if "OLD" in entry_mode or "CONTINUATION" in entry_mode:
        tags.append("LATE_ENTRY")
        details.append(f"Entry mode was {entry_mode}.")

    if _is_far_otm_or_wrong_strike(trade):
        tags.append("WRONG_STRIKE")
        details.append("Strike looked far from preferred ATM/ITM zone.")

    regime = str(trade.get("market_regime") or "")
    if regime in {"SIDEWAYS", "LOW_VOLATILITY"}:
        tags.append("SIDEWAYS_MARKET")
        details.append(f"Market regime was {regime}.")

    if str(trade.get("news_conflict")).lower() == "true" or trade.get("news_risk_level") == "HIGH":
        tags.append("NEWS_REVERSAL")
        details.append("News/context conflicted or risk was high.")

    exit_reason = str(trade.get("exit_reason") or trade.get("reason") or "")
    if exit_reason in {"MARKET_CLOSE_SQUARE_OFF", "DYNAMIC_TIME_EXIT"}:
        tags.append("BAD_EXIT")
        details.append(f"Exit reason was {exit_reason}; trade did not move as expected.")

    if not tags:
        tags.append("BAD_EXIT")
        details.append("Loss reason was unclear; review entry/exit manually.")

    primary = _primary_tag(tags)
    return {
        "loss_reason": primary,
        "loss_reason_detail": " ".join(details),
        "loss_reason_tags": " | ".join(dict.fromkeys(tags)),
    }


def _primary_tag(tags: list[str]) -> str:
    for reason in LOSS_REASON_PRIORITY:
        if reason in tags:
            return reason
    return tags[0] if tags else ""


def _is_far_otm_or_wrong_strike(trade: dict) -> bool:
    steps = trade.get("steps_from_atm")
    if steps not in {None, ""}:
        return abs(_to_float(steps)) > 1
    selection = str(trade.get("selection_mode") or "")
    return "OTM" in selection.upper()


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
