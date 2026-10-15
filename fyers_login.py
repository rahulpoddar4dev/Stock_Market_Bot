import json
import os
import subprocess
import sys
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fyers_apiv3 import fyersModel
from requests.exceptions import RequestException
import config
from market_time import today_date

BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "fyers_session.json"


def _fyers_call_with_retries(label, callback):
    last_error = None

    for attempt in range(1, config.FYERS_LOGIN_RETRIES + 1):
        try:
            return callback()
        except (RequestException, ConnectionResetError, TimeoutError) as error:
            last_error = error
            if attempt == config.FYERS_LOGIN_RETRIES:
                break
            print(
                f"{label} timed out. Retrying "
                f"{attempt}/{config.FYERS_LOGIN_RETRIES} in {config.FYERS_LOGIN_RETRY_SLEEP}s..."
            )
            time.sleep(config.FYERS_LOGIN_RETRY_SLEEP)

    raise last_error


def save_access_token(access_token):
    session_data = {
        "date": today_date().isoformat(),
        "access_token": access_token,
    }
    SESSION_FILE.write_text(json.dumps(session_data, indent=2))
    print(f"[FYERS ACCESS TOKEN] Active token for today ({today_date().isoformat()}): {access_token[:15]}...{access_token[-8:] if len(access_token) > 23 else ''}")

    # Save token to shared Database so all servers/instances auto-sync
    try:
        from database import db_save_config
        db_save_config("fyers_access_token", access_token)
        db_save_config("fyers_access_token_date", today_date().isoformat())
        print(f"[DB Auto-Sync] Fyers Access Token saved to shared Database for date {today_date().isoformat()}")
    except Exception as e:
        print(f"[DB Auto-Sync Warning] Could not save access token to Database: {e}")


def load_saved_access_token():
    env_token = os.getenv("FYERS_ACCESS_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()

    # 1. Check local session file
    if SESSION_FILE.exists():
        try:
            session_data = json.loads(SESSION_FILE.read_text())
            if session_data.get("date") == today_date().isoformat() and session_data.get("access_token"):
                return session_data.get("access_token")
        except json.JSONDecodeError:
            pass

    # 2. Check shared Database
    try:
        from database import db_get_config
        db_date = db_get_config("fyers_access_token_date")
        if db_date == today_date().isoformat():
            db_token = db_get_config("fyers_access_token")
            if db_token and db_token.strip():
                # Write to local file for fast caching
                session_data = {
                    "date": today_date().isoformat(),
                    "access_token": db_token.strip(),
                }
                SESSION_FILE.write_text(json.dumps(session_data, indent=2))
                print("[DB Auto-Sync] Valid Fyers Access Token retrieved from shared Database!")
                return db_token.strip()
    except Exception as e:
        pass

    return None


_SAVED_PROFILE_CACHE = {}


def create_fyers_client(access_token: str | None = None) -> fyersModel.FyersModel:
    token = access_token or load_saved_access_token()
    client = fyersModel.FyersModel(
        client_id=config.FYERS_APP_ID,
        token=token,
        is_async=False,
        log_path="",
    )

    def _unified_quote(keys):
        if isinstance(keys, str):
            keys = [keys]
        symbols = [k.replace("NFO:", "NSE:").replace("BFO:", "BSE:") for k in keys if k]
        if not symbols:
            return {}
        symbol_str = ",".join(symbols)
        try:
            res = client.quotes({"symbols": symbol_str})
        except Exception:
            res = {}

        output = {}
        if isinstance(res, dict) and res.get("s") == "ok":
            for item in res.get("d", []):
                sym = item.get("n", "")
                v = item.get("v", {})
                lp = float(v.get("lp", 0.0) or 0.0)
                cmd = v.get("cmd", {}) if isinstance(v.get("cmd"), dict) else {}
                best_bid = float(cmd.get("b_b", lp) or lp)
                best_ask = float(cmd.get("b_a", lp) or lp)
                quote_entry = {
                    "last_price": lp,
                    "volume": int(v.get("volume", 0) or 0),
                    "oi": int(v.get("oi", 0) or 0),
                    "oi_day_high": int(v.get("oi_day_high", 0) or 0),
                    "oi_day_low": int(v.get("oi_day_low", 0) or 0),
                    "ohlc": {
                        "open": float(v.get("open_price", lp) or lp),
                        "high": float(v.get("high_price", lp) or lp),
                        "low": float(v.get("low_price", lp) or lp),
                        "close": float(v.get("prev_close_price", lp) or lp),
                    },
                    "depth": {
                        "buy": [{"price": best_bid, "quantity": 1000, "orders": 1}],
                        "sell": [{"price": best_ask, "quantity": 1000, "orders": 1}],
                    },
                }
                output[sym] = quote_entry
                for orig_key in keys:
                    if orig_key.endswith(sym.split(":")[-1]):
                        output[orig_key] = quote_entry

        if len(symbols) == 1 and config.MARKET_DEPTH_ENABLED:
            try:
                depth_res = client.depth({"symbol": symbols[0], "ohlcv_flag": 1})
                if isinstance(depth_res, dict) and depth_res.get("s") == "ok":
                    d_data = depth_res.get("d", {})
                    bids = d_data.get("bids", [])
                    asks = d_data.get("asks", [])
                    if bids or asks:
                        d_entry = {
                            "buy": [{"price": float(b.get("price", 0)), "quantity": int(b.get("volume", 0)), "orders": int(b.get("orders", 1))} for b in bids],
                            "sell": [{"price": float(a.get("price", 0)), "quantity": int(a.get("volume", 0)), "orders": int(a.get("orders", 1))} for a in asks],
                        }
                        if symbols[0] in output:
                            output[symbols[0]]["depth"] = d_entry
                        for orig_k in keys:
                            if orig_k in output:
                                output[orig_k]["depth"] = d_entry
            except Exception:
                pass

        for k in keys:
            if k not in output:
                output[k] = {
                    "last_price": 0.0,
                    "volume": 0,
                    "oi": 0,
                    "ohlc": {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
                    "depth": {"buy": [], "sell": []},
                }
        return output

    def _unified_historical(instrument_token=None, symbol=None, from_date=None, to_date=None, interval="5minute"):
        from datetime import timedelta
        from market_time import market_now_naive
        sym = symbol or str(instrument_token or "")
        sym = sym.replace("NFO:", "NSE:").replace("BFO:", "BSE:")
        res_map = {"5minute": "5", "15minute": "15", "3minute": "3", "minute": "1", "day": "D"}
        resolution = res_map.get(str(interval).lower(), "5")
        to_dt = to_date or market_now_naive()
        from_dt = from_date or (to_dt - timedelta(days=5))
        payload = {
            "symbol": sym,
            "resolution": resolution,
            "date_format": "1",
            "range_from": from_dt.strftime("%Y-%m-%d") if hasattr(from_dt, "strftime") else str(from_dt),
            "range_to": to_dt.strftime("%Y-%m-%d") if hasattr(to_dt, "strftime") else str(to_dt),
            "cont_flag": "1",
        }
        try:
            resp = client.history(payload)
            if isinstance(resp, dict) and resp.get("s") == "ok":
                candles = []
                for c in resp.get("candles", []):
                    if len(c) >= 5:
                        dt = datetime.fromtimestamp(c[0]) if isinstance(c[0], (int, float)) else c[0]
                        candles.append({
                            "date": dt,
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5]) if len(c) > 5 else 0,
                        })
                return candles
        except Exception:
            pass
        return []

    def _unified_profile():
        try:
            res = client.get_profile()
            if isinstance(res, dict) and res.get("s") == "ok":
                data = res.get("data") or {}
                return {
                    "user_name": data.get("name") or data.get("fy_id") or "Trader",
                    "user_id": data.get("fy_id"),
                    "email": data.get("email_id"),
                    "broker": "Fyers",
                }
        except Exception:
            pass
        return {"user_name": "Trader", "broker": "Fyers"}

    def _unified_margins():
        try:
            res = client.funds()
            if isinstance(res, dict):
                fund_limits = res.get("fund_limit", [])
                for item in fund_limits:
                    title = str(item.get("title", "")).lower()
                    if "total balance" in title or "available balance" in title or "equity" in title:
                        amt = float(item.get("equityAmount", 0.0) or item.get("availableBalance", 0.0) or 0.0)
                        return {"equity": {"available": {"live_balance": amt}}}
                if fund_limits:
                    amt = float(fund_limits[0].get("equityAmount", 0.0) or 0.0)
                    return {"equity": {"available": {"live_balance": amt}}}
        except Exception:
            pass
        return {"equity": {"available": {"live_balance": 0.0}}}

    def _unified_orders():
        try:
            res = client.orderbook()
            if isinstance(res, dict):
                return res.get("orderBook", [])
        except Exception:
            pass
        return []

    client.quote = _unified_quote
    client.historical_data = _unified_historical
    client.profile = _unified_profile
    client.margins = _unified_margins
    client.orders = _unified_orders
    return client


def try_saved_login(silent: bool = False):
    access_token = load_saved_access_token()
    if not access_token:
        return None

    fyers = create_fyers_client(access_token)

    if _SAVED_PROFILE_CACHE.get("token") == access_token and _SAVED_PROFILE_CACHE.get("profile"):
        profile = _SAVED_PROFILE_CACHE["profile"]
    else:
        try:
            res = _fyers_call_with_retries("Saved Fyers session check", fyers.get_profile)
            if not isinstance(res, dict) or res.get("s") != "ok":
                return None
            profile = res.get("data") or {}
            _SAVED_PROFILE_CACHE["token"] = access_token
            _SAVED_PROFILE_CACHE["profile"] = profile
        except (RequestException, ConnectionResetError, TimeoutError) as error:
            if not silent:
                print("Using saved Fyers session.")
                print(f"Profile check skipped because Fyers network timed out: {error}")
            return fyers
        except Exception:
            return None

    if not silent:
        print("Using saved Fyers session.")
        user_name = profile.get("name") or profile.get("fy_id") or "Trader"
        print(f"Welcome: {user_name} ({profile.get('fy_id', '')})")
    return fyers


class _FyersCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    error_message = None
    dashboard_url = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        code = params.get("auth_code", [""])[0] or params.get("code", [""])[0]
        status = params.get("s", [""])[0]
        error = params.get("message", [""])[0] or params.get("error", [""])[0]

        if code and (status in {"", "ok", "200"}):
            _FyersCallbackHandler.auth_code = code
            self._send_page(
                "Fyers login successful. Redirecting to dashboard...",
                redirect=True,
            )
            return

        if parsed.path != config.FYERS_CALLBACK_PATH:
            self._send_page(
                "Fyers callback server is running. Please complete Fyers login.",
                redirect=False,
            )
            return

        _FyersCallbackHandler.error_message = error or "Fyers login callback did not include auth_code."
        self._send_page(_FyersCallbackHandler.error_message, redirect=False)

    def _send_page(self, message: str, redirect: bool):
        dashboard_url = _FyersCallbackHandler.dashboard_url
        redirect_tag = ""
        if redirect and dashboard_url:
            redirect_tag = f'<meta http-equiv="refresh" content="1; url={dashboard_url}">'

        body = f"""
        <html>
          <head>
            <title>Fyers Login</title>
            {redirect_tag}
            <style>
              body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 40px; }}
              .box {{ max-width: 640px; border: 1px solid #d8dee9; border-radius: 8px; padding: 24px; }}
              a {{ color: #3157c8; }}
            </style>
          </head>
          <body>
            <div class="box">
              <h2>{message}</h2>
              <p>You can return to the terminal now.</p>
              {f'<p><a href="{dashboard_url}">Open dashboard</a></p>' if dashboard_url else ''}
            </div>
          </body>
        </html>
        """
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


def _start_callback_server():
    server = None
    ports = [config.FYERS_CALLBACK_PORT] + [
        port for port in range(8085, 8095) if port != config.FYERS_CALLBACK_PORT
    ]

    for port in ports:
        try:
            server = ThreadingHTTPServer((config.FYERS_CALLBACK_HOST, port), _FyersCallbackHandler)
            return server, port
        except OSError:
            continue

    return None, None


def _extract_auth_code(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if not cleaned:
        return ""

    if "://" in cleaned or cleaned.startswith("/"):
        parsed = urlparse(cleaned)
        params = parse_qs(parsed.query)
        codes = params.get("auth_code") or params.get("code")
        if codes and codes[0]:
            return codes[0].strip()

    if "auth_code=" in cleaned:
        query_part = cleaned.split("auth_code=", 1)[1]
        return query_part.split("&", 1)[0].strip()

    return cleaned


def _capture_auth_code(login_url: str, dashboard_url: str | None = None):
    _FyersCallbackHandler.auth_code = None
    _FyersCallbackHandler.error_message = None
    _FyersCallbackHandler.dashboard_url = dashboard_url

    # Check if redirect uri is local
    is_local_redirect = "127.0.0.1" in config.FYERS_REDIRECT_URI or "localhost" in config.FYERS_REDIRECT_URI
    server = None
    if is_local_redirect:
        server, bound_port = _start_callback_server()
        if server:
            server.timeout = 1.0

    print("Opening browser for Fyers login...")
    print(login_url)
    try:
        webbrowser.open(login_url, new=1)
    except Exception:
        pass

    deadline = time.time() + config.FYERS_CALLBACK_TIMEOUT_SECONDS
    try:
        while time.time() < deadline:
            if server:
                server.handle_request()
            if _FyersCallbackHandler.auth_code:
                return _FyersCallbackHandler.auth_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        return None
    finally:
        if server:
            server.server_close()

    return None


def _wait_for_dashboard_login(dashboard_url: str | None = None) -> bool:
    if not config.DASHBOARD_ENABLED:
        return False

    target_url = config.PUBLIC_URL or dashboard_url or f"http://127.0.0.1:{config.DASHBOARD_PORT}"
    print("\nWaiting for Fyers login via Dashboard...")
    print(f"Open dashboard: {target_url}")
    print("Log in from the dashboard button, or press Enter here to paste auth_code.")

    deadline = time.time() + 60
    while time.time() < deadline:
        if try_saved_login(silent=True):
            print("Detected Fyers login from dashboard!")
            return True
        time.sleep(2)

    return False


def get_fyers_login_url() -> str:
    session = fyersModel.SessionModel(
        client_id=config.FYERS_APP_ID,
        secret_key=config.FYERS_SECRET_ID,
        redirect_uri=config.FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    return session.generate_authcode()


def exchange_auth_code_for_token(auth_code: str) -> str:
    session = fyersModel.SessionModel(
        client_id=config.FYERS_APP_ID,
        secret_key=config.FYERS_SECRET_ID,
        redirect_uri=config.FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    response = session.generate_token()
    # Debug: show raw response from token endpoint
    print("[DEBUG] Fyers token generation raw response:", response)
    if not isinstance(response, dict) or response.get("s") != "ok":
        msg = response.get("message") if isinstance(response, dict) else str(response)
        raise RuntimeError(f"Fyers token generation failed: {msg}")

    access_token = response["access_token"]
    save_access_token(access_token)
    return access_token


def login(dashboard_url: str | None = None):
    saved_fyers = try_saved_login()
    if saved_fyers:
        return saved_fyers

    env_auth_code = os.getenv("FYERS_AUTH_CODE")
    if env_auth_code:
        auth_code = _extract_auth_code(env_auth_code)
        try:
            access_token = exchange_auth_code_for_token(auth_code)
            fyers = create_fyers_client(access_token)
            print("Login via FYERS_AUTH_CODE successful!")
            res = _fyers_call_with_retries("Fyers profile fetch", fyers.get_profile)
            profile = res.get("data", {}) if isinstance(res, dict) else {}
            print(f"Welcome: {profile.get('name', 'Trader')} ({profile.get('fy_id', '')})")
            return fyers
        except Exception as error:
            print(f"Failed to use env FYERS_AUTH_CODE: {error}")

    login_url = get_fyers_login_url()
    auth_code = None

    if config.FYERS_AUTO_LOGIN and sys.stdin.isatty():
        auth_code = _capture_auth_code(login_url, dashboard_url)

    if not auth_code:
        target_url = config.PUBLIC_URL or dashboard_url or login_url
        print("\nOpen this URL in browser to login to Fyers:")
        print(login_url)
        print()

        if not sys.stdin.isatty():
            print("Non-interactive terminal detected.")
            print(f"Please open dashboard URL to login or paste auth_code: {target_url}")
            print("Waiting for Fyers login to be completed via Dashboard...")
            last_msg_at = 0
            while True:
                saved_fyers = try_saved_login()
                if saved_fyers:
                    return saved_fyers
                now = time.time()
                if now - last_msg_at > 30:
                    print(f"[{time.strftime('%H:%M:%S')}] Still waiting for Fyers login via dashboard ({target_url})...")
                    last_msg_at = now
                time.sleep(2)

        pasted_value = input("Paste auth_code OR full redirected URL here: ")
        auth_code = _extract_auth_code(pasted_value)

    if not auth_code:
        raise RuntimeError("No auth_code captured – cannot continue login.")

    print(f"[DEBUG] Auth code captured: {auth_code[:15]}...")
    try:
        access_token = exchange_auth_code_for_token(auth_code)
    except Exception as e:
        print(f"[ERROR] Token exchange failed: {e}")
        raise
    print(f"[DEBUG] Access token obtained: {access_token[:30]}...{access_token[-10:] if len(access_token)>40 else ''}")
    fyers = create_fyers_client(access_token)
    print("Fyers Login successful!")
    res = _fyers_call_with_retries("Fyers profile fetch", fyers.get_profile)
    profile = res.get("data", {}) if isinstance(res, dict) else {}
    print(f"Welcome: {profile.get('name', 'Trader')} ({profile.get('fy_id', '')})")

    return fyers


if __name__ == "__main__":
    login()


def logout():
    """Clear saved Fyers session and cached credentials.

    This removes the local ``fyers_session.json`` file, deletes any stored
    token from the shared database, clears environment variables, resets account
    status, and clears the in‑memory profile cache.
    """
    # 1. Clear environment variables if present
    if "FYERS_ACCESS_TOKEN" in os.environ:
        del os.environ["FYERS_ACCESS_TOKEN"]
    if "FYERS_AUTH_CODE" in os.environ:
        del os.environ["FYERS_AUTH_CODE"]

    # 2. Remove the local session file
    if SESSION_FILE.exists():
        try:
            SESSION_FILE.unlink()
            print("[INFO] Local Fyers session file removed.")
        except Exception as e:
            print(f"[Warning] Failed to remove session file: {e}")
    else:
        print("[INFO] No local session file found.")

    # 3. Remove token from database
    try:
        from database import db_delete_config
        db_delete_config("fyers_access_token")
        db_delete_config("fyers_access_token_date")
        print("[INFO] Token removed from database.")
    except Exception as e:
        print(f"[Warning] DB token cleanup error: {e}")

    # 4. Clear account status
    try:
        from account_status import clear_account_status
        clear_account_status()
    except Exception:
        pass

    # 5. Clear cached profile data
    _SAVED_PROFILE_CACHE.clear()
    print("Logged out from Fyers successfully.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1].lower() == "logout":
        logout()
    else:
        login()
