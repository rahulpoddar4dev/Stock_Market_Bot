import os
from pathlib import Path

# config.py - Runtime settings. Keep real API keys in .env or environment variables.


def _load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file()

FYERS_APP_ID = os.getenv("FYERS_APP_ID", "KWZ0ORZ4RY-200")        # From Fyers API Dashboard
FYERS_SECRET_ID = os.getenv("FYERS_SECRET_ID", "XhOfsb84HaeUaB6t")    # From Fyers API Dashboard
FYERS_REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/redirect-uri/index.html")
FYERS_ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "")
FYERS_REQUEST_TIMEOUT = 15                 # Seconds to wait for Fyers API response
FYERS_LOGIN_RETRIES = 3                    # Retry temporary Fyers/network timeout during login
FYERS_LOGIN_RETRY_SLEEP = 5                # Seconds between login retries
FYERS_AUTO_LOGIN = True                    # Auto login / capture auth_code
PUBLIC_URL = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL") or os.getenv("DASHBOARD_PUBLIC_URL") or "").rstrip("/")
FYERS_CALLBACK_HOST = os.getenv("FYERS_CALLBACK_HOST", "0.0.0.0" if (os.getenv("PORT") or os.getenv("RENDER")) else "127.0.0.1") # Bind host for local/server callback
FYERS_CALLBACK_PUBLIC_HOST = os.getenv("FYERS_CALLBACK_PUBLIC_HOST", FYERS_CALLBACK_HOST)
FYERS_CALLBACK_PORT = int(os.getenv("FYERS_CALLBACK_PORT", "8085"))
FYERS_CALLBACK_PATH = "/callback"
FYERS_CALLBACK_TIMEOUT_SECONDS = int(os.getenv("FYERS_CALLBACK_TIMEOUT_SECONDS", "180")) # Time allowed to complete browser login
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")     # From https://platform.openai.com/api-keys
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")    # Override in Render env if you want a cheaper/faster model
PAPER_TRADE = True                        # True means no real orders will be placed
TRADING_MODE = "PAPER"                    # PAPER, SEMI_AUTO, or LIVE_AUTO
ENABLE_LIVE_AUTO = False                  # Must be True before LIVE_AUTO can place orders
AUTO_EXIT_ON_TARGET_SL = True             # Try to exit live trade when target/SL is hit
TEST_SIGNAL_OVERRIDE = ""                 # Use "BUY" or "SELL" only for paper testing
RUN_CONTINUOUSLY = True                   # Keep analyzing after one successful login
ANALYSIS_INTERVAL_SECONDS = 60            # Wait time between analysis runs
DASHBOARD_ENABLED = True                  # Start browser dashboard with main.py
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0" if (os.getenv("PORT") or os.getenv("RENDER")) else "127.0.0.1") # Local/server dashboard host
DASHBOARD_PORT = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080"))) # Dashboard starts here or next free port
NIFTY_INSTRUMENT_TOKEN = "NSE:NIFTY50-INDEX"  # NIFTY 50 Fyers symbol
ANALYZE_INDICES = ["NIFTY", "SENSEX"]     # Bot compares these indices before trading
REQUIRE_INDEX_CONFIRMATION = False        # Pro: NIFTY and SENSEX can trade independently
CANDLE_INTERVAL = "5minute"               # Candle size for strategy analysis
CANDLE_LOOKBACK_DAYS = 5                  # Historical candle lookback
MIN_CANDLES_FOR_STRATEGY = 30             # Minimum candles needed for indicators
CANDLE_PATTERN_ENABLED = True             # Add candlestick pattern confirmation to chart analysis
CANDLE_PATTERN_BLOCK_CONFLICT = False     # Soft-block: penalize score instead of killing trade when HTF trend is strong
CANDLE_PATTERN_SCORE_BONUS = 5.0          # Setup score bonus when pattern confirms chart signal
CANDLE_PATTERN_SCORE_PENALTY = 5.0        # Setup score penalty when no pattern confirms a normal setup
CANDLE_PATTERN_CONFLICT_MARGIN = 2.0      # Opposite pattern score margin needed to block
CANDLE_PATTERN_REQUIRE_CONFIRMATION_BELOW_SCORE = 18.0  # Weak/normal setups need pattern support below this score
CANDLE_PATTERN_MULTI_TIMEFRAME_ENABLED = True
CANDLE_PATTERN_SECONDARY_INTERVALS = ["minute", "3minute"]
CANDLE_PATTERN_SECONDARY_LOOKBACK_DAYS = 2
HTF_CANDLE_INTERVAL = "15minute"              # Higher-Timeframe anchor (prevents counter-trend traps)
HTF_TREND_FILTER_ENABLED = True               # Hard invariant: 15m trend direction dictates allowed trades
HTF_LOOKBACK_DAYS = 5
MIN_SETUP_SCORE = 6.0                     # Entry setup score threshold (aligned with active momentum)
MIN_EMA_GAP_POINTS = 1.5                  # Minimum EMA separation (aligned with active market)
MIN_ATR_PERCENT = 0.05                    # Avoid very low volatility / sideways market
MIN_ADX = 12.0                            # Trend strength threshold (aligned with active momentum)
BREAKOUT_LOOKBACK_CANDLES = 3             # BUY needs close above recent high, SELL below recent low
MAX_RSI_BUY = 80.0                        # Allow strong bullish moves up to 80
MIN_RSI_SELL = 20.0                       # Allow strong bearish crash moves down to 20
NO_NEW_TRADE_AFTER = "15:00"              # Do not open fresh trades after this time
NO_NEW_TRADE_BEFORE = "09:20"             # Earliest entry window (first 5-min candle completed)
DYNAMIC_OPENING_FILTER_ENABLED = True     # Use live liquidity & spread checks instead of blind clock wait
OPENING_WINDOW_END = "09:30"              # Opening spread settlement monitoring window
OPENING_MAX_BID_ASK_SPREAD_PERCENT = 0.8  # Max allowed spread % during opening window (0.8% = liquid book)
PRE_OPEN_START = "09:00"                  # Pre-open reference window start
PRE_OPEN_END = "09:15"                    # Pre-open reference window end
PRE_OPEN_REFERENCE_END = "09:25"          # Keep showing pre-open context during early market decisions
PRE_OPEN_GAP_PERCENT = 0.20               # Gap threshold for bullish/bearish context
PRE_OPEN_STRONG_GAP_PERCENT = 0.50        # Strong gap threshold for stronger context
AVOID_LUNCH_START = "12:00"               # Optional low-volume window start
AVOID_LUNCH_END = "13:15"                 # Optional low-volume window end
AVOID_LUNCH_TRADES = True                 # Soft lunch filter: only strong fresh entries are allowed
LUNCH_ALLOW_STRONG_SETUP = True           # Allow lunch trades when chart setup is strong enough
SQUARE_OFF_TIME = "15:20"                 # Close all open trades before market close
MARKET_CLOSE_TIME = "15:30"               # NSE market close time
EXPIRY_DAY_NO_NEW_TRADE_AFTER = "14:15"   # Do not open fresh same-expiry option trades after this time
NEWS_FILTER_ENABLED = True                # Fetch market news for analysis/logging
NEWS_BLOCK_TRADES = False                 # News is reference-only; chart/risk/quality decide trades
NEWS_BLOCK_HIGH_RISK = False              # News is reference-only; do not block by news alone
NEWS_FETCH_TIMEOUT = 5                    # Seconds to wait for RSS news
NEWS_CACHE_SECONDS = 300                  # Reuse news result for this many seconds
NEWS_MAX_HEADLINES = 8                    # Headlines used for sentiment check
NEWS_ALLOW_UNVERIFIED_SSL_FALLBACK = True # RSS fallback for macOS local certificate issues
NEWS_RSS_URLS = [
    "https://news.google.com/rss/search?q=NIFTY%20OR%20SENSEX%20OR%20Indian%20stock%20market%20when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=RBI%20OR%20inflation%20OR%20Fed%20OR%20crude%20oil%20India%20market%20when:1d&hl=en-IN&gl=IN&ceid=IN:en",
]
MARKET_CONTEXT_ENABLED = True
MARKET_CONTEXT_BLOCK_HIGH_RISK = False   # Do NOT block trades when volatility/crash news spikes
MARKET_CONTEXT_BLOCK_OPPOSITE_BIAS = False
MARKET_CONTEXT_REDUCE_SIZE_ON_MIXED = True
MARKET_CONTEXT_REDUCED_SIZE_PERCENT = 50
MARKET_CONTEXT_BULLISH_SCORE = 15
MARKET_CONTEXT_BEARISH_SCORE = -15
MARKET_CONTEXT_HIGH_RISK_SCORE = -35
MARKET_CONTEXT_VIX_HIGH = 18.0
MARKET_CONTEXT_VIX_EXTREME = 22.0
MARKET_CONTEXT_VIX_LOW = 13.0
VIX_FILTER_ENABLED = True
VIX_CRUSH_CHANGE_PERCENT = 3.0          # If India VIX drops >= 3.0% intraday, flag IV crush risk
VIX_BLOCK_ON_IV_CRUSH = False           # Disabled: do not block option buying during strong market rally when VIX cools down
MARKET_CONTEXT_SUPPORT_INDEX_CHANGE_MIN = 0.15
MARKET_CONTEXT_TOP_STOCK_CHANGE_MIN = 0.25
MARKET_CONTEXT_BREADTH_POSITIVE_PERCENT = 60
MARKET_CONTEXT_BREADTH_NEGATIVE_PERCENT = 40
MARKET_CONTEXT_OPTION_CHAIN_STRIKES_EACH_SIDE = 2
MARKET_CONTEXT_PCR_BULLISH = 1.1
MARKET_CONTEXT_PCR_BEARISH = 0.8
FREE_MARKET_DATA_ENABLED = True
FREE_MARKET_DATA_TIMEOUT_SECONDS = 4
FREE_MARKET_DATA_CACHE_SECONDS = 180
FREE_MARKET_ALLOW_UNVERIFIED_SSL_FALLBACK = True
FREE_GIFT_NIFTY_NSEIX_ENABLED = True
FREE_GIFT_NIFTY_NSEIX_URL = "https://www.nseix.com/api/market-rate?type=derivative"
FREE_MARKET_GLOBAL_SYMBOLS = {
    "Dow Jones": "^DJI",
    "Nasdaq": "^IXIC",
    "S&P 500": "^GSPC",
}
FREE_MARKET_SINGAPORE_SYMBOLS = {
    "Singapore STI": "^STI",
}
FREE_MARKET_GIFT_NIFTY_SYMBOLS = {
    "GIFT Nifty": "GIFTNIFTY.NS",
    "GIFT Nifty Alt": "NIFTY1!",
}
FREE_MARKET_CURRENCY_COMMODITY_SYMBOLS = {
    "USDINR": "INR=X",
    "Dollar Index": "DX-Y.NYB",
    "Crude Oil": "CL=F",
}
FREE_MARKET_GLOBAL_CHANGE_MIN = 0.25
FREE_MARKET_SINGAPORE_CHANGE_MIN = 0.20
FREE_MARKET_USDINR_CHANGE_MIN = 0.20
FREE_MARKET_DXY_CHANGE_MIN = 0.20
FREE_MARKET_CRUDE_CHANGE_MIN = 0.75
MARKET_CONTEXT_TOP_STOCKS = [
    "NSE:RELIANCE",
    "NSE:HDFCBANK",
    "NSE:ICICIBANK",
    "NSE:INFY",
    "NSE:TCS",
    "NSE:LT",
]
MARKET_CONTEXT_SUPPORT_INDICES = {
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY": "NSE:NIFTY FIN SERVICE",
}

# Google Drive CSV backup/sync.
# Configure an rclone Google Drive remote with this name before enabling.
GOOGLE_DRIVE_SYNC_ENABLED = False
GOOGLE_DRIVE_RCLONE_REMOTE = "tradingbotdrive"
GOOGLE_DRIVE_FOLDER_ID = "1fEdcm071Z3_K9owIIt9Dfw1mEgSR7pwG"
GOOGLE_DRIVE_SYNC_INTERVAL_SECONDS = 300
GOOGLE_DRIVE_SYNC_MOVE_AFTER_UPLOAD = False
GOOGLE_DRIVE_SYNC_INCLUDE_ACTIVE_LOGS = True
GOOGLE_DRIVE_SYNC_ROOTS = ["logs"]
GOOGLE_DRIVE_SYNC_EXCLUDE_FILENAMES = ["learning_history.csv"]

# Risk Management Settings
PAPER_BALANCE_OVERRIDE = float(os.getenv("PAPER_BALANCE", 100000)) # Base paper fund capital (1 Lakh)
MAX_TRADE_AMOUNT = 0       # 0 means no fixed cap; sizing uses MAX_FUND_USAGE_PERCENT
MAX_FUND_USAGE_PERCENT = 40 # Default/Normal fund usage percent (40% of fund)
MAX_RISK_PER_TRADE = 0   # 0 means dynamic % of fund used
MAX_RISK_PER_TRADE_PERCENT_OF_FUND = 5.0 # Normal 5.0% fund risk per trade
MAX_RISK_PER_TRADE_BUFFER_PERCENT = 10 # Allow 10% natural tolerance on risk limit so near-lot fractions round up
MAX_DAILY_LOSS = 0      # 0 means dynamic % of fund used
MAX_DAILY_LOSS_PERCENT_OF_FUND = 12 # Stop trading after 12% daily drawdown to protect capital

# Dynamic Regime-Based Position Sizing (Scale up on strong trend, scale down on chop)
DYNAMIC_REGIME_SIZING_ENABLED = True
REGIME_TRENDING_FUND_PERCENT = 70.0      # When market is strongly trending, allow up to 70% fund deployment
REGIME_TRENDING_RISK_PERCENT = 7.5       # When market is strongly trending, allow up to 7.5% risk (enables 3-4 lots)
REGIME_NORMAL_FUND_PERCENT = 40.0        # In normal/balanced conditions (1-2 lots)
REGIME_NORMAL_RISK_PERCENT = 5.0         # Normal 5.0% risk
REGIME_SIDEWAYS_FUND_PERCENT = 30.0      # When sideways / struggling chop
REGIME_SIDEWAYS_RISK_PERCENT = 3.5       # Reduced 3.5% risk in chop (defensive 1 lot)
REGIME_HIGH_VOL_FUND_PERCENT = 30.0      # High volatility / news event
REGIME_HIGH_VOL_RISK_PERCENT = 3.5       # Reduced 3.5% risk

MAX_DAILY_PROFIT = 0       # 0 means dynamic % of fund used
MAX_DAILY_PROFIT_PERCENT_OF_FUND = 10 # Daily profit lock: stop trading after 10% daily gain to protect profits
MAX_DAILY_TRADES = 0       # 0 means no fixed trade count limit; risk guards still stop bad days
MAX_DAILY_STOP_LOSSES = 0  # 0 means count limit removed; machine keeps trading and does not stop after 2 SLs
MAX_DAILY_FAILED_TRADES = 0 # 0 means count limit removed
MAX_OPEN_TRADES = 1         # Keep this at 1 while testing
LIVE_MAX_FUND_USAGE_PERCENT = MAX_FUND_USAGE_PERCENT
LIVE_MAX_SINGLE_TRADE_FUND_USAGE_PERCENT = MAX_FUND_USAGE_PERCENT
LIVE_MAX_OPEN_TRADES = MAX_OPEN_TRADES
LIVE_MAX_DAILY_STOP_LOSSES = 0
LIVE_MAX_DAILY_FAILED_TRADES = 0
LIVE_MAX_RISK_PER_TRADE = 0
LIVE_MAX_DAILY_LOSS = 0
LIVE_MAX_DAILY_PROFIT = 0
LIVE_MAX_DAILY_PROFIT_PERCENT_OF_FUND = MAX_DAILY_PROFIT_PERCENT_OF_FUND
PAPER_MAX_FUND_USAGE_PERCENT = 40
PAPER_MAX_SINGLE_TRADE_FUND_USAGE_PERCENT = 40
PAPER_MAX_OPEN_TRADES = 1
PAPER_MAX_DAILY_STOP_LOSSES = 0
PAPER_MAX_DAILY_FAILED_TRADES = 0
PAPER_MAX_RISK_PER_TRADE = 0
PAPER_MAX_DAILY_LOSS = 0
PAPER_MAX_DAILY_PROFIT = 0
PAPER_MAX_DAILY_PROFIT_PERCENT_OF_FUND = MAX_DAILY_PROFIT_PERCENT_OF_FUND
PAPER_MIN_SETUP_SCORE = MIN_SETUP_SCORE
PAPER_MIN_ADX = MIN_ADX
TRAILING_STOP_ENABLED = True
# Tier 0 (+7.5% profit): Early breakeven lock -> Move SL to Entry + 1.5% (Cost + Brokerage covered)
TRAIL_TO_COST_PROFIT_PERCENT = 7.5
TRAIL_COST_LOCK_PERCENT = 1.5
# Tier 1 (+12% profit): Lock SL to Entry + 3.5%
TRAIL_TO_ENTRY_PROFIT_PERCENT = 12.0
TRAIL_TIER1_LOCK_PERCENT = 3.5
# Tier 2 (+20% profit): Lock SL to Entry + 10.0%
TRAIL_LOCK_PROFIT_PERCENT = 20.0
TRAIL_TIER2_LOCK_PERCENT = 10.0
# Peak Trailing: once profit > 25%, trail 10% below peak
TRAIL_STEP_PROFIT_PERCENT = 25
TRAIL_DISTANCE_PERCENT = 10
TARGET_PROFIT_PERCENT = 25
STOP_LOSS_PERCENT = 15
PARTIAL_EXIT_ENABLED = True
PARTIAL_EXIT_T1_PERCENT = 50
PARTIAL_EXIT_T2_PERCENT = 50
STAGE_1_STOP_PROFIT_PERCENT = 1.5 # Cost + brokerage lock once T1 is cleared (Zero loss guarantee)
STAGE_2_STOP_PROFIT_PERCENT = 12
OPTION_SELECTION_MODE = "AUTO" # AUTO, ATM, ITM

# Structural & Multi-Timeframe Stop-Loss Settings
STOP_LOSS_CALCULATION_MODE = "STRUCTURAL_HTF" # Uses 15m candle swing + ATR converted via Delta
OPTION_SL_MIN_PERCENT = 15.0       # Floor for medium options (prevents fake 20-point Nifty wicks)
OPTION_SL_CHEAP_PERCENT = 18.0     # Floor for cheap options (< 100) with higher percentage vibration
OPTION_SL_EXPENSIVE_PERCENT = 12.0 # Floor for expensive options (> 220) to limit point risk
OPTION_SL_MAX_PERCENT = 20.0       # Ceiling so risk is strictly bounded
UNDERLYING_ATR_MULTIPLIER = 1.5    # 1.5x ATR room on underlying index

# 94% Target Buffer Rule (treats 94% of target distance as Target Hit to avoid missing by a hair)
TARGET_BUFFER_RATIO = 0.94

# Time-Based Exit (20-Minute Rule: exit if trade has no momentum to protect against theta decay)
TIME_BASED_EXIT_ENABLED = True
TIME_BASED_EXIT_MINUTES = 20
TIME_BASED_EXIT_MIN_PROFIT_PERCENT = 4.0

# Flip-Flop & Wave Exhaustion Cooldowns
OPPOSITE_DIRECTION_FLIP_COOLDOWN_MINUTES = 10
POST_PROFIT_WAVE_COOLDOWN_MINUTES = 6           # v5: Prevent chasing exhausted wave tops within 6m of a T1+/10%+ win
POST_PROFIT_WAVE_MIN_GAIN_PERCENT = 9.0         # v5: Minimum exit gain % to trigger post-profit wave cooldown
CROSS_INDEX_SL_COOLDOWN_MINUTES = 5             # v5: Prevent immediate NIFTY <-> SENSEX SL whipsaw within 5m

# v5 Smart Post-Buy Live Chart & Support Tracking (Active Position Guardian)
LIVE_POSITION_CHART_TRACKING_ENABLED = True     # v5: Track 5m/10m/15m Index & Option chart continuously while trade is open
LIVE_POSITION_CHART_REFRESH_SECONDS = 30        # v5: Refresh live candle & support structure every 30s during monitoring
LIVE_OPTION_SUPPORT_TRAIL_ENABLED = True        # v5: Trail SL behind confirmed 5m option swing low support
LIVE_OPTION_SUPPORT_BUFFER_PERCENT = 1.0        # v5: Keep 1.0% breathing room below confirmed option swing low
LIVE_STRUCTURE_HOLD_EXEMPTION_ENABLED = True    # v5: Do not cut trade on 10m/20m timer when live chart structure is intact
LIVE_CHART_REVERSAL_EXIT_ENABLED = True         # v5: Exit cleanly when both 5m Index & Option charts break structure against trade

# Environment & Database Isolation (Separates staging_bot from main production)
def _detect_bot_env() -> str:
    explicit = os.getenv("BOT_ENV", "").strip().lower()
    if explicit:
        return explicit
    branch = os.getenv("RENDER_GIT_BRANCH", "").strip().lower()
    service = os.getenv("RENDER_SERVICE_NAME", "").strip().lower()
    pub_url = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL") or "").strip().lower()
    if branch == "main" or ("trading-ai-bot" in pub_url and "staging" not in pub_url):
        return "main"
    if "staging" in branch or "staging" in service or "staging" in pub_url:
        return "staging"
    return "staging"


BOT_ENV = _detect_bot_env()

# MongoDB Database Configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "stock_market_bot")

# Super-Trend Trailing SL & Smart Full-Position Dynamic Shadow Trailing System (v4/v5)
RUNNER_TRAILING_ENABLED = True
RUNNER_TRAIL_START_PROFIT_PERCENT = 13.5 # Activate peak trailing immediately after Target 1 is cleared
RUNNER_TRAIL_DISTANCE_PERCENT = 12.0     # Fallback runner trail distance when v4 shadow trail is disabled
FULL_POSITION_RIDE_ENABLED = True        # v4: Hold 100% lots on Strong T1 & trail with Adaptive Shadow SL
SHADOW_TRAIL_PARITY_FLOOR_RATIO = 0.65          # Lock 65% of T1 profit on 100% lots when Strong T1 breaks
SHADOW_TRAIL_ONE_LOT_PARITY_FLOOR_RATIO = 0.65  # Lock 65% of T1 profit when a 1-lot trade rides past Strong T1
SHADOW_TRAIL_WEAK_T1_STOP_RATIO = 0.25          # When Weak T1 sells 50%, lock remaining 50% at 25% of T1 profit
SHADOW_TRAIL_GAP_RATIO = 0.35                   # Trail by 35% of active target gap (T2-T1 or T3-T2)
SHADOW_TRAIL_MIN_SPREAD_MULTIPLIER = 3.0        # Anti-noise floor: at least 3x current bid-ask spread
SHADOW_TRAIL_MIN_PEAK_PERCENT = 2.5             # Anti-noise floor: at least 2.5% of peak option price
SHADOW_TRAIL_MIN_ATR_RATIO = 0.25               # Anti-noise floor: at least 0.25x option ATR
SHADOW_TRAIL_UPDATE_MIN_STEP_PERCENT = 0.30     # 1s tick throttle: only write SL update if >= max(1 tick, 0.30%) higher
T1_CONT_MAX_SPREAD_PERCENT = 0.80               # Max spread % for Strong T1 continuation
T1_CONT_HIGH_PREMIUM_MAX_SPREAD_PERCENT = 1.20  # Max spread % for high-premium (> Rs 350) Sensex options
T1_CONT_MIN_BID_ASK_RATIO = 0.90                # Total 5-level Bid Qty >= 0.90x Total 5-level Ask Qty
T1_CONT_MAX_BID_DROP_RATIO = 0.45               # No sudden >= 45% bid quantity collapse vs previous check
TARGET_3_PROFIT_RUNNER_ALLOW_EXPANSION = False  # Clean 100% full exit at Target 3 in v4

# Morning Opening Sizing Shield (09:20 - 09:30): Deploy test size to prevent opening-wick drawdowns
MORNING_OPENING_SIZE_PERCENT = 50.0
MORNING_OPENING_MAX_LOTS = 2

# Option Strike Sweet-Spot Selection (High Delta & Responsiveness)
OPTION_SWEET_SPOT_ENABLED = True
OPTION_MIN_RECOMMENDED_PREMIUM = 70.0    # Avoid illiquid/penny options with high theta decay
OPTION_MAX_RECOMMENDED_PREMIUM = 350.0   # Avoid deep ITM bloated options

# Compatibility defaults used by the current bot modules.
OPEN_TRADE_MONITOR_INTERVAL_SECONDS = globals().get("OPEN_TRADE_MONITOR_INTERVAL_SECONDS", 1)
PAPER_ANALYSIS_INTERVAL_SECONDS = globals().get("PAPER_ANALYSIS_INTERVAL_SECONDS", 60)
PAPER_OPEN_TRADE_MONITOR_INTERVAL_SECONDS = globals().get("PAPER_OPEN_TRADE_MONITOR_INTERVAL_SECONDS", 1)
AUTO_ANALYSIS_INTERVAL_SECONDS = globals().get("AUTO_ANALYSIS_INTERVAL_SECONDS", 15)
AUTO_OPEN_TRADE_MONITOR_INTERVAL_SECONDS = globals().get("AUTO_OPEN_TRADE_MONITOR_INTERVAL_SECONDS", 1)
ENTRY_ORDER_TYPE = globals().get("ENTRY_ORDER_TYPE", "LIMIT")
ENTRY_LIMIT_BUFFER_PERCENT = globals().get("ENTRY_LIMIT_BUFFER_PERCENT", 0.5)
EXIT_ORDER_TYPE = globals().get("EXIT_ORDER_TYPE", "LIMIT")
EXIT_LIMIT_BUFFER_PERCENT = globals().get("EXIT_LIMIT_BUFFER_PERCENT", 1.0)
FORCE_MARKET_ON_SQUARE_OFF = globals().get("FORCE_MARKET_ON_SQUARE_OFF", True)
FORCE_MARKET_ON_SL = globals().get("FORCE_MARKET_ON_SL", True)
DYNAMIC_MOMENTUM_EXIT_ENABLED = globals().get("DYNAMIC_MOMENTUM_EXIT_ENABLED", True)
DYNAMIC_STALL_MINUTES = globals().get("DYNAMIC_STALL_MINUTES", 10)
DYNAMIC_STALL_MIN_PEAK_PROFIT_PERCENT = globals().get("DYNAMIC_STALL_MIN_PEAK_PROFIT_PERCENT", 2.5)
DYNAMIC_STALL_MAX_PROFIT_PERCENT = globals().get("DYNAMIC_STALL_MAX_PROFIT_PERCENT", 0.5)
DYNAMIC_BREAKOUT_EXEMPTION_PERCENT = globals().get("DYNAMIC_BREAKOUT_EXEMPTION_PERCENT", 0.8)
DYNAMIC_FLIP_VALIDATION_ENABLED = globals().get("DYNAMIC_FLIP_VALIDATION_ENABLED", True)
ORDER_PRICE_TICK_SIZE = globals().get("ORDER_PRICE_TICK_SIZE", 0.05)
DYNAMIC_EXIT_ENABLED = globals().get("DYNAMIC_EXIT_ENABLED", False)
DYNAMIC_EMERGENCY_STOP_LOSS_PERCENT = globals().get("DYNAMIC_EMERGENCY_STOP_LOSS_PERCENT", 25)
DYNAMIC_PROFIT_LOCK_START_PERCENT = globals().get("DYNAMIC_PROFIT_LOCK_START_PERCENT", 5)
DYNAMIC_PROFIT_LOCK_PERCENT = globals().get("DYNAMIC_PROFIT_LOCK_PERCENT", 0)
DYNAMIC_TRAIL_START_PERCENT = globals().get("DYNAMIC_TRAIL_START_PERCENT", 8)
DYNAMIC_TRAIL_DISTANCE_PERCENT = globals().get("DYNAMIC_TRAIL_DISTANCE_PERCENT", 3)
DYNAMIC_PARTIAL_EXIT_PERCENT = globals().get("DYNAMIC_PARTIAL_EXIT_PERCENT", 50)
DYNAMIC_PARTIAL_MIN_PROFIT_PERCENT = globals().get("DYNAMIC_PARTIAL_MIN_PROFIT_PERCENT", 6)
DYNAMIC_PARTIAL_PEAK_PULLBACK_PERCENT = globals().get("DYNAMIC_PARTIAL_PEAK_PULLBACK_PERCENT", 3)
DYNAMIC_FULL_EXIT_MIN_PEAK_PROFIT_PERCENT = globals().get("DYNAMIC_FULL_EXIT_MIN_PEAK_PROFIT_PERCENT", 10)
DYNAMIC_FULL_EXIT_PEAK_PULLBACK_PERCENT = globals().get("DYNAMIC_FULL_EXIT_PEAK_PULLBACK_PERCENT", 5)
DYNAMIC_SOFT_LOSS_EXIT_PERCENT = globals().get("DYNAMIC_SOFT_LOSS_EXIT_PERCENT", 7)
DYNAMIC_EARLY_LOSS_PERCENT = globals().get("DYNAMIC_EARLY_LOSS_PERCENT", 4)
DYNAMIC_EARLY_LOSS_RECOVERY_CANDLES = globals().get("DYNAMIC_EARLY_LOSS_RECOVERY_CANDLES", 2)
DYNAMIC_QUICK_PROFIT_EXIT_PERCENT = globals().get("DYNAMIC_QUICK_PROFIT_EXIT_PERCENT", 10)
DYNAMIC_TIME_EXIT_MINUTES = globals().get("DYNAMIC_TIME_EXIT_MINUTES", 15)
DYNAMIC_TIME_EXIT_MIN_PROFIT_PERCENT = globals().get("DYNAMIC_TIME_EXIT_MIN_PROFIT_PERCENT", 3)
DYNAMIC_DEPTH_WEAK_RATIO = globals().get("DYNAMIC_DEPTH_WEAK_RATIO", 0.65)
MARKET_DEPTH_ENABLED = globals().get("MARKET_DEPTH_ENABLED", True)
MAX_BID_ASK_SPREAD_PERCENT = globals().get("MAX_BID_ASK_SPREAD_PERCENT", 2.0)
MIN_TOP_DEPTH_QUANTITY_MULTIPLIER = globals().get("MIN_TOP_DEPTH_QUANTITY_MULTIPLIER", 0.5)
USE_DEPTH_FOR_LIMIT_PRICE = globals().get("USE_DEPTH_FOR_LIMIT_PRICE", True)
ALLOW_SINGLE_INDEX_TRADE = globals().get("ALLOW_SINGLE_INDEX_TRADE", True)
SINGLE_INDEX_TRADE_SIZE_PERCENT = globals().get("SINGLE_INDEX_TRADE_SIZE_PERCENT", 100)
MIN_SINGLE_INDEX_SETUP_SCORE = globals().get("MIN_SINGLE_INDEX_SETUP_SCORE", 8.0)
MIN_SINGLE_INDEX_ADX = globals().get("MIN_SINGLE_INDEX_ADX", 16.0)
AI_TRADE_OVERRIDE_ENABLED = globals().get("AI_TRADE_OVERRIDE_ENABLED", globals().get("PAPER_AI_TRADE_OVERRIDE_ENABLED", True))
AI_TRADE_ALLOWED_MODES = globals().get("AI_TRADE_ALLOWED_MODES", ["PAPER", "LIVE_AUTO"])
AI_TRADE_SIZE_PERCENT = globals().get("AI_TRADE_SIZE_PERCENT", globals().get("PAPER_AI_TRADE_SIZE_PERCENT", 35))
AI_TRADE_MIN_SCORE = globals().get("AI_TRADE_MIN_SCORE", globals().get("PAPER_AI_MIN_SCORE", 55.0))
AI_TRADE_HIGH_CONFIDENCE_SCORE = globals().get("AI_TRADE_HIGH_CONFIDENCE_SCORE", globals().get("PAPER_AI_HIGH_CONFIDENCE_SCORE", 72.0))
AI_TRADE_MIN_SCORE_GAP = globals().get("AI_TRADE_MIN_SCORE_GAP", globals().get("PAPER_AI_MIN_SCORE_GAP", 10.0))
AI_TRADE_BREAKOUT_LOOKBACK_CANDLES = globals().get("AI_TRADE_BREAKOUT_LOOKBACK_CANDLES", globals().get("PAPER_AI_BREAKOUT_LOOKBACK_CANDLES", 6))
AI_TRADE_MIN_CANDLE_BODY_PERCENT = globals().get("AI_TRADE_MIN_CANDLE_BODY_PERCENT", globals().get("PAPER_AI_MIN_CANDLE_BODY_PERCENT", 0.04))
AI_TRADE_MIN_RECENT_MOVE_PERCENT = globals().get("AI_TRADE_MIN_RECENT_MOVE_PERCENT", globals().get("PAPER_AI_MIN_RECENT_MOVE_PERCENT", 0.10))
AI_TRADE_MIN_DAY_CHANGE_PERCENT = globals().get("AI_TRADE_MIN_DAY_CHANGE_PERCENT", globals().get("PAPER_AI_MIN_DAY_CHANGE_PERCENT", 0.15))
AI_TRADE_MIN_ADX = globals().get("AI_TRADE_MIN_ADX", globals().get("PAPER_AI_MIN_ADX", 12.0))
AI_TRADE_MIN_ATR_PERCENT = globals().get("AI_TRADE_MIN_ATR_PERCENT", globals().get("PAPER_AI_MIN_ATR_PERCENT", 0.02))
AI_TRADE_MIN_EMA_GAP_PERCENT = globals().get("AI_TRADE_MIN_EMA_GAP_PERCENT", globals().get("PAPER_AI_MIN_EMA_GAP_PERCENT", 0.01))
AI_TRADE_USE_OPENAI = os.getenv("AI_TRADE_USE_OPENAI", "false").lower() in {"1", "true", "yes", "on"}
AI_TRADE_OPENAI_TIMEOUT_SECONDS = int(os.getenv("AI_TRADE_OPENAI_TIMEOUT_SECONDS", "20"))
AI_TRADE_OPENAI_MAX_CANDLES = int(os.getenv("AI_TRADE_OPENAI_MAX_CANDLES", "30"))
AI_TRADE_OPENAI_RESPECT_HOLD = os.getenv("AI_TRADE_OPENAI_RESPECT_HOLD", "true").lower() in {"1", "true", "yes", "on"}
MAX_SINGLE_TRADE_FUND_USAGE_PERCENT = globals().get("MAX_SINGLE_TRADE_FUND_USAGE_PERCENT", MAX_FUND_USAGE_PERCENT)
MAX_SINGLE_TRADE_FUND_USAGE_BUFFER_PERCENT = globals().get("MAX_SINGLE_TRADE_FUND_USAGE_BUFFER_PERCENT", 0)
MAX_RISK_PER_TRADE_PERCENT_OF_FUND = globals().get("MAX_RISK_PER_TRADE_PERCENT_OF_FUND", 5.0)
MAX_RISK_PER_TRADE_BUFFER_PERCENT = globals().get("MAX_RISK_PER_TRADE_BUFFER_PERCENT", 10)
MAX_RISK_PER_TRADE_PERCENT_OF_USED_FUND = globals().get("MAX_RISK_PER_TRADE_PERCENT_OF_USED_FUND", 12)
MAX_DAILY_LOSS_PERCENT_OF_FUND = globals().get("MAX_DAILY_LOSS_PERCENT_OF_FUND", 10)
MAX_DAILY_LOSS_PERCENT_OF_USED_FUND = globals().get("MAX_DAILY_LOSS_PERCENT_OF_USED_FUND", MAX_DAILY_LOSS_PERCENT_OF_FUND)
MAX_DAILY_PROFIT_PERCENT_OF_FUND = globals().get("MAX_DAILY_PROFIT_PERCENT_OF_FUND", 10)
MAX_DAILY_PROFIT_PERCENT_OF_USED_FUND = globals().get("MAX_DAILY_PROFIT_PERCENT_OF_USED_FUND", 50)
SAME_UNDERLYING_SL_COOLDOWN_MINUTES = globals().get("SAME_UNDERLYING_SL_COOLDOWN_MINUTES", 20)
SECOND_TRADE_SIZE_PERCENT = globals().get("SECOND_TRADE_SIZE_PERCENT", 50)
STRONG_MOMENTUM_SCORE = globals().get("STRONG_MOMENTUM_SCORE", 18)
STRONG_MOMENTUM_ADX = globals().get("STRONG_MOMENTUM_ADX", 25)
LUNCH_MIN_SETUP_SCORE = globals().get("LUNCH_MIN_SETUP_SCORE", 25.0)
LUNCH_MIN_ADX = globals().get("LUNCH_MIN_ADX", 25.0)
LUNCH_TRADE_SIZE_PERCENT = globals().get("LUNCH_TRADE_SIZE_PERCENT", 100)
WEAK_MOMENTUM_SCORE = globals().get("WEAK_MOMENTUM_SCORE", 10)
WEAK_MOMENTUM_ADX = globals().get("WEAK_MOMENTUM_ADX", 20)
STRONG_TARGET_PROFIT_PERCENT = globals().get("STRONG_TARGET_PROFIT_PERCENT", 35)
STRONG_STOP_LOSS_PERCENT = globals().get("STRONG_STOP_LOSS_PERCENT", 15)
WEAK_TARGET_PROFIT_PERCENT = globals().get("WEAK_TARGET_PROFIT_PERCENT", 24)
WEAK_STOP_LOSS_PERCENT = globals().get("WEAK_STOP_LOSS_PERCENT", 15)
STRONG_TARGET_STAGE_1_PERCENT = globals().get("STRONG_TARGET_STAGE_1_PERCENT", 14)
STRONG_TARGET_STAGE_2_PERCENT = globals().get("STRONG_TARGET_STAGE_2_PERCENT", 24)
NORMAL_TARGET_STAGE_1_PERCENT = globals().get("NORMAL_TARGET_STAGE_1_PERCENT", 14)
NORMAL_TARGET_STAGE_2_PERCENT = globals().get("NORMAL_TARGET_STAGE_2_PERCENT", 24)
WEAK_TARGET_STAGE_1_PERCENT = globals().get("WEAK_TARGET_STAGE_1_PERCENT", 12)
WEAK_TARGET_STAGE_2_PERCENT = globals().get("WEAK_TARGET_STAGE_2_PERCENT", 18)
OPTION_SELECTION_MODE = globals().get("OPTION_SELECTION_MODE", "AUTO")
OPTION_CHAIN_ANALYZER_ENABLED = globals().get("OPTION_CHAIN_ANALYZER_ENABLED", True)
OPTION_CHAIN_STRIKES_EACH_SIDE = globals().get("OPTION_CHAIN_STRIKES_EACH_SIDE", 2)
OPTION_CHAIN_CANDLE_LOOKBACK_DAYS = globals().get("OPTION_CHAIN_CANDLE_LOOKBACK_DAYS", 2)
OPTION_CHAIN_CANDLE_INTERVAL = globals().get("OPTION_CHAIN_CANDLE_INTERVAL", CANDLE_INTERVAL)
OPTION_CHAIN_MIN_CONFIDENCE_SCORE = globals().get("OPTION_CHAIN_MIN_CONFIDENCE_SCORE", 55)
OPTION_CHAIN_SMALL_SIZE_SCORE = globals().get("OPTION_CHAIN_SMALL_SIZE_SCORE", 40)
OPTION_CHAIN_LOG_ONLY_SCORE = globals().get("OPTION_CHAIN_LOG_ONLY_SCORE", 0)
OPTION_CHAIN_SMALL_SIZE_PERCENT = globals().get("OPTION_CHAIN_SMALL_SIZE_PERCENT", 50)
OPTION_CHAIN_MAX_SPREAD_PERCENT = globals().get("OPTION_CHAIN_MAX_SPREAD_PERCENT", 1.0)
OPTION_CHAIN_MIN_VOLUME = globals().get("OPTION_CHAIN_MIN_VOLUME", 1000)
OPTION_CHAIN_MIN_OI = globals().get("OPTION_CHAIN_MIN_OI", 5000)
OPTION_CHAIN_MIN_LTP = globals().get("OPTION_CHAIN_MIN_LTP", 20)
OPTION_CHAIN_MAX_LTP = globals().get("OPTION_CHAIN_MAX_LTP", 600)
OPTION_CHAIN_MAX_OTM_STEPS = globals().get("OPTION_CHAIN_MAX_OTM_STEPS", 1)
OPTION_CHAIN_MIN_POTENTIAL_LEFT_PERCENT = globals().get("OPTION_CHAIN_MIN_POTENTIAL_LEFT_PERCENT", 8)
OPTION_CHAIN_MIN_RISK_REWARD = globals().get("OPTION_CHAIN_MIN_RISK_REWARD", 1.2)
OPTION_CHAIN_DEFAULT_STOP_LOSS_PERCENT = globals().get("OPTION_CHAIN_DEFAULT_STOP_LOSS_PERCENT", 10)
OPTION_CHAIN_MAX_STOP_LOSS_PERCENT = globals().get("OPTION_CHAIN_MAX_STOP_LOSS_PERCENT", 12)
OPTION_CHAIN_MIN_STOP_LOSS_PERCENT = globals().get("OPTION_CHAIN_MIN_STOP_LOSS_PERCENT", 8)
OPTION_CHAIN_ONE_LOT_TARGET_STAGE = globals().get("OPTION_CHAIN_ONE_LOT_TARGET_STAGE", 1)
OPTION_CHAIN_PRICE_TIER_SL_ENABLED = globals().get("OPTION_CHAIN_PRICE_TIER_SL_ENABLED", True)
OPTION_CHAIN_BREAKOUT_LOOKBACK_CANDLES = globals().get("OPTION_CHAIN_BREAKOUT_LOOKBACK_CANDLES", 3)
OPTION_CHAIN_SWING_LOOKBACK_CANDLES = globals().get("OPTION_CHAIN_SWING_LOOKBACK_CANDLES", 5)
OPTION_CHAIN_RETEST_TOLERANCE_PERCENT = globals().get("OPTION_CHAIN_RETEST_TOLERANCE_PERCENT", 0.5)
OPTION_CHAIN_ENTRY_MAX_CHASE_PERCENT = globals().get("OPTION_CHAIN_ENTRY_MAX_CHASE_PERCENT", 2.5)
OPTION_CHAIN_REJECT_NEAR_TARGET_BUFFER_PERCENT = globals().get("OPTION_CHAIN_REJECT_NEAR_TARGET_BUFFER_PERCENT", 1.0)
OPTION_CHAIN_REQUIRED_DEPTH_LOTS = globals().get("OPTION_CHAIN_REQUIRED_DEPTH_LOTS", 1)
OPTION_CHAIN_OI_SUPPORT_RESISTANCE_ENABLED = globals().get("OPTION_CHAIN_OI_SUPPORT_RESISTANCE_ENABLED", True)
OPTION_REQUIRE_ABOVE_VWAP = globals().get("OPTION_REQUIRE_ABOVE_VWAP", True)
OPTION_VWAP_TOLERANCE_PERCENT = globals().get("OPTION_VWAP_TOLERANCE_PERCENT", 0.5)
REGIME_TRENDING_ADX = globals().get("REGIME_TRENDING_ADX", 22)
REGIME_SIDEWAYS_ADX = globals().get("REGIME_SIDEWAYS_ADX", 18)
REGIME_HIGH_ATR_PERCENT = globals().get("REGIME_HIGH_ATR_PERCENT", 0.45)
REGIME_LOW_ATR_PERCENT = globals().get("REGIME_LOW_ATR_PERCENT", 0.05)
REGIME_TRENDING_EMA_GAP_PERCENT = globals().get("REGIME_TRENDING_EMA_GAP_PERCENT", 0.025)
REGIME_SIDEWAYS_EMA_GAP_PERCENT = globals().get("REGIME_SIDEWAYS_EMA_GAP_PERCENT", 0.018)
REGIME_HIGH_VOL_SIZE_PERCENT = globals().get("REGIME_HIGH_VOL_SIZE_PERCENT", 50)
REGIME_SIDEWAYS_SIZE_PERCENT = globals().get("REGIME_SIDEWAYS_SIZE_PERCENT", 50)
REGIME_NEWS_EVENT_SIZE_PERCENT = globals().get("REGIME_NEWS_EVENT_SIZE_PERCENT", 50)
TIME_LATE_SESSION_MIN_QUALITY_SCORE = globals().get("TIME_LATE_SESSION_MIN_QUALITY_SCORE", 80)
TIME_MIDDAY_MIN_QUALITY_SCORE = globals().get("TIME_MIDDAY_MIN_QUALITY_SCORE", 75)
TRADE_QUALITY_NORMAL_THRESHOLD = globals().get("TRADE_QUALITY_NORMAL_THRESHOLD", 50)
TRADE_QUALITY_SMALL_SIZE_THRESHOLD = globals().get("TRADE_QUALITY_SMALL_SIZE_THRESHOLD", 35)
TRADE_QUALITY_LOG_ONLY_THRESHOLD = globals().get("TRADE_QUALITY_LOG_ONLY_THRESHOLD", 0)
TRADE_QUALITY_SMALL_SIZE_PERCENT = globals().get("TRADE_QUALITY_SMALL_SIZE_PERCENT", 50)
MAX_ENTRY_DISTANCE_FROM_EMA9_PERCENT = globals().get("MAX_ENTRY_DISTANCE_FROM_EMA9_PERCENT", 0.85)
MAX_ENTRY_DISTANCE_FROM_VWAP_PERCENT = globals().get("MAX_ENTRY_DISTANCE_FROM_VWAP_PERCENT", 2.25)
FRESH_SIGNAL_CANDLES = globals().get("FRESH_SIGNAL_CANDLES", 3)
RETEST_TOUCH_TOLERANCE_PERCENT = globals().get("RETEST_TOUCH_TOLERANCE_PERCENT", 0.20)
CONTINUATION_MIN_ADX = globals().get("CONTINUATION_MIN_ADX", 12.0)
CONTINUATION_MIN_QUALITY_SCORE = globals().get("CONTINUATION_MIN_QUALITY_SCORE", 50)
ALLOW_CONTINUATION_REDUCED_SIZE = globals().get("ALLOW_CONTINUATION_REDUCED_SIZE", True)
HARD_BLOCK_EXTREME_ENTRY_DISTANCE_MULTIPLIER = globals().get("HARD_BLOCK_EXTREME_ENTRY_DISTANCE_MULTIPLIER", 1.5)
LEARNING_GUARD_ENABLED = globals().get("LEARNING_GUARD_ENABLED", True)
LEARNING_LOOKBACK_DAYS = globals().get("LEARNING_LOOKBACK_DAYS", 5)
LEARNING_PATTERN_LOSS_LIMIT = globals().get("LEARNING_PATTERN_LOSS_LIMIT", 2)
LEARNING_TIME_WINDOW_LOSS_LIMIT = globals().get("LEARNING_TIME_WINDOW_LOSS_LIMIT", 2)
LEARNING_EXIT_PROFILE_LOSS_LIMIT = globals().get("LEARNING_EXIT_PROFILE_LOSS_LIMIT", 2)
LEARNING_NEWS_CONFLICT_LOSS_LIMIT = globals().get("LEARNING_NEWS_CONFLICT_LOSS_LIMIT", 2)
LEARNING_LOSS_REASON_LIMIT = globals().get("LEARNING_LOSS_REASON_LIMIT", 2)
LEARNING_ENTRY_MODE_LOSS_LIMIT = globals().get("LEARNING_ENTRY_MODE_LOSS_LIMIT", 2)
LEARNING_MARKET_REGIME_LOSS_LIMIT = globals().get("LEARNING_MARKET_REGIME_LOSS_LIMIT", 2)
LEARNING_TIME_WINDOW_MINUTES = globals().get("LEARNING_TIME_WINDOW_MINUTES", 60)
LEARNING_MIN_RECENT_PATTERN_WINS = globals().get("LEARNING_MIN_RECENT_PATTERN_WINS", 1)
LEARNING_ADAPTIVE_EXIT_ENABLED = globals().get("LEARNING_ADAPTIVE_EXIT_ENABLED", False)
LEARNING_T2_MISSED_EXIT_LOOKBACK = globals().get("LEARNING_T2_MISSED_EXIT_LOOKBACK", 1)
LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT = globals().get("LEARNING_REDUCE_SIZE_ON_LOSING_PATTERN_PERCENT", 50)
LEARNING_BLOCK_REPEATED_LOSS_PATTERNS = globals().get("LEARNING_BLOCK_REPEATED_LOSS_PATTERNS", False)
LEARNING_USE_SCORE_PENALTIES = globals().get("LEARNING_USE_SCORE_PENALTIES", True)
LEARNING_BAD_TIME_WINDOW_SCORE_PENALTY = globals().get("LEARNING_BAD_TIME_WINDOW_SCORE_PENALTY", 15)
LEARNING_LOSS_PATTERN_SCORE_PENALTY = globals().get("LEARNING_LOSS_PATTERN_SCORE_PENALTY", 10)
LEARNING_STRONG_SETUP_OVERRIDE_SCORE = globals().get("LEARNING_STRONG_SETUP_OVERRIDE_SCORE", 85)

RESEARCH_LOGGING_ENABLED = globals().get("RESEARCH_LOGGING_ENABLED", True)
GHOST_TRADE_TRACKING_ENABLED = globals().get("GHOST_TRADE_TRACKING_ENABLED", True)
GHOST_TRACK_MINUTES = globals().get("GHOST_TRACK_MINUTES", [5, 15, 30])

REALISTIC_PAPER_FILL_ENABLED = globals().get("REALISTIC_PAPER_FILL_ENABLED", True)
PAPER_ENTRY_SLIPPAGE_PERCENT = globals().get("PAPER_ENTRY_SLIPPAGE_PERCENT", 0.15)
PAPER_EXIT_SLIPPAGE_PERCENT = globals().get("PAPER_EXIT_SLIPPAGE_PERCENT", 0.15)
PAPER_MAX_FILL_SPREAD_PERCENT = globals().get("PAPER_MAX_FILL_SPREAD_PERCENT", 1.0)
PAPER_LIMIT_FILL_TOLERANCE_PERCENT = globals().get("PAPER_LIMIT_FILL_TOLERANCE_PERCENT", 1.5)
PAPER_BROKERAGE_PER_ORDER = globals().get("PAPER_BROKERAGE_PER_ORDER", 20)
PAPER_CHARGES_PERCENT = globals().get("PAPER_CHARGES_PERCENT", 0.12)

LIVE_SAFETY_GATE_ENABLED = globals().get("LIVE_SAFETY_GATE_ENABLED", True)
LIVE_SAFETY_MIN_TRADING_DAYS = globals().get("LIVE_SAFETY_MIN_TRADING_DAYS", 20)
LIVE_SAFETY_MIN_PROFIT_FACTOR = globals().get("LIVE_SAFETY_MIN_PROFIT_FACTOR", 1.5)
LIVE_SAFETY_MAX_DRAWDOWN_PERCENT = globals().get("LIVE_SAFETY_MAX_DRAWDOWN_PERCENT", 10)

AGGRESSIVE_TRADER_MODE = os.getenv("AGGRESSIVE_TRADER_MODE", "true").lower() in {"1", "true", "yes", "on"}
AGGRESSIVE_IGNORE_SOFT_BLOCKS = os.getenv("AGGRESSIVE_IGNORE_SOFT_BLOCKS", "true").lower() in {"1", "true", "yes", "on"}
AGGRESSIVE_ALLOW_LOW_OPTION_SCORE = os.getenv("AGGRESSIVE_ALLOW_LOW_OPTION_SCORE", "true").lower() in {"1", "true", "yes", "on"}
AGGRESSIVE_ALLOW_MARKET_DEPTH_BLOCK = os.getenv("AGGRESSIVE_ALLOW_MARKET_DEPTH_BLOCK", "false").lower() in {"1", "true", "yes", "on"}
AGGRESSIVE_FORCE_AI_HOLD_FALLBACK = os.getenv("AGGRESSIVE_FORCE_AI_HOLD_FALLBACK", "false").lower() in {"1", "true", "yes", "on"}

if AGGRESSIVE_TRADER_MODE:
    REQUIRE_INDEX_CONFIRMATION = False
    MARKET_CONTEXT_BLOCK_HIGH_RISK = False
    MARKET_CONTEXT_BLOCK_OPPOSITE_BIAS = False
    AVOID_LUNCH_TRADES = False
    MIN_SINGLE_INDEX_SETUP_SCORE = 8.0
    MIN_SINGLE_INDEX_ADX = 10.0
    AI_TRADE_SIZE_PERCENT = 100
    AI_TRADE_MIN_SCORE = 35.0
    AI_TRADE_HIGH_CONFIDENCE_SCORE = 60.0
    AI_TRADE_MIN_SCORE_GAP = 0.0
    AI_TRADE_OPENAI_RESPECT_HOLD = False
    TRADE_QUALITY_NORMAL_THRESHOLD = 45
    TRADE_QUALITY_SMALL_SIZE_THRESHOLD = 25
    TRADE_QUALITY_LOG_ONLY_THRESHOLD = 0
    TRADE_QUALITY_SMALL_SIZE_PERCENT = 100
    OPTION_CHAIN_MIN_CONFIDENCE_SCORE = 55
    OPTION_CHAIN_SMALL_SIZE_SCORE = 30
    OPTION_CHAIN_LOG_ONLY_SCORE = 0
    OPTION_CHAIN_SMALL_SIZE_PERCENT = 100
    SAME_UNDERLYING_SL_COOLDOWN_MINUTES = 20
    LEARNING_BLOCK_REPEATED_LOSS_PATTERNS = False
