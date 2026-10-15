# view_db.py - Inspect database tables / collections and view trades & audit data easily

import argparse
import os
import sqlite3
from database import get_db_connection, is_postgres, is_mongo, get_mongo_db, DB_PATH, _table, _collection


def print_table(title: str, headers: list[str], rows: list[list]):
    print(f"\n{'='*20} {title} {'='*20}")
    if not rows:
        print("  (No data found)")
        return

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val) if val is not None else "-"))

    # Format header
    header_str = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_str = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    print(header_str)
    print(sep_str)

    # Format rows
    for row in rows:
        row_str = " | ".join(str(val if val is not None else "-").ljust(col_widths[i]) for i, val in enumerate(row))
        print(row_str)


def view_database(target_table: str = "all", limit: int = 15):
    if is_mongo():
        mongo_db = get_mongo_db()
        print(f"\n[Database Connected]: MongoDB ({mongo_db.name})")

        # 1. Summary of collection counts
        collections = ["trades", "signal_decisions", "system_logs", "daily_metrics", "app_config"]
        summary_rows = []
        for coll in collections:
            actual_coll = _collection(coll)
            count = mongo_db[actual_coll].count_documents({})
            summary_rows.append([actual_coll, count])
        print_table("MONGODB COLLECTIONS SUMMARY", ["Collection Name", "Total Documents"], summary_rows)

        # 2. View Trades
        if target_table in {"all", "trades"}:
            t_trades = _collection("trades")
            trades = list(mongo_db[t_trades].find({}, {"_id": 0}).sort("created_at", -1).limit(limit))
            headers = ["Trade ID", "Date", "Time", "Index", "Option Contract", "Type", "Side", "Mode", "Status", "Entry", "Exit", "P&L (Rs)", "Exit Reason"]
            rows = [
                [t.get("trade_id", "-")[:16], t.get("date"), t.get("time"), t.get("symbol"), t.get("option_symbol"),
                 t.get("option_type"), t.get("trade_type"), t.get("mode"), t.get("status"),
                 t.get("entry_price"), t.get("exit_price"), t.get("pnl"), t.get("exit_reason")]
                for t in trades
            ]
            print_table(f"LATEST TRADES (Last {limit})", headers, rows)

        # 3. View Signal Decisions
        if target_table in {"all", "decisions"}:
            t_dec = _collection("signal_decisions")
            dec_rows = list(mongo_db[t_dec].find({}, {"_id": 0}).sort("created_at", -1).limit(limit))
            headers = ["Time", "Symbol", "Signal", "Decision", "Score", "ADX", "RSI", "Reason / Block"]
            rows = [
                [d.get("time"), d.get("symbol"), d.get("signal"), d.get("final_decision"),
                 d.get("setup_score"), d.get("adx"), d.get("rsi"), d.get("blocked_reason")]
                for d in dec_rows
            ]
            print_table(f"LATEST DECISIONS & AUDIT LOGS (Last {limit})", headers, rows)

        # 4. View System Logs
        if target_table in {"all", "logs"}:
            t_logs = _collection("system_logs")
            log_rows = list(mongo_db[t_logs].find({}, {"_id": 0}).sort("event_time", -1).limit(limit))
            headers = ["Event Time", "Level", "Component", "Message"]
            rows = [
                [l.get("event_time"), l.get("level"), l.get("component"), l.get("message")]
                for l in log_rows
            ]
            print_table(f"LATEST SYSTEM EVENTS (Last {limit})", headers, rows)

        return

    # Fallback to SQLite / Postgres
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    db_type = "PostgreSQL (Cloud / Render)" if is_pg else f"SQLite ({DB_PATH.name})"
    print(f"\n[Database Connected]: {db_type}")

    cursor = conn.cursor()

    try:
        tables = ["trades", "signal_decisions", "system_logs", "daily_metrics"]
        summary_rows = []
        for tbl in tables:
            actual_tbl = _table(tbl)
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {actual_tbl}")
                count = cursor.fetchone()[0]
                summary_rows.append([actual_tbl, count])
            except Exception:
                summary_rows.append([actual_tbl, "N/A"])

        print_table("DATABASE TABLES SUMMARY", ["Table Name", "Total Records"], summary_rows)

        if target_table in {"all", "trades"}:
            t_trades = _table("trades")
            cursor.execute(f"""
                SELECT id, date, time, symbol, option_symbol, option_type, trade_type, mode, status, entry_price, exit_price, pnl, exit_reason
                FROM {t_trades} ORDER BY id DESC LIMIT {limit}
            """)
            trade_rows = cursor.fetchall()
            headers = ["ID", "Date", "Time", "Index", "Option Contract", "Type", "Side", "Mode", "Status", "Entry", "Exit", "P&L (Rs)", "Exit Reason"]
            rows = [[r[i] for i in range(len(headers))] for r in trade_rows]
            print_table(f"LATEST TRADES (Last {limit})", headers, rows)

        if target_table in {"all", "decisions"}:
            t_decisions = _table("signal_decisions")
            cursor.execute(f"""
                SELECT id, time, symbol, signal, final_decision, setup_score, adx, rsi, blocked_reason
                FROM {t_decisions} ORDER BY id DESC LIMIT {limit}
            """)
            dec_rows = cursor.fetchall()
            headers = ["ID", "Time", "Symbol", "Signal", "Decision", "Score", "ADX", "RSI", "Reason / Block"]
            rows = [[r[i] for i in range(len(headers))] for r in dec_rows]
            print_table(f"LATEST DECISIONS & AUDIT LOGS (Last {limit})", headers, rows)

        if target_table in {"all", "logs"}:
            t_logs = _table("system_logs")
            cursor.execute(f"""
                SELECT id, event_time, level, component, message
                FROM {t_logs} ORDER BY id DESC LIMIT {limit}
            """)
            log_rows = cursor.fetchall()
            headers = ["ID", "Event Time", "Level", "Component", "Message"]
            rows = [[r[i] for i in range(len(headers))] for r in log_rows]
            print_table(f"LATEST SYSTEM EVENTS (Last {limit})", headers, rows)

    except Exception as e:
        print(f"[Error querying database]: {e}")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View trading bot database tables / collections and records")
    parser.add_argument("--table", choices=["all", "trades", "decisions", "logs"], default="all", help="Target table/collection to view")
    parser.add_argument("--limit", type=int, default=15, help="Number of records to show")
    args = parser.parse_args()

    view_database(target_table=args.table, limit=args.limit)
