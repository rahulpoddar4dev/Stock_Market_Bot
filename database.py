# database.py - Robust MongoDB storage for trading data, active trades, and audit logs (with SQLite fallback)

import csv
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from paper_fill_engine import effective_net_pnl, effective_trade_charges

try:
    import pymongo
except ImportError:
    pymongo = None

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")
DB_PATH = Path("trading_bot.db")
DATABASE_URL = os.getenv("DATABASE_URL")
MONGO_URI = os.getenv("MONGO_URI", getattr(config, "MONGO_URI", "mongodb://localhost:27017"))
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", getattr(config, "MONGO_DB_NAME", "stock_market_bot"))

_MONGO_CLIENT = None
_MONGO_DB = None
_MONGO_AVAILABLE = None
_LAST_MONGO_FAIL_TIME = 0.0
_LAST_MONGO_ERROR = ""


def get_mongo_error() -> str:
    return _LAST_MONGO_ERROR


def get_bot_env() -> str:
    env = str(os.getenv("BOT_ENV") or getattr(config, "BOT_ENV", "main") or "main").strip().lower()
    env = re.sub(r"[^a-z0-9_]", "", env)
    if env in {"", "main", "prod", "production"}:
        return "main"
    return env


def get_table_prefix() -> str:
    env = get_bot_env()
    return "" if env == "main" else f"{env}_"


def _table(base_name: str) -> str:
    return f"{get_table_prefix()}{base_name}"


def _collection(base_name: str) -> str:
    return _table(base_name)


def get_db_path() -> Path:
    prefix = get_table_prefix()
    return Path(f"{prefix}trading_bot.db") if prefix else Path("trading_bot.db")


DB_PATH = get_db_path()


def now_ist() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def get_mongo_db():
    global _MONGO_CLIENT, _MONGO_DB, _MONGO_AVAILABLE, _LAST_MONGO_FAIL_TIME, _LAST_MONGO_ERROR
    if _MONGO_DB is not None:
        return _MONGO_DB
    if pymongo is None:
        _MONGO_AVAILABLE = False
        _LAST_MONGO_ERROR = "pymongo package is not installed"
        return None

    now = time.time()
    if _MONGO_AVAILABLE is False and (now - _LAST_MONGO_FAIL_TIME) < 15.0:
        return None

    try:
        raw_uri = os.getenv("MONGO_URI", getattr(config, "MONGO_URI", "mongodb://localhost:27017"))
        raw_db = os.getenv("MONGO_DB_NAME", getattr(config, "MONGO_DB_NAME", "stock_market_bot"))
        uri = str(raw_uri or "").strip().strip("'\"")
        db_name = str(raw_db or "").strip().strip("'\"")

        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=6000)
        client.admin.command("ping")
        _MONGO_CLIENT = client
        _MONGO_DB = client[db_name]
        _MONGO_AVAILABLE = True
        _LAST_MONGO_ERROR = ""
        print(f"[Database] Successfully connected to MongoDB Atlas database: {db_name}")
        return _MONGO_DB
    except Exception as e:
        _LAST_MONGO_FAIL_TIME = now
        _LAST_MONGO_ERROR = str(e)
        _MONGO_AVAILABLE = False
        print(f"[Database] MongoDB connection failed: {e}. Falling back to SQLite/Postgres.")
        return None


def get_mongo_client():
    if get_mongo_db() is not None:
        return _MONGO_CLIENT
    return None


def is_mongo() -> bool:
    return get_mongo_db() is not None


def is_postgres() -> bool:
    return bool(DATABASE_URL and ("postgres://" in DATABASE_URL or "postgresql://" in DATABASE_URL))


def get_db_connection():
    if is_postgres():
        try:
            import psycopg2
            import psycopg2.extras
            pg_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            if "render.com" in pg_url and "sslmode" not in pg_url:
                separator = "&" if "?" in pg_url else "?"
                pg_url += f"{separator}sslmode=require"
            return psycopg2.connect(pg_url, connect_timeout=10)
        except Exception:
            pass

    conn = sqlite3.connect(str(get_db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _adapt_query(query: str, is_pg: bool) -> str:
    if is_pg:
        query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        query = query.replace("?", "%s")
    return query


def _init_sqlite_or_pg():
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_trades = _table("trades")
    t_decisions = _table("signal_decisions")
    t_logs = _table("system_logs")
    t_metrics = _table("daily_metrics")
    prefix = get_table_prefix()
    try:
        cursor = conn.cursor()
        cursor.execute(_adapt_query(f"""
        CREATE TABLE IF NOT EXISTS {t_trades} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            event_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            underlying TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            display_symbol TEXT,
            option_type TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            quantity INTEGER NOT NULL,
            lot_size INTEGER NOT NULL,
            lots INTEGER NOT NULL,
            target_1 REAL,
            target_2 REAL,
            target_3 REAL,
            stop_loss REAL,
            pnl REAL DEFAULT 0.0,
            gross_pnl REAL DEFAULT 0.0,
            charges REAL DEFAULT 0.0,
            exit_reason TEXT,
            entry_reason TEXT,
            strategy_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
        """, is_pg))

        cursor.execute(_adapt_query(f"""
        CREATE TABLE IF NOT EXISTS {t_decisions} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            event_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            final_decision TEXT NOT NULL,
            setup_score REAL,
            adx REAL,
            rsi REAL,
            close_price REAL,
            blocked_reason TEXT,
            details_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """, is_pg))

        cursor.execute(_adapt_query(f"""
        CREATE TABLE IF NOT EXISTS {t_logs} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT NOT NULL,
            level TEXT NOT NULL,
            component TEXT NOT NULL,
            message TEXT NOT NULL,
            extra_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """, is_pg))

        cursor.execute(_adapt_query(f"""
        CREATE TABLE IF NOT EXISTS {t_metrics} (
            date TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0,
            total_pnl REAL DEFAULT 0.0,
            max_drawdown REAL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        )
        """, is_pg))

        cursor.execute(_adapt_query("""
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """, is_pg))

        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}trades_date ON {t_trades}(date)")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}trades_status ON {t_trades}(status)")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}decisions_date ON {t_decisions}(date)")

        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[SQLite Init Error] {e}")
    finally:
        conn.close()


def _migrate_sqlite_to_mongo(mongo_db):
    sqlite_file = get_db_path()
    if not sqlite_file.exists():
        return
    try:
        conn = sqlite3.connect(str(sqlite_file))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            cur.execute(f"SELECT * FROM {_table('trades')}")
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                c_trades = _collection("trades")
                for r in rows:
                    r.pop("id", None)
                    mongo_db[c_trades].update_one(
                        {"trade_id": r.get("trade_id")},
                        {"$set": r},
                        upsert=True
                    )
                print(f"[MongoDB Auto-Migrate] Migrated {len(rows)} trades from SQLite to MongoDB.")
        except Exception:
            pass

        try:
            cur.execute(f"SELECT * FROM {_table('signal_decisions')}")
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                c_dec = _collection("signal_decisions")
                for r in rows:
                    r.pop("id", None)
                    mongo_db[c_dec].insert_one(r)
        except Exception:
            pass

        try:
            cur.execute("SELECT * FROM app_config")
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                c_conf = _collection("app_config")
                for r in rows:
                    mongo_db[c_conf].update_one(
                        {"key": r.get("key")},
                        {"$set": r},
                        upsert=True
                    )
        except Exception:
            pass

        conn.close()
    except Exception as e:
        print(f"[Migration Notice] {e}")


def init_database() -> None:
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            c_trades = _collection("trades")
            c_decisions = _collection("signal_decisions")
            c_logs = _collection("system_logs")
            c_metrics = _collection("daily_metrics")
            c_config = _collection("app_config")

            mongo_db[c_trades].create_index([("trade_id", 1)], unique=True)
            mongo_db[c_trades].create_index([("date", 1)])
            mongo_db[c_trades].create_index([("status", 1)])
            mongo_db[c_decisions].create_index([("date", 1)])
            mongo_db[c_logs].create_index([("event_time", -1)])
            mongo_db[c_metrics].create_index([("date", 1)], unique=True)
            mongo_db[c_config].create_index([("key", 1)], unique=True)

            if mongo_db[c_trades].count_documents({}) == 0:
                _migrate_sqlite_to_mongo(mongo_db)
                if mongo_db[c_trades].count_documents({}) == 0:
                    db_import_csv_trades()
            return
        except Exception as e:
            print(f"[MongoDB Init Error] {e}")

    _init_sqlite_or_pg()


def db_save_trade(trade: dict) -> None:
    """Insert or update a trade record in MongoDB (fallback: SQLite/Postgres)."""
    trade_id = trade.get("trade_id") or f"{trade.get('option_symbol')}_{trade.get('opened_at') or now_ist().isoformat()}"
    pnl_val = float(effective_net_pnl(trade))
    charges_val = float(effective_trade_charges(trade))
    doc = {
        "trade_id": trade_id,
        "date": trade.get("date") or now_ist().strftime("%Y-%m-%d"),
        "time": trade.get("time") or now_ist().strftime("%H:%M:%S"),
        "event_time": trade.get("event_time") or now_ist().isoformat(),
        "symbol": trade.get("underlying") or trade.get("symbol") or "NIFTY",
        "underlying": trade.get("underlying") or "NIFTY",
        "option_symbol": trade.get("option_symbol", ""),
        "display_symbol": trade.get("display_symbol", ""),
        "option_type": trade.get("option_type", "CE"),
        "trade_type": trade.get("trade_type", "BUY"),
        "mode": trade.get("mode", "PAPER"),
        "status": trade.get("status", "OPEN"),
        "entry_price": float(trade.get("entry_price") or 0),
        "exit_price": float(trade.get("exit_price") or 0) if trade.get("exit_price") else None,
        "quantity": int(float(trade.get("quantity") or 0)),
        "lot_size": int(float(trade.get("lot_size") or 1)),
        "lots": int(float(trade.get("lots") or 1)),
        "target_1": float(trade.get("target_1") or 0) if trade.get("target_1") else None,
        "target_2": float(trade.get("target_2") or 0) if trade.get("target_2") else None,
        "target_3": float(trade.get("target_3") or trade.get("target") or 0) if trade.get("target_3") or trade.get("target") else None,
        "stop_loss": float(trade.get("stop_loss") or 0),
        "pnl": pnl_val,
        "gross_pnl": float(trade.get("gross_pnl") or 0),
        "charges": charges_val,
        "exit_reason": trade.get("exit_reason") or trade.get("reason"),
        "entry_reason": trade.get("entry_reason"),
        "strategy_score": float(trade.get("strategy_score") or trade.get("SETUP_SCORE") or 0),
        "closed_at": trade.get("closed_at"),
        "updated_at": now_ist().isoformat(),
    }

    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            mongo_db[_collection("trades")].update_one(
                {"trade_id": trade_id},
                {"$set": doc, "$setOnInsert": {"created_at": now_ist().isoformat()}},
                upsert=True
            )
            return
        except Exception as e:
            db_log_event("ERROR", "db_save_trade_mongo", str(e))

    # SQLite fallback
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_trades = _table("trades")
    try:
        cursor = conn.cursor()
        query = _adapt_query(f"""
        INSERT INTO {t_trades} (
            trade_id, date, time, event_time, symbol, underlying, option_symbol, display_symbol,
            option_type, trade_type, mode, status, entry_price, exit_price, quantity, lot_size,
            lots, target_1, target_2, target_3, stop_loss, pnl, gross_pnl, charges,
            exit_reason, entry_reason, strategy_score, closed_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(trade_id) DO UPDATE SET
            status = excluded.status,
            exit_price = excluded.exit_price,
            stop_loss = excluded.stop_loss,
            pnl = excluded.pnl,
            gross_pnl = excluded.gross_pnl,
            charges = excluded.charges,
            exit_reason = excluded.exit_reason,
            closed_at = excluded.closed_at
        """, is_pg)

        params = (
            trade_id,
            doc["date"], doc["time"], doc["event_time"],
            doc["symbol"], doc["underlying"], doc["option_symbol"], doc["display_symbol"],
            doc["option_type"], doc["trade_type"], doc["mode"], doc["status"],
            doc["entry_price"], doc["exit_price"], doc["quantity"], doc["lot_size"],
            doc["lots"], doc["target_1"], doc["target_2"], doc["target_3"],
            doc["stop_loss"], doc["pnl"], doc["gross_pnl"], doc["charges"],
            doc["exit_reason"], doc["entry_reason"], doc["strategy_score"], doc["closed_at"],
        )
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
    except Exception as e:
        db_log_event("ERROR", "db_save_trade_sqlite", str(e))
    finally:
        conn.close()


def db_log_signal_decision(symbol: str, signal: dict, final_decision: str, reason: str, extra: dict | None = None) -> None:
    """Log every signal decision (executed or blocked) to MongoDB (fallback: SQLite)."""
    doc = {
        "date": now_ist().strftime("%Y-%m-%d"),
        "time": now_ist().strftime("%H:%M:%S"),
        "event_time": now_ist().isoformat(),
        "symbol": symbol,
        "signal": signal.get("SIGNAL", "HOLD"),
        "final_decision": final_decision,
        "setup_score": float(signal.get("SETUP_SCORE") or 0),
        "adx": float(signal.get("ADX_14") or 0),
        "rsi": float(signal.get("RSI_14") or 0),
        "close_price": float(signal.get("close") or 0),
        "blocked_reason": reason,
        "details_json": json.dumps(extra or {}, default=str),
        "created_at": now_ist().isoformat(),
    }

    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            mongo_db[_collection("signal_decisions")].insert_one(doc)
            return
        except Exception as e:
            print(f"[MongoDB Error] db_log_signal_decision: {e}")

    # SQLite fallback
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_decisions = _table("signal_decisions")
    try:
        cursor = conn.cursor()
        query = _adapt_query(f"""
        INSERT INTO {t_decisions} (
            date, time, event_time, symbol, signal, final_decision,
            setup_score, adx, rsi, close_price, blocked_reason, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, is_pg)
        params = (
            doc["date"], doc["time"], doc["event_time"], doc["symbol"],
            doc["signal"], doc["final_decision"], doc["setup_score"],
            doc["adx"], doc["rsi"], doc["close_price"], doc["blocked_reason"], doc["details_json"],
        )
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[DB Error] db_log_signal_decision: {e}")
    finally:
        conn.close()


def db_log_event(level: str, component: str, message: str, extra: dict | None = None) -> None:
    """Log system operational events and warnings to MongoDB (fallback: SQLite)."""
    doc = {
        "event_time": now_ist().isoformat(),
        "level": level.upper(),
        "component": component,
        "message": message,
        "extra_data": json.dumps(extra or {}, default=str) if extra else None,
        "created_at": now_ist().isoformat(),
    }

    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            mongo_db[_collection("system_logs")].insert_one(doc)
            return
        except Exception:
            pass

    # SQLite fallback
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_logs = _table("system_logs")
    try:
        cursor = conn.cursor()
        query = _adapt_query(f"""
        INSERT INTO {t_logs} (event_time, level, component, message, extra_data)
        VALUES (?, ?, ?, ?, ?)
        """, is_pg)
        params = (
            doc["event_time"], doc["level"], doc["component"], doc["message"], doc["extra_data"],
        )
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[DB Error] db_log_event: {e}")
    finally:
        conn.close()


def db_get_recent_trades(limit: int = 50) -> list[dict]:
    """Fetch recent trades for dashboard or analysis."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            cursor = mongo_db[_collection("trades")].find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
            return list(cursor)
        except Exception:
            pass

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_trades = _table("trades")
    try:
        cursor = conn.cursor()
        query = _adapt_query(f"SELECT * FROM {t_trades} ORDER BY id DESC LIMIT ?", is_pg)
        cursor.execute(query, (limit,))
        if is_pg:
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception:
        return []
    finally:
        conn.close()


def db_get_pnl_summary(mode: str = "PAPER") -> float:
    """Return total realized net P&L from closed trades in database."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            trades = mongo_db[_collection("trades")].find({"status": "CLOSED", "mode": mode}, {"_id": 0})
            total = 0.0
            for t in trades:
                charges = float(t.get("charges") or 0.0)
                pnl = float(t.get("pnl") or 0.0)
                gross_pnl = float(t.get("gross_pnl") or 0.0)
                if charges > 0 and abs(pnl - gross_pnl) < 0.01:
                    total += (gross_pnl - charges)
                else:
                    total += pnl
            return round(total, 2)
        except Exception:
            pass

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_trades = _table("trades")
    try:
        cursor = conn.cursor()
        query = _adapt_query(
            f"""
            SELECT COALESCE(
                SUM(
                    CASE
                        WHEN COALESCE(charges, 0.0) > 0 AND ABS(COALESCE(pnl, 0.0) - COALESCE(gross_pnl, 0.0)) < 0.01
                        THEN (COALESCE(gross_pnl, 0.0) - COALESCE(charges, 0.0))
                        ELSE COALESCE(pnl, 0.0)
                    END
                ),
                0.0
            )
            FROM {t_trades} WHERE status = 'CLOSED' AND mode = ?
            """,
            is_pg,
        )
        cursor.execute(query, (mode,))
        val = cursor.fetchone()[0]
        cursor.close()
        return round(float(val or 0.0), 2)
    except Exception:
        return 0.0
    finally:
        conn.close()


def db_get_distinct_dates() -> list[str]:
    """Return list of distinct trading dates from database."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            dates = set(mongo_db[_collection("trades")].distinct("date"))
            dec_dates = set(mongo_db[_collection("signal_decisions")].distinct("date"))
            all_dates = [str(d) for d in (dates | dec_dates) if d]
            return sorted(all_dates, reverse=True)
        except Exception:
            pass

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_trades = _table("trades")
    t_decisions = _table("signal_decisions")
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT DISTINCT date FROM {t_trades} UNION SELECT DISTINCT date FROM {t_decisions} ORDER BY 1 DESC")
        dates = [str(row[0]) for row in cursor.fetchall() if row[0]]
        cursor.close()
        return dates
    except Exception:
        return []
    finally:
        conn.close()


def db_get_trades_for_date(day: str, mode: str | None = None) -> list[dict]:
    """Return all trades for a given date from database."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            q = {"date": day}
            if mode:
                q["mode"] = mode
            cursor = mongo_db[_collection("trades")].find(q, {"_id": 0}).sort("time", 1)
            return list(cursor)
        except Exception:
            pass

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_trades = _table("trades")
    try:
        cursor = conn.cursor()
        if mode:
            query = _adapt_query(f"SELECT * FROM {t_trades} WHERE date = ? AND mode = ? ORDER BY id ASC", is_pg)
            cursor.execute(query, (day, mode))
        else:
            query = _adapt_query(f"SELECT * FROM {t_trades} WHERE date = ? ORDER BY id ASC", is_pg)
            cursor.execute(query, (day,))
        if is_pg:
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception:
        return []
    finally:
        conn.close()


def db_get_decisions_for_date(day: str) -> list[dict]:
    """Return all signal decisions for a given date from database."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            cursor = mongo_db[_collection("signal_decisions")].find({"date": day}, {"_id": 0}).sort("time", 1)
            return list(cursor)
        except Exception:
            pass

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    t_decisions = _table("signal_decisions")
    try:
        cursor = conn.cursor()
        query = _adapt_query(f"SELECT * FROM {t_decisions} WHERE date = ? ORDER BY id ASC", is_pg)
        cursor.execute(query, (day,))
        if is_pg:
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception:
        return []
    finally:
        conn.close()


def db_import_csv_trades(csv_path: str = "logs/trade_history.csv") -> int:
    """Import historical trades from CSV into database if not already imported."""
    p = Path(csv_path)
    if not p.exists():
        return 0

    count = 0
    try:
        with p.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    db_save_trade(row)
                    count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return count


def _get_shared_config_connection():
    shared_url = os.getenv("SHARED_CONFIG_DATABASE_URL")
    if not shared_url and DATABASE_URL and "/trading_bot_staging" in DATABASE_URL:
        shared_url = DATABASE_URL.replace("/trading_bot_staging", "/trading_bot_j0t6")
    if shared_url and ("postgres://" in shared_url or "postgresql://" in shared_url):
        try:
            import psycopg2
            pg_url = shared_url.replace("postgres://", "postgresql://", 1)
            if "render.com" in pg_url and "sslmode" not in pg_url:
                separator = "&" if "?" in pg_url else "?"
                pg_url += f"{separator}sslmode=require"
            return psycopg2.connect(pg_url, connect_timeout=10)
        except Exception:
            return None
    return None


def db_save_config(key: str, value: str) -> None:
    """Save or update a key-value pair in app_config table/collection."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            mongo_db[_collection("app_config")].update_one(
                {"key": key},
                {"$set": {"key": key, "value": value, "updated_at": now_ist().isoformat()}},
                upsert=True
            )
            return
        except Exception as e:
            print(f"[MongoDB Config Error] Failed to save {key}: {e}")

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        updated_at = now_ist().isoformat()
        if is_pg:
            query = """
            INSERT INTO app_config (key, value, updated_at) VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """
        else:
            query = """
            INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """
        cursor.execute(query, (key, value, updated_at))
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[DB Config Error] Failed to save {key}: {e}")
    finally:
        conn.close()

    shared_conn = _get_shared_config_connection()
    if shared_conn is not None:
        try:
            cur = shared_conn.cursor()
            cur.execute(
                """
                INSERT INTO app_config (key, value, updated_at) VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """,
                (key, value, now_ist().isoformat()),
            )
            shared_conn.commit()
            cur.close()
        except Exception:
            pass
        finally:
            shared_conn.close()


def db_get_config(key: str) -> str | None:
    """Get value for a key from app_config collection (fallback: SQLite/Postgres)."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            doc = mongo_db[_collection("app_config")].find_one({"key": key}, {"_id": 0, "value": 1})
            if doc and doc.get("value") is not None:
                return str(doc["value"])
            return None
        except Exception:
            pass

    shared_conn = _get_shared_config_connection()
    if shared_conn is not None:
        try:
            cur = shared_conn.cursor()
            cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
            row = cur.fetchone()
            cur.close()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
        finally:
            shared_conn.close()

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        query = _adapt_query("SELECT value FROM app_config WHERE key = ?", is_pg)
        cursor.execute(query, (key,))
        row = cursor.fetchone()
        cursor.close()
        return str(row[0]) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def db_delete_config(key: str) -> None:
    """Delete a key from app_config collection/table."""
    mongo_db = get_mongo_db()
    if mongo_db is not None:
        try:
            mongo_db[_collection("app_config")].delete_one({"key": key})
            return
        except Exception as e:
            print(f"[MongoDB Config Error] Failed to delete {key}: {e}")

    shared_conn = _get_shared_config_connection()
    if shared_conn is not None:
        try:
            cur = shared_conn.cursor()
            cur.execute("DELETE FROM app_config WHERE key = %s", (key,))
            shared_conn.commit()
            cur.close()
        except Exception:
            pass
        finally:
            shared_conn.close()

    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        query = _adapt_query("DELETE FROM app_config WHERE key = ?", is_pg)
        cursor.execute(query, (key,))
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[DB Config Error] Failed to delete {key}: {e}")
    finally:
        conn.close()


# Auto-initialize database schema / indexes on import
init_database()
