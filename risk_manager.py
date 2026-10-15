# risk_manager.py - Daily trading guardrails

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from account_status import load_account_status
from market_time import market_now_naive, today_date
from paper_account import get_paper_balance
from paper_fill_engine import effective_net_pnl, effective_trade_charges
from position_sizer import calculate_fund_usage_limit


HISTORY_FILE = Path("logs") / "trade_history.csv"


def _current_dashboard_balance() -> float:
    if config.PAPER_TRADE or config.TRADING_MODE == "PAPER":
        return get_paper_balance()

    status = load_account_status()
    return float(status.get("broker_balance") or status.get("available_balance") or config.PAPER_BALANCE_OVERRIDE or 0)


def _dynamic_daily_loss_limit(balance: float) -> float:
    if config.MAX_DAILY_LOSS and config.MAX_DAILY_LOSS > 0:
        return float(config.MAX_DAILY_LOSS)
    percent = getattr(config, "MAX_DAILY_LOSS_PERCENT_OF_FUND", 20)
    return float(balance) * (percent / 100)


def _dynamic_daily_profit_target(balance: float) -> float:
    if config.MAX_DAILY_PROFIT and config.MAX_DAILY_PROFIT > 0:
        return float(config.MAX_DAILY_PROFIT)
    percent = getattr(config, "MAX_DAILY_PROFIT_PERCENT_OF_FUND", 10)
    if percent and percent > 0:
        return float(balance) * (percent / 100)
    return 0.0


def _switch_to_paper_after_daily_loss() -> bool:
    if config.TRADING_MODE == "PAPER" or config.PAPER_TRADE:
        return False

    try:
        from runtime_settings import save_trading_mode

        save_trading_mode("PAPER")
        config.TRADING_MODE = "PAPER"
        config.PAPER_TRADE = True
        config.ENABLE_LIVE_AUTO = False
        return True
    except Exception:
        return False


def build_daily_risk_summary(day: str | None = None) -> dict:
    day = day or today_date().isoformat()
    entries = 0
    exits = 0
    failed = 0
    stop_losses = 0
    pnl = 0.0
    charges = 0.0

    if HISTORY_FILE.exists():
        with HISTORY_FILE.open() as csv_file:
            for row in csv.DictReader(csv_file):
                if row.get("date") != day:
                    continue
                row_mode = (row.get("mode") or "").upper()
                if config.TRADING_MODE == "PAPER":
                    if row_mode not in {"", "PAPER"}:
                        continue
                elif row_mode != config.TRADING_MODE:
                    continue

                if row.get("event") == "ENTRY":
                    entries += 1
                elif row.get("event") == "EXIT":
                    exits += 1
                    row_pnl = effective_net_pnl(row)
                    row_charges = effective_trade_charges(row)
                    pnl += row_pnl
                    charges += row_charges
                    if row_pnl < 0:
                        failed += 1
                    reason = row.get("reason") or ""
                    stop_loss_reasons = {
                        "STOP_LOSS_HIT",
                        "DYNAMIC_SAFE_SL_HIT",
                        "DYNAMIC_SOFT_LOSS_EXIT",
                        "DYNAMIC_EARLY_LOSS_EXIT",
                    }
                    if reason in stop_loss_reasons and row_pnl < 0:
                        stop_losses += 1

    if entries == 0 and exits == 0:
        try:
            from database import db_get_trades_for_date
            mode = config.TRADING_MODE
            db_trades = db_get_trades_for_date(day, "PAPER" if mode in {"", "PAPER"} else mode)
            for row in db_trades:
                entries += 1
                if row.get("status") == "CLOSED":
                    exits += 1
                    row_pnl = effective_net_pnl(row)
                    row_charges = effective_trade_charges(row)
                    pnl += row_pnl
                    charges += row_charges
                    if row_pnl < 0:
                        failed += 1
                    reason = str(row.get("exit_reason") or "").upper()
                    if ("SL" in reason or "LOSS" in reason or "STOP" in reason) and row_pnl < 0:
                        stop_losses += 1
        except Exception:
            pass

    return {
        "date": day,
        "entries": entries,
        "exits": exits,
        "failed": failed,
        "stop_losses": stop_losses,
        "charges": round(charges, 2),
        "pnl": round(pnl, 2),
    }


def check_same_underlying_sl_cooldown(option_trade: dict, signal: dict | None = None) -> tuple[bool, str]:
    cooldown_minutes = int(getattr(config, "SAME_UNDERLYING_SL_COOLDOWN_MINUTES", 0) or 0)
    flip_cooldown_minutes = int(getattr(config, "OPPOSITE_DIRECTION_FLIP_COOLDOWN_MINUTES", 10) or 10)
    cross_sl_cooldown_minutes = int(getattr(config, "CROSS_INDEX_SL_COOLDOWN_MINUTES", 5) or 0)
    wave_cooldown_minutes = int(getattr(config, "POST_PROFIT_WAVE_COOLDOWN_MINUTES", 6) or 0)
    wave_min_gain_pct = float(getattr(config, "POST_PROFIT_WAVE_MIN_GAIN_PERCENT", 9.0) or 9.0)

    if (
        cooldown_minutes <= 0
        and flip_cooldown_minutes <= 0
        and cross_sl_cooldown_minutes <= 0
        and wave_cooldown_minutes <= 0
    ):
        return True, "SL cooldown disabled."

    underlying = option_trade.get("underlying")
    option_type = option_trade.get("option_type")
    from market_time import market_now_naive, today_date
    now = market_now_naive()
    same_cutoff = now - timedelta(minutes=cooldown_minutes) if cooldown_minutes > 0 else now
    flip_cutoff = now - timedelta(minutes=flip_cooldown_minutes) if flip_cooldown_minutes > 0 else now
    cross_sl_cutoff = now - timedelta(minutes=cross_sl_cooldown_minutes) if cross_sl_cooldown_minutes > 0 else now
    wave_cutoff = now - timedelta(minutes=wave_cooldown_minutes) if wave_cooldown_minutes > 0 else now
    current_date = today_date().isoformat()

    latest_same_exit_time = None
    latest_opposite_exit_time = None
    latest_cross_sl_exit_time = None
    latest_cross_sl_underlying = None
    latest_wave_win_exit_time = None
    latest_wave_win_underlying = None

    def _process_row(row_underlying, row_type, row_pnl, row_reason, closed_at_str, entry_p=None, exit_p=None):
        nonlocal latest_same_exit_time, latest_opposite_exit_time
        nonlocal latest_cross_sl_exit_time, latest_cross_sl_underlying
        nonlocal latest_wave_win_exit_time, latest_wave_win_underlying

        try:
            exit_time = datetime.fromisoformat(str(closed_at_str))
            if exit_time.tzinfo is not None:
                exit_time = exit_time.replace(tzinfo=None)
        except (TypeError, ValueError):
            return

        pnl_val = float(row_pnl or 0)
        reason_upper = str(row_reason or "").upper()
        is_sl_loss = pnl_val < 0 and ("SL" in reason_upper or "LOSS" in reason_upper or "STOP" in reason_upper)

        if is_sl_loss:
            if row_underlying == underlying:
                if row_type == option_type:
                    if latest_same_exit_time is None or exit_time > latest_same_exit_time:
                        latest_same_exit_time = exit_time
                elif row_type:
                    if latest_opposite_exit_time is None or exit_time > latest_opposite_exit_time:
                        latest_opposite_exit_time = exit_time
            elif row_underlying and underlying and row_underlying != underlying:
                if latest_cross_sl_exit_time is None or exit_time > latest_cross_sl_exit_time:
                    latest_cross_sl_exit_time = exit_time
                    latest_cross_sl_underlying = row_underlying
            return

        # v5 Post-Profit Wave Exhaustion Check:
        # After booking a strong wave win (Target Hit or >= 9% option gain) in the same direction (CE/PE),
        # prevent jumping right back into the exhausted top of the wave within 6 minutes.
        if pnl_val > 0 and row_type == option_type and wave_cooldown_minutes > 0:
            ep = float(entry_p or 0)
            xp = float(exit_p or 0)
            gain_pct = ((xp - ep) / ep * 100.0) if ep > 0 and xp > 0 else 0.0
            is_wave_win = (
                "TARGET" in reason_upper
                or gain_pct >= wave_min_gain_pct
                or (ep <= 0 and pnl_val >= 700.0)
            )
            if is_wave_win:
                if latest_wave_win_exit_time is None or exit_time > latest_wave_win_exit_time:
                    latest_wave_win_exit_time = exit_time
                    latest_wave_win_underlying = row_underlying or underlying

    if HISTORY_FILE.exists():
        with HISTORY_FILE.open() as csv_file:
            for row in csv.DictReader(csv_file):
                if row.get("date") != current_date or row.get("event") != "EXIT":
                    continue
                _process_row(
                    row.get("underlying"),
                    row.get("option_type"),
                    row.get("pnl"),
                    row.get("reason"),
                    row.get("closed_at") or row.get("event_time"),
                    row.get("entry_price"),
                    row.get("exit_price") or row.get("current_price"),
                )

    # Also check database for today's trades when using the default production history file
    if HISTORY_FILE == Path("logs") / "trade_history.csv":
        try:
            from database import db_get_trades_for_date
            db_trades = db_get_trades_for_date(current_date)
            for row in db_trades:
                if row.get("status") != "CLOSED":
                    continue
                _process_row(
                    row.get("underlying"),
                    row.get("option_type"),
                    row.get("pnl"),
                    row.get("exit_reason"),
                    row.get("closed_at") or row.get("event_time"),
                    row.get("entry_price"),
                    row.get("exit_price") or row.get("current_price"),
                )
        except Exception:
            pass

    if latest_same_exit_time and cooldown_minutes > 0 and latest_same_exit_time >= same_cutoff:
        wait_until = latest_same_exit_time + timedelta(minutes=cooldown_minutes)
        return False, (
            f"Same {underlying} {option_type} re-entry blocked after negative SL. "
            f"Cooldown ends at {wait_until.strftime('%H:%M:%S')}."
        )

    if latest_opposite_exit_time and flip_cooldown_minutes > 0 and latest_opposite_exit_time >= flip_cutoff:
        if getattr(config, "DYNAMIC_FLIP_VALIDATION_ENABLED", True) and signal:
            quality_score = float(signal.get("TRADE_QUALITY_SCORE") or 0)
            setup_score = float(signal.get("SETUP_SCORE") or 0)
            confidence = float(option_trade.get("confidence_score") or 0)
            alignment = signal.get("INDEX_ALIGNMENT")
            if (quality_score >= 12 or confidence >= 70 or setup_score >= 14) and alignment in ("BULLISH", "BEARISH"):
                return True, (
                    f"Structural trend reversal confirmed: Opposite SL cooldown bypassed due to strong breakout "
                    f"(Quality {quality_score}, Setup {setup_score}, Alignment {alignment})."
                )

        wait_until = latest_opposite_exit_time + timedelta(minutes=flip_cooldown_minutes)
        return False, (
            f"Flip-flop re-entry on {underlying} {option_type} blocked after recent opposite SL "
            f"without structural breakout confirmation. Cooldown ends at {wait_until.strftime('%H:%M:%S')}."
        )

    if latest_cross_sl_exit_time and cross_sl_cooldown_minutes > 0 and latest_cross_sl_exit_time >= cross_sl_cutoff:
        wait_until = latest_cross_sl_exit_time + timedelta(minutes=cross_sl_cooldown_minutes)
        return False, (
            f"Cross-index SL cooldown: {underlying} {option_type} blocked after recent {latest_cross_sl_underlying} SL. "
            f"Waiting for 5m candle structure to settle until {wait_until.strftime('%H:%M:%S')}."
        )

    if latest_wave_win_exit_time and wave_cooldown_minutes > 0 and latest_wave_win_exit_time >= wave_cutoff:
        wait_until = latest_wave_win_exit_time + timedelta(minutes=wave_cooldown_minutes)
        return False, (
            f"Post-profit wave exhaustion guard: {underlying} {option_type} re-entry blocked after recent "
            f"{latest_wave_win_underlying} {option_type} wave profit. Waiting for fresh 5m pullback/support until {wait_until.strftime('%H:%M:%S')}."
        )

    return True, "No recent same-underlying negative SL."


def check_daily_risk_allowed() -> tuple[bool, str, dict]:
    summary = build_daily_risk_summary()
    balance = _current_dashboard_balance()
    daily_loss_limit = _dynamic_daily_loss_limit(balance)
    daily_profit_target = _dynamic_daily_profit_target(balance)
    summary["daily_loss_limit"] = round(daily_loss_limit, 2)
    summary["daily_profit_target"] = round(daily_profit_target, 2)
    summary["fund_usage_limit"] = round(calculate_fund_usage_limit(balance), 2)

    if config.MAX_DAILY_TRADES and summary["entries"] >= config.MAX_DAILY_TRADES:
        return False, f"Daily trade limit reached: {summary['entries']}/{config.MAX_DAILY_TRADES}.", summary

    if config.MAX_DAILY_STOP_LOSSES and summary["stop_losses"] >= config.MAX_DAILY_STOP_LOSSES:
        return False, (
            f"Daily stop-loss limit reached: "
            f"{summary['stop_losses']}/{config.MAX_DAILY_STOP_LOSSES}."
        ), summary

    if config.MAX_DAILY_FAILED_TRADES and summary["failed"] >= config.MAX_DAILY_FAILED_TRADES:
        return False, f"Daily failed-trade limit reached: {summary['failed']}/{config.MAX_DAILY_FAILED_TRADES}.", summary

    if summary["pnl"] <= -abs(daily_loss_limit):
        switched_to_paper = _switch_to_paper_after_daily_loss()
        summary["switched_to_paper"] = switched_to_paper
        action = " Switched to PAPER mode automatically." if switched_to_paper else ""
        return False, (
            f"Daily net loss limit reached: Rs {summary['pnl']} <= -Rs {round(daily_loss_limit, 2)} "
            f"({getattr(config, 'MAX_DAILY_LOSS_PERCENT_OF_FUND', 20)}% of fund).{action}"
        ), summary

    if daily_profit_target > 0 and summary["pnl"] >= daily_profit_target:
        percent = getattr(config, "MAX_DAILY_PROFIT_PERCENT_OF_FUND", 10)
        return False, (
            f"Daily profit target reached: Rs {summary['pnl']} >= Rs {round(daily_profit_target, 2)} "
            f"({percent}% of fund). Trading halted to lock and protect today's profit!"
        ), summary

    return True, "Daily risk checks passed.", summary
