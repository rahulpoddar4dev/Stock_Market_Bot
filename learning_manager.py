# learning_manager.py - Learn from recent trades and adapt the next setup

import csv
from datetime import date, datetime, time, timedelta
from pathlib import Path

import config
from log_utils import csv_date, csv_datetime, csv_time
from market_time import market_now_naive, today_date


HISTORY_FILE = Path("logs") / "trade_history.csv"
LEARNING_LOG_FILE = Path("logs") / "learning_history.csv"
DATEWISE_DIR = Path("logs") / "by_date"

LEARNING_FIELDNAMES = [
    "date",
    "time",
    "event_time",
    "underlying",
    "option_symbol",
    "display_symbol",
    "option_type",
    "exit_profile",
    "mistake_type",
    "matched_pattern",
    "lookback_days",
    "recent_losses",
    "recent_wins",
    "recent_trade_refs",
    "old_behavior",
    "new_behavior",
    "action_taken",
    "reason",
]


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _parse_row_time(row: dict) -> datetime | None:
    opened = _parse_datetime(row.get("opened_at"))
    if opened:
        return opened

    event_time = _parse_datetime(row.get("event_time"))
    if event_time:
        return event_time

    row_date = _parse_date(row.get("date"))
    row_time = row.get("time")
    if not row_date or not row_time:
        return None

    try:
        return datetime.combine(row_date, time.fromisoformat(row_time[:8]))
    except ValueError:
        return None


def _read_recent_exits() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []

    cutoff = today_date() - timedelta(days=config.LEARNING_LOOKBACK_DAYS)
    rows = []
    with HISTORY_FILE.open() as csv_file:
        for row in csv.DictReader(csv_file):
            if row.get("event") != "EXIT":
                continue
            row_date = _parse_date(row.get("date"))
            if not row_date or row_date < cutoff:
                continue
            rows.append(row)

    return rows


def _wins_losses(rows: list[dict]) -> tuple[int, int]:
    wins = 0
    losses = 0
    for row in rows:
        pnl = _to_float(row.get("pnl"))
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    return wins, losses


def _trade_refs(rows: list[dict], limit: int = 5) -> str:
    refs = []
    for row in rows[-limit:]:
        refs.append(
            f"{row.get('date')} {row.get('time')} {row.get('display_symbol') or row.get('option_symbol')} "
            f"pnl={row.get('pnl')} reason={row.get('reason')} stage={row.get('target_stage')}"
        )
    return " | ".join(refs)


def _append_learning_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if path.exists():
        with path.open() as csv_file:
            rows = list(csv.DictReader(csv_file))

    fieldnames = list(LEARNING_FIELDNAMES)
    for existing in rows:
        for fieldname in existing.keys():
            if fieldname and fieldname not in fieldnames:
                fieldnames.append(fieldname)

    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for existing in rows:
            writer.writerow(existing)
        writer.writerow(row)


def _record_learning(option_trade: dict, event: dict) -> None:
    row = {
        "date": csv_date(),
        "time": csv_time(),
        "event_time": csv_datetime(),
        "underlying": option_trade.get("underlying"),
        "option_symbol": option_trade.get("option_symbol"),
        "display_symbol": option_trade.get("display_symbol"),
        "option_type": option_trade.get("option_type"),
        "exit_profile": option_trade.get("exit_profile"),
        "lookback_days": config.LEARNING_LOOKBACK_DAYS,
        **event,
    }
    _append_learning_row(LEARNING_LOG_FILE, row)
    _append_learning_row(DATEWISE_DIR / row["date"] / "learning_history.csv", row)


def _near_current_time(rows: list[dict], now: datetime) -> list[dict]:
    window = timedelta(minutes=config.LEARNING_TIME_WINDOW_MINUTES)
    current_minutes = now.hour * 60 + now.minute
    matches = []

    for row in rows:
        row_dt = _parse_row_time(row)
        if not row_dt:
            continue
        row_minutes = row_dt.hour * 60 + row_dt.minute
        if abs(row_minutes - current_minutes) <= window.total_seconds() / 60:
            matches.append(row)

    return matches


def _learning_reason(label: str, losses: int, wins: int) -> str:
    return (
        f"Learning guard found {losses} loss(es) "
        f"and {wins} win(s) for {label} in the last {config.LEARNING_LOOKBACK_DAYS} day(s)."
    )


def _same_setup_rows(recent_exits: list[dict], option_trade: dict) -> list[dict]:
    underlying = option_trade.get("underlying")
    option_type = option_trade.get("option_type")
    exit_profile = option_trade.get("exit_profile")
    return [
        row
        for row in recent_exits
        if row.get("underlying") == underlying
        and row.get("option_type") == option_type
        and (not exit_profile or not row.get("exit_profile") or row.get("exit_profile") == exit_profile)
    ]


def _reduce_trade_size(option_trade: dict, size_percent: float, reason: str) -> dict:
    adjusted = dict(option_trade)
    lot_size = int(adjusted.get("lot_size") or 1)
    base_lots = int(adjusted.get("base_lots") or adjusted.get("lots") or (int(float(adjusted.get("quantity") or 0)) // lot_size))
    prev_percent = float(adjusted.get("effective_size_percent") or 100.0)
    effective_percent = min(prev_percent, float(size_percent))
    adjusted_lots = max(1, round(base_lots * (effective_percent / 100)))
    adjusted["base_lots"] = base_lots
    adjusted["effective_size_percent"] = effective_percent
    adjusted["lots"] = adjusted_lots
    adjusted["quantity"] = adjusted_lots * lot_size
    entry_price = _to_float(adjusted.get("entry_price"))
    stop_loss = _to_float(adjusted.get("stop_loss"))
    adjusted["estimated_required_amount"] = round(entry_price * adjusted["quantity"], 2)
    adjusted["estimated_risk_amount"] = round(max(entry_price - stop_loss, 0) * adjusted["quantity"], 2)
    adjusted["learning_size_reduction_percent"] = effective_percent
    adjusted["learning_adjustment_reason"] = reason
    return adjusted


def _block_trade(option_trade: dict, reason: str) -> dict:
    adjusted = dict(option_trade)
    adjusted["status"] = "BLOCKED_LEARNING_GUARD"
    adjusted["lots"] = 0
    adjusted["quantity"] = 0
    adjusted["remaining_quantity"] = 0
    adjusted["estimated_required_amount"] = 0
    adjusted["estimated_risk_amount"] = 0
    adjusted["risk_block_reason"] = reason
    adjusted["sizing_reason"] = reason
    adjusted["learning_adjustment_reason"] = reason
    return adjusted


def _add_score_penalty(option_trade: dict, penalty: float, reason: str) -> dict:
    adjusted = dict(option_trade)
    current_penalty = _to_float(adjusted.get("learning_score_penalty"))
    total_penalty = round(current_penalty + float(penalty), 2)
    adjusted["learning_score_penalty"] = total_penalty
    previous_reason = adjusted.get("learning_penalty_reason") or adjusted.get("learning_adjustment_reason") or ""
    adjusted["learning_penalty_reason"] = f"{previous_reason} {reason}".strip()
    adjusted["learning_adjustment_reason"] = adjusted["learning_penalty_reason"]

    for key in ["confidence_score", "option_chain_score", "trade_quality_score"]:
        if adjusted.get(key) not in {None, ""}:
            adjusted[key] = max(0, round(_to_float(adjusted.get(key)) - float(penalty), 2))

    return adjusted


def _penalize_or_reduce(option_trade: dict, penalty: float, reason: str) -> dict:
    if config.LEARNING_USE_SCORE_PENALTIES:
        adjusted = _add_score_penalty(option_trade, penalty, reason)
        score = max(
            _to_float(adjusted.get("confidence_score")),
            _to_float(adjusted.get("option_chain_score")),
            _to_float(adjusted.get("trade_quality_score")),
        )
        if score >= float(config.LEARNING_STRONG_SETUP_OVERRIDE_SCORE):
            adjusted["learning_size_reduction_percent"] = 100
            return adjusted
        return _reduce_trade_size(adjusted, config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT, reason)

    return _reduce_trade_size(option_trade, config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT, reason)


def _move_final_target_to_t2(option_trade: dict, reason: str) -> dict:
    adjusted = dict(option_trade)
    target_2 = adjusted.get("target_2")
    if not target_2:
        return adjusted

    if not getattr(config, "FULL_POSITION_RIDE_ENABLED", True):
        adjusted["target_3"] = target_2
        adjusted["target_3_percent"] = adjusted.get("target_2_percent")
    adjusted["target"] = adjusted.get("target_3") or target_2
    adjusted["target_percent"] = adjusted.get("target_3_percent") or adjusted.get("target_2_percent")
    adjusted["learning_exit_mode"] = "EXIT_AT_TARGET_2"
    adjusted["learning_adjustment_reason"] = reason
    adjusted["exit_profile_reason"] = (
        f"{adjusted.get('exit_profile_reason', '')} Learning adjustment: {reason}"
    ).strip()
    return adjusted


def apply_learning_adjustments(option_trade: dict, signal: dict) -> tuple[dict, list[str]]:
    if not config.LEARNING_GUARD_ENABLED:
        return option_trade, []

    recent_exits = _read_recent_exits()
    if not recent_exits:
        return option_trade, []

    adjusted = dict(option_trade)
    reasons = []
    news_conflict = _truthy(signal.get("NEWS_CONFLICT"))
    same_setup = _same_setup_rows(recent_exits, option_trade)

    current_entry_mode = signal.get("ENTRY_MODE") or option_trade.get("entry_mode") or option_trade.get("entry_logic")
    if current_entry_mode:
        same_entry_mode = [row for row in recent_exits if row.get("entry_mode") == current_entry_mode or row.get("entry_logic") == current_entry_mode]
        wins, losses = _wins_losses(same_entry_mode)
        if losses >= config.LEARNING_ENTRY_MODE_LOSS_LIMIT and wins < config.LEARNING_MIN_RECENT_PATTERN_WINS:
            reason = (
                f"{_learning_reason('entry mode ' + str(current_entry_mode), losses, wins)} "
                f"Size reduced to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}%."
            )
            adjusted = _penalize_or_reduce(adjusted, config.LEARNING_LOSS_PATTERN_SCORE_PENALTY, reason)
            reasons.append(reason)
            _record_learning(option_trade, {
                "mistake_type": "REPEATED_ENTRY_MODE_LOSS",
                "matched_pattern": str(current_entry_mode),
                "recent_losses": losses,
                "recent_wins": wins,
                "recent_trade_refs": _trade_refs(same_entry_mode),
                "old_behavior": "Use normal size for this entry mode.",
                "new_behavior": f"Reduce size to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}%.",
                "action_taken": "REDUCE_SIZE",
                "reason": reason,
            })

    current_regime = signal.get("MARKET_REGIME") or option_trade.get("market_regime")
    if current_regime:
        same_regime = [row for row in recent_exits if row.get("market_regime") == current_regime]
        wins, losses = _wins_losses(same_regime)
        if losses >= config.LEARNING_MARKET_REGIME_LOSS_LIMIT and wins < config.LEARNING_MIN_RECENT_PATTERN_WINS:
            reason = (
                f"{_learning_reason('market regime ' + str(current_regime), losses, wins)} "
                f"Size reduced to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}%."
            )
            adjusted = _penalize_or_reduce(adjusted, config.LEARNING_LOSS_PATTERN_SCORE_PENALTY, reason)
            reasons.append(reason)
            _record_learning(option_trade, {
                "mistake_type": "REPEATED_MARKET_REGIME_LOSS",
                "matched_pattern": str(current_regime),
                "recent_losses": losses,
                "recent_wins": wins,
                "recent_trade_refs": _trade_refs(same_regime),
                "old_behavior": "Use normal size in this regime.",
                "new_behavior": f"Reduce size to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}%.",
                "action_taken": "REDUCE_SIZE",
                "reason": reason,
            })

    high_impact_loss_reasons = {"LATE_ENTRY", "OPTION_OVEREXTENDED", "WRONG_STRIKE", "LOW_LIQUIDITY", "SIDEWAYS_MARKET", "SPREAD_LOSS"}
    repeated_loss_rows = [
        row
        for row in recent_exits
        if row.get("loss_reason") in high_impact_loss_reasons
        and row.get("underlying") == option_trade.get("underlying")
    ]
    if repeated_loss_rows:
        loss_reason_counts = {}
        for row in repeated_loss_rows:
            key = row.get("loss_reason")
            loss_reason_counts[key] = loss_reason_counts.get(key, 0) + 1
        worst_reason, count = sorted(loss_reason_counts.items(), key=lambda item: item[1], reverse=True)[0]
        if count >= config.LEARNING_LOSS_REASON_LIMIT:
            reason = (
                f"Repeated loss reason {worst_reason} appeared {count} time(s) recently for "
                f"{option_trade.get('underlying')}. Size reduced to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}%."
            )
            adjusted = _penalize_or_reduce(adjusted, config.LEARNING_LOSS_PATTERN_SCORE_PENALTY, reason)
            reasons.append(reason)
            _record_learning(option_trade, {
                "mistake_type": f"REPEATED_{worst_reason}",
                "matched_pattern": worst_reason,
                "recent_losses": count,
                "recent_wins": 0,
                "recent_trade_refs": _trade_refs([row for row in repeated_loss_rows if row.get("loss_reason") == worst_reason]),
                "old_behavior": "Use normal size after this loss reason.",
                "new_behavior": f"Reduce size to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}%.",
                "action_taken": "REDUCE_SIZE",
                "reason": reason,
            })

    if config.LEARNING_ADAPTIVE_EXIT_ENABLED:
        t2_then_sl = [
            row
            for row in same_setup
            if int(_to_float(row.get("target_stage"))) >= 2
            and row.get("reason") == "STOP_LOSS_HIT"
            and _to_float(row.get("target_2")) > 0
        ]
        if len(t2_then_sl) >= config.LEARNING_T2_MISSED_EXIT_LOOKBACK:
            reason = (
                "Recent similar trade reached Target 2 but later closed by stop loss, "
                "so next similar trade will finish at Target 2 instead of waiting for Target 3."
            )
            adjusted = _move_final_target_to_t2(adjusted, reason)
            reasons.append(reason)
            _record_learning(option_trade, {
                "mistake_type": "T2_THEN_STOP_LOSS",
                "matched_pattern": f"{option_trade.get('underlying')} {option_trade.get('option_type')} {option_trade.get('exit_profile')}",
                "recent_losses": len(t2_then_sl),
                "recent_wins": 0,
                "recent_trade_refs": _trade_refs(t2_then_sl),
                "old_behavior": "Wait for Target 3 after Target 2.",
                "new_behavior": "Use Target 2 as final exit for next similar setup.",
                "action_taken": "EXIT_AT_TARGET_2",
                "reason": reason,
            })

    same_pattern = [
        row
        for row in recent_exits
        if row.get("underlying") == option_trade.get("underlying")
        and row.get("option_type") == option_trade.get("option_type")
    ]
    wins, losses = _wins_losses(same_pattern)
    if (
        losses >= config.LEARNING_PATTERN_LOSS_LIMIT
        and wins < config.LEARNING_MIN_RECENT_PATTERN_WINS
    ):
        label = f"{option_trade.get('underlying')} {option_trade.get('option_type')}"
        if config.LEARNING_BLOCK_REPEATED_LOSS_PATTERNS and not config.LEARNING_USE_SCORE_PENALTIES:
            reason = (
                f"{_learning_reason(label, losses, wins)} "
                "Trade blocked because the same index/direction is repeatedly losing."
            )
            adjusted = _block_trade(adjusted, reason)
            action_taken = "BLOCK_TRADE"
            new_behavior = "Block the repeated losing setup."
        else:
            reason = (
                f"{_learning_reason(label, losses, wins)} "
                f"Learning penalty applied instead of hard blocking."
            )
            adjusted = _penalize_or_reduce(adjusted, config.LEARNING_LOSS_PATTERN_SCORE_PENALTY, reason)
            action_taken = "SCORE_PENALTY"
            new_behavior = f"Apply -{config.LEARNING_LOSS_PATTERN_SCORE_PENALTY} score penalty and reduce size if needed."
        reasons.append(reason)
        _record_learning(option_trade, {
            "mistake_type": "REPEATED_UNDERLYING_DIRECTION_LOSS",
            "matched_pattern": label,
            "recent_losses": losses,
            "recent_wins": wins,
            "recent_trade_refs": _trade_refs(same_pattern),
            "old_behavior": "Use normal position size.",
            "new_behavior": new_behavior,
            "action_taken": action_taken,
            "reason": reason,
        })

    same_time = _near_current_time(recent_exits, market_now_naive())
    wins, losses = _wins_losses(same_time)
    if (
        losses >= config.LEARNING_TIME_WINDOW_LOSS_LIMIT
        and wins < config.LEARNING_MIN_RECENT_PATTERN_WINS
    ):
        label = f"same entry time window (+/- {config.LEARNING_TIME_WINDOW_MINUTES} min)"
        if config.LEARNING_BLOCK_REPEATED_LOSS_PATTERNS and not config.LEARNING_USE_SCORE_PENALTIES:
            reason = (
                f"{_learning_reason(label, losses, wins)} "
                "Trade blocked because this time window is repeatedly losing."
            )
            adjusted = _block_trade(adjusted, reason)
            action_taken = "BLOCK_TRADE"
            new_behavior = "Block entries in the repeated losing time window."
        else:
            reason = (
                f"{_learning_reason(label, losses, wins)} "
                "Bad time-window score penalty applied instead of hard blocking."
            )
            adjusted = _add_score_penalty(adjusted, config.LEARNING_BAD_TIME_WINDOW_SCORE_PENALTY, reason)
            action_taken = "SCORE_PENALTY"
            new_behavior = f"Apply -{config.LEARNING_BAD_TIME_WINDOW_SCORE_PENALTY} score penalty for this time window."
        reasons.append(reason)
        _record_learning(option_trade, {
            "mistake_type": "REPEATED_TIME_WINDOW_LOSS",
            "matched_pattern": label,
            "recent_losses": losses,
            "recent_wins": wins,
            "recent_trade_refs": _trade_refs(same_time),
            "old_behavior": "Wait for final target in this time window.",
            "new_behavior": new_behavior,
            "action_taken": action_taken,
            "reason": reason,
        })

    if option_trade.get("exit_profile"):
        same_profile = [row for row in recent_exits if row.get("exit_profile") == option_trade.get("exit_profile")]
        wins, losses = _wins_losses(same_profile)
        if (
            losses >= config.LEARNING_EXIT_PROFILE_LOSS_LIMIT
            and wins < config.LEARNING_MIN_RECENT_PATTERN_WINS
        ):
            label = f"exit profile {option_trade.get('exit_profile')}"
            reason = (
                f"{_learning_reason(label, losses, wins)} "
                "Bot will prefer Target 2 exit for this profile."
            )
            adjusted = _move_final_target_to_t2(adjusted, reason)
            reasons.append(reason)
            _record_learning(option_trade, {
                "mistake_type": "REPEATED_EXIT_PROFILE_LOSS",
                "matched_pattern": label,
                "recent_losses": losses,
                "recent_wins": wins,
                "recent_trade_refs": _trade_refs(same_profile),
                "old_behavior": "Wait for final target for this exit profile.",
                "new_behavior": "Prefer Target 2 exit for this exit profile.",
                "action_taken": "EXIT_AT_TARGET_2",
                "reason": reason,
            })

    if news_conflict:
        conflict_rows = [row for row in recent_exits if _truthy(row.get("news_conflict"))]
        wins, losses = _wins_losses(conflict_rows)
        if losses >= config.LEARNING_NEWS_CONFLICT_LOSS_LIMIT:
            reason = (
                f"{_learning_reason('news-conflict trades', losses, wins)} "
                f"Size reduced to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}%."
            )
            adjusted = _penalize_or_reduce(adjusted, config.LEARNING_LOSS_PATTERN_SCORE_PENALTY, reason)
            reasons.append(reason)
            _record_learning(option_trade, {
                "mistake_type": "REPEATED_NEWS_CONFLICT_LOSS",
                "matched_pattern": "news-conflict trades",
                "recent_losses": losses,
                "recent_wins": wins,
                "recent_trade_refs": _trade_refs(conflict_rows),
                "old_behavior": "Use normal size during news conflict.",
                "new_behavior": f"Reduce size to {config.LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT}% during news conflict.",
                "action_taken": "REDUCE_SIZE",
                "reason": reason,
            })

    if reasons:
        adjusted["learning_adjustments"] = " | ".join(reasons)

    return adjusted, reasons
