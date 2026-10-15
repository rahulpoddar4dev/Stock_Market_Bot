# market_depth.py - Option bid/ask depth helpers for execution quality

import config
from order_manager import round_to_tick


def extract_market_depth(option_quote: dict, quantity: int = 0) -> dict:
    depth = option_quote.get("depth") or {}
    buy_depth = depth.get("buy") or []
    sell_depth = depth.get("sell") or []
    best_bid = _level_price(buy_depth)
    best_ask = _level_price(sell_depth)
    bid_qty = _level_quantity(buy_depth)
    ask_qty = _level_quantity(sell_depth)
    total_bid_qty_5 = _total_levels_quantity(buy_depth, max_levels=5) or bid_qty
    total_ask_qty_5 = _total_levels_quantity(sell_depth, max_levels=5) or ask_qty
    ltp = float(option_quote.get("last_price") or 0)
    spread = round(best_ask - best_bid, 2) if best_bid and best_ask else 0
    reference_price = best_ask or ltp or best_bid
    spread_percent = round((spread / reference_price) * 100, 2) if reference_price else 0
    required_top_qty = int(quantity * config.MIN_TOP_DEPTH_QUANTITY_MULTIPLIER) if quantity else 0

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "total_bid_qty_5": total_bid_qty_5,
        "total_ask_qty_5": total_ask_qty_5,
        "spread": spread,
        "spread_percent": spread_percent,
        "top_depth_required_qty": required_top_qty,
        "top_depth_sufficient": ask_qty >= required_top_qty if required_top_qty else True,
    }


def apply_market_depth_to_trade(option_trade: dict, option_quote: dict) -> dict:
    if not config.MARKET_DEPTH_ENABLED:
        return option_trade

    trade = dict(option_trade)
    quantity = int(float(trade.get("quantity") or 0))
    prev_bid_5 = int(float(trade.get("total_bid_qty_5") or trade.get("bid_qty") or 0))
    depth = extract_market_depth(option_quote, quantity)
    trade.update({
        "best_bid": depth["best_bid"],
        "best_ask": depth["best_ask"],
        "bid_qty": depth["bid_qty"],
        "ask_qty": depth["ask_qty"],
        "prev_total_bid_qty_5": prev_bid_5,
        "total_bid_qty_5": depth["total_bid_qty_5"],
        "total_ask_qty_5": depth["total_ask_qty_5"],
        "bid_ask_spread": depth["spread"],
        "bid_ask_spread_percent": depth["spread_percent"],
        "top_depth_required_qty": depth["top_depth_required_qty"],
        "top_depth_sufficient": depth["top_depth_sufficient"],
    })

    block_reasons = []
    if depth["spread_percent"] and depth["spread_percent"] > config.MAX_BID_ASK_SPREAD_PERCENT:
        block_reasons.append(
            f"Spread {depth['spread_percent']}% is above max {config.MAX_BID_ASK_SPREAD_PERCENT}%."
        )
    if not depth["top_depth_sufficient"]:
        block_reasons.append(
            f"Best ask qty {depth['ask_qty']} is below required top-depth qty {depth['top_depth_required_qty']}."
        )

    if block_reasons:
        trade["market_depth_block_reason"] = " ".join(block_reasons)

    return trade


def is_t1_continuation_strong(trade: dict, current_price: float, target_1: float) -> tuple[bool, str]:
    """
    5-Factor Orderbook + Momentum Check at Target 1:
    1. Price acceptance: current_price >= target_1 OR in 94%-99.9% near-T1 buffer with strong non-fading orderbook depth.
    2. Momentum not fading: current_price has not dropped meaningfully below last_monitor_price.
    3. Spread sanity: bid_ask_spread_percent <= 0.80% (or tier max for >350 premium options).
    4. 5-level depth support: total_bid_qty_5 >= 0.90 * total_ask_qty_5.
    5. No sudden bid collapse: total_bid_qty_5 did not drop > 45% vs prev_total_bid_qty_5.
    """
    if not target_1 or float(target_1) <= 0:
        return False, "Target 1 not configured"

    t1_val = float(target_1)
    prev_price = float(trade.get("last_monitor_price") or current_price)
    if prev_price > 0 and current_price < prev_price * 0.997:
        return False, f"Price fading at T1 (LTP Rs {round(current_price, 2)} < prev Rs {round(prev_price, 2)})"

    spread_pct = float(trade.get("bid_ask_spread_percent") or 0.0)
    base_max_spread = float(getattr(config, "T1_CONT_MAX_SPREAD_PERCENT", 0.80) or 0.80)
    high_prem_max_spread = float(getattr(config, "T1_CONT_HIGH_PREMIUM_MAX_SPREAD_PERCENT", 1.20) or 1.20)
    allowed_spread = max(base_max_spread, high_prem_max_spread) if current_price > 350.0 else base_max_spread
    if spread_pct > 0 and spread_pct > allowed_spread:
        return False, f"Spread {spread_pct}% exceeds max {allowed_spread}% at T1"

    bid_q = int(float(trade.get("total_bid_qty_5") or trade.get("bid_qty") or 0))
    ask_q = int(float(trade.get("total_ask_qty_5") or trade.get("ask_qty") or 0))
    min_ratio = float(getattr(config, "T1_CONT_MIN_BID_ASK_RATIO", 0.90) or 0.90)
    if bid_q > 0 and ask_q > 0:
        ratio = bid_q / ask_q
        if ratio < min_ratio:
            return False, f"Weak T1 orderbook (5-level bid/ask ratio {ratio:.2f} < {min_ratio})"

    prev_bid_q = int(float(trade.get("prev_total_bid_qty_5") or 0))
    max_drop = float(getattr(config, "T1_CONT_MAX_BID_DROP_RATIO", 0.45) or 0.45)
    if prev_bid_q > 0 and bid_q > 0:
        drop_ratio = (prev_bid_q - bid_q) / prev_bid_q
        if drop_ratio > max_drop:
            return False, f"Sudden bid collapse at T1 ({drop_ratio * 100:.1f}% drop > {max_drop * 100:.0f}%)"

    # If inside the 94% near-T1 buffer (< 100% T1), require confirmed positive orderbook depth and non-falling price
    if current_price < t1_val:
        if bid_q <= 0 or ask_q <= 0 or (prev_price > 0 and current_price < prev_price):
            return False, f"Near-T1 buffer stall (LTP Rs {round(current_price, 2)} < T1 Rs {round(t1_val, 2)})"
        return True, "Strong near-T1 orderbook & momentum continuation confirmed (5-factor check passed)"

    return True, "Strong T1 continuation confirmed (5-factor check passed)"


def depth_based_buy_limit_price(option_trade: dict) -> float | None:
    best_ask = float(option_trade.get("best_ask") or 0)
    if not config.USE_DEPTH_FOR_LIMIT_PRICE or not best_ask:
        return None

    buffer_percent = config.ENTRY_LIMIT_BUFFER_PERCENT / 100
    return round_to_tick(best_ask * (1 + buffer_percent))


def _level_price(levels: list[dict]) -> float:
    if not levels:
        return 0.0
    return float(levels[0].get("price") or 0)


def _level_quantity(levels: list[dict]) -> int:
    if not levels:
        return 0
    return int(float(levels[0].get("quantity") or 0))


def _total_levels_quantity(levels: list[dict], max_levels: int = 5) -> int:
    if not levels:
        return 0
    total = 0
    for lvl in levels[:max_levels]:
        if isinstance(lvl, dict):
            total += int(float(lvl.get("quantity") or 0))
    return total
