# paper_account.py - Rolling paper account balance from closed trade P&L

import csv
import json
from pathlib import Path

import config
from log_utils import csv_datetime
from paper_fill_engine import effective_net_pnl, effective_trade_charges


ACCOUNT_FILE = Path("paper_account.json")
HISTORY_FILE = Path("logs") / "trade_history.csv"


def starting_balance() -> float:
    return float(config.PAPER_BALANCE_OVERRIDE or 0)


def _csv_pnl_and_charges() -> tuple[float, float, float]:
    if not HISTORY_FILE.exists():
        return 0.0, 0.0, 0.0

    net_total = 0.0
    gross_total = 0.0
    charges_total = 0.0
    with HISTORY_FILE.open() as csv_file:
        for row in csv.DictReader(csv_file):
            if row.get("event") == "EXIT":
                net_pnl = effective_net_pnl(row)
                charges = effective_trade_charges(row)
                gross_val = row.get("gross_pnl")
                gross_pnl = float(gross_val) if gross_val not in {None, ""} else round(net_pnl + charges, 2)
                net_total += net_pnl
                gross_total += gross_pnl
                charges_total += charges

    return round(net_total, 2), round(gross_total, 2), round(charges_total, 2)


def realized_pnl() -> float:
    csv_net, _, _ = _csv_pnl_and_charges()
    if HISTORY_FILE.exists() and csv_net != 0.0:
        return csv_net

    try:
        from database import db_get_pnl_summary, get_bot_env
        mode = getattr(config, "TRADING_MODE", "PAPER")
        db_pnl = db_get_pnl_summary("PAPER" if mode in {"", "PAPER"} else mode)
        if db_pnl != 0.0 or get_bot_env() != "main":
            return db_pnl
    except Exception:
        pass

    return csv_net


def build_paper_account() -> dict:
    initial = starting_balance()
    csv_net, csv_gross, csv_charges = _csv_pnl_and_charges()
    pnl = realized_pnl()
    return {
        "updated_at": csv_datetime(),
        "starting_balance": round(initial, 2),
        "gross_realized_pnl": csv_gross if csv_gross != 0.0 else round(pnl + csv_charges, 2),
        "total_charges": csv_charges,
        "realized_pnl": pnl,
        "available_balance": round(initial + pnl, 2),
    }


def refresh_paper_account() -> dict:
    account = build_paper_account()
    ACCOUNT_FILE.write_text(json.dumps(account, indent=2))
    return account


def get_paper_balance() -> float:
    return float(refresh_paper_account()["available_balance"])
