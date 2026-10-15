# order_manager.py - Place F&O orders on Fyers API v3

import config


def round_to_tick(price: float, tick_size: float | None = None) -> float:
    tick_size = tick_size or config.ORDER_PRICE_TICK_SIZE
    if not tick_size:
        return round(float(price), 2)
    return round(round(float(price) / tick_size) * tick_size, 2)


def place_order(fyers, symbol, action, quantity, exchange=None, order_type: str = "MARKET", price: float | None = None):
    """
    Place a Fyers F&O order.
    action = "BUY" or "SELL"
    order_type = "MARKET" or "LIMIT"
    """
    if config.PAPER_TRADE or config.TRADING_MODE == "PAPER":
        print("Order blocked: PAPER_TRADE/PAPER mode is enabled.")
        return None

    try:
        normalized_order_type = (order_type or "MARKET").upper()
        side = 1 if action.upper() == "BUY" else -1
        fyers_type = 1 if normalized_order_type == "LIMIT" else 2
        limit_price = round_to_tick(price) if normalized_order_type == "LIMIT" and price is not None else 0.0

        if normalized_order_type == "LIMIT" and price is None:
            raise ValueError("LIMIT order needs a price.")

        order_data = {
            "symbol": symbol,
            "qty": int(quantity),
            "type": fyers_type,
            "side": side,
            "productType": "INTRADAY",
            "limitPrice": float(limit_price),
            "stopPrice": 0.0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }

        res = fyers.place_order(order_data)
        if isinstance(res, dict) and res.get("s") == "ok":
            order_id = res.get("id")
            print(f"{normalized_order_type} order placed on Fyers! Order ID: {order_id}")
            return order_id
        else:
            msg = res.get("message") if isinstance(res, dict) else str(res)
            print(f"Fyers order rejected/failed: {msg}")
            return None

    except Exception as e:
        print(f"Order failed: {e}")
        return None


def get_order_by_id(fyers, order_id):
    if not order_id:
        return None

    try:
        res = fyers.orderbook()
        order_list = (res.get("orderBook") or []) if isinstance(res, dict) else []
        for order in order_list:
            if str(order.get("id")) == str(order_id):
                return order
    except Exception as error:
        print(f"Could not fetch Fyers order status: {error}")

    return None


def get_positions(fyers):
    """Check current open positions on Fyers"""
    try:
        res = fyers.positions()
        if isinstance(res, dict):
            return res.get("netPositions", [])
    except Exception as error:
        print(f"Could not fetch Fyers positions: {error}")
    return []


def get_balance(fyers):
    """Check available margin/balance on Fyers"""
    try:
        res = fyers.funds()
        if isinstance(res, dict):
            fund_limits = res.get("fund_limit", [])
            for item in fund_limits:
                title = str(item.get("title", "")).lower()
                if "total balance" in title or "available balance" in title or "equity" in title:
                    amt = float(item.get("equityAmount", 0.0) or item.get("availableBalance", 0.0) or 0.0)
                    print(f"Available Balance: Rs {amt}")
                    return amt
            if fund_limits:
                amt = float(fund_limits[0].get("equityAmount", 0.0) or 0.0)
                print(f"Available Balance: Rs {amt}")
                return amt
    except Exception as error:
        print(f"Could not fetch Fyers funds: {error}")

    print("Available Balance: Rs 0.0")
    return 0.0
