# account_status.py - Persist current account funds for dashboard display

import json
from pathlib import Path

from log_utils import csv_datetime


STATUS_FILE = Path(__file__).resolve().parent / "account_status.json"


def save_account_status(
    balance: float,
    profile: dict | None = None,
    active_balance: float | None = None,
    paper_balance: float | None = None,
    mode: str | None = None,
) -> dict:
    existing = load_account_status()
    status = {
        **existing,
        "updated_at": csv_datetime(),
        "available_balance": float(balance),
        "broker_balance": float(balance),
    }
    if active_balance is not None:
        status["active_balance"] = float(active_balance)
    if paper_balance is not None:
        status["paper_balance"] = float(paper_balance)
    if mode:
        status["mode"] = mode

    if profile:
        status.update({
            "user_name": profile.get("user_name"),
            "user_id": profile.get("user_id"),
            "email": profile.get("email"),
            "broker": profile.get("broker"),
        })

    STATUS_FILE.write_text(json.dumps(status, indent=2))
    return status


def load_account_status() -> dict:
    if not STATUS_FILE.exists():
        return {
            "updated_at": None,
            "available_balance": 0,
        }

    try:
        return json.loads(STATUS_FILE.read_text())
    except json.JSONDecodeError:
        return {
            "updated_at": None,
            "available_balance": 0,
        }


def clear_account_status() -> None:
    status = {
        "updated_at": None,
        "available_balance": 0,
        "broker_balance": 0,
        "user_name": None,
        "user_id": None,
        "email": None,
        "broker": "Fyers",
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2))
