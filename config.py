# ── Grid Bot · Configuration ─────────────────────────────────
DB_URL     = "postgresql+psycopg2://server@localhost/bybit_market"
API_KEY    = "2z2n4veKJSrex3QadB"
API_SECRET = "JvVXQFeiOxgIgHQkIOloqy1OAs1zRZ9TAqsF"
TESTNET    = True

# Grid
GRID_LEVELS      = 3       # Orders pro Seite
GRID_DISTANCE    = 0.003    # 0.3% zwischen Levels
RISK_PER_LEVEL   = 6.0      # USD pro Order (min. 5 USD Bybit)
MAX_EXPOSURE_USD = 120.0    # Max Margin pro Coin

# Range Detection
ATR_PERIOD     = 14
ADX_PERIOD     = 14
ATR_PCT_MAX    = 0.015      # >1.5% = Trend
ADX_MAX        = 22         # >22   = Trend
BREAKOUT_MULT  = 1.5        # ATR-Multiplikator für Kill-Switch

# Re-Centering
RECENTER_THRESH = 0.004     # 0.4% Drift vom Grid-Mitte → neues Grid

# Timing
CHECK_INTERVAL = 60         # Sekunden
KLINE_TABLE    = "kline_1h"
KLINE_LIMIT    = 120

# Coins: Multi-Coin-Selector (offen) → Fallback
SYMBOLS: list = []          # z.B. ["SOLUSDT", "XRPUSDT"]