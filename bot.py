"""
Grid Bot · Multi-Coin · ADX + ATR Range Detection
──────────────────────────────────────────────────
Philosophie:
  • Grid läuft NUR in bestätigter Range (ADX + ATR%)
  • Breakout → sofortiger Kill-Switch (cancel + close)
  • Re-Centering → Grid folgt Preis wenn Drift > Schwelle
  • Fill-Detection → gefüllte Sell → neue Buy darunter (und umgekehrt)
  • Kein State in DB – alles in-memory, Bybit ist Source of Truth
"""
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text

import analytics
import config
from exchange import Exchange

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("grid_bot.log"),
    ],
)
log = logging.getLogger("grid_bot")

# ── Graceful Shutdown ─────────────────────────────────────────
_running = True

def _handle_signal(sig, _):
    global _running
    log.info("Shutdown-Signal – beende nach aktuellem Zyklus...")
    _running = False

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Datenbank ─────────────────────────────────────────────────
def load_candles(engine, symbol: str) -> pd.DataFrame:
    q = text(f"""
        SELECT open_time, open, high, low, close, volume
        FROM   {config.KLINE_TABLE}
        WHERE  symbol = :sym
        ORDER  BY open_time DESC
        LIMIT  :lim
    """)
    try:
        df = pd.read_sql_query(q, engine, params={"sym": symbol, "lim": config.KLINE_LIMIT})
        df = df.sort_values("open_time").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        log.error(f"load_candles {symbol}: {e}", exc_info=True)
        return pd.DataFrame()


# ── Grid State ────────────────────────────────────────────────
@dataclass
class GridState:
    active:     bool          = False
    mid:        float         = 0.0           # Preis beim letzten Grid-Start
    upper_band: float         = 0.0           # Breakout-Kill-Band oben
    lower_band: float         = 0.0           # Breakout-Kill-Band unten
    orders:     dict          = field(default_factory=dict)  # order_id → {"side", "price", "qty"}
    exposure:   float         = 0.0           # offene USD-Exposure


# ── Grid-Operationen ──────────────────────────────────────────
def _place_grid(symbol: str, mid: float, ex: Exchange, state: GridState):
    """Platziert neues Grid um mid. Löscht vorher allen State."""
    levels = analytics.grid_levels(mid, config.GRID_LEVELS, config.GRID_DISTANCE)
    placed = {}

    for side, prices in (("Buy", levels["buy"]), ("Sell", levels["sell"])):
        for price in prices:
            qty = analytics.qty_for_level(config.RISK_PER_LEVEL, price)
            oid = ex.limit(symbol, side, qty, price)
            if oid:
                placed[oid] = {"side": side, "price": price, "qty": qty}

    state.mid     = mid
    state.orders  = placed
    state.active  = True
    state.exposure = 0.0
    log.info(f"  {symbol}: Grid platziert – {len(placed)} Orders um ${mid:.4f}")


def _shutdown_grid(symbol: str, ex: Exchange, state: GridState):
    """Cancelt alle Orders und schließt offene Bybit-Positionen."""
    log.warning(f"[GRID OFF] {symbol}")
    ex.cancel_all(symbol)
    for pos in ex.positions(symbol):
        size = float(pos.get("size", 0))
        side = pos.get("side", "")
        if size > 0 and side:
            ex.close_market(symbol, side, size)
    state.active  = False
    state.orders  = {}
    state.exposure = 0.0


def _sync_fills(symbol: str, ex: Exchange, state: GridState):
    """
    Vergleicht in-memory Orders mit Bybit.
    Gefüllte Orders:
      - Buy gefüllt  → platziere Sell-Limit darüber (+1 Level)
      - Sell gefüllt → platziere Buy-Limit darunter (-1 Level)
    Hält Exposure aktuell.
    """
    if not state.orders:
        return

    live_ids  = {o["orderId"] for o in ex.open_orders(symbol)}
    filled    = {oid: o for oid, o in state.orders.items() if oid not in live_ids}

    for oid, order in filled.items():
        side, price, qty = order["side"], order["price"], order["qty"]
        log.info(f"  {symbol}: Filled {side} {qty} @ ${price:.4f}")

        # Exposure tracken
        if side == "Buy":
            state.exposure += qty * price
        else:
            state.exposure -= qty * price

        # Gegenorder platzieren (Grid-Mechanik: Profit beim nächsten Level)
        if side == "Buy":
            counter_price = round(price * (1 + config.GRID_DISTANCE), 8)
            counter_side  = "Sell"
        else:
            counter_price = round(price * (1 - config.GRID_DISTANCE), 8)
            counter_side  = "Buy"

        # Exposure-Guard
        if abs(state.exposure) < config.MAX_EXPOSURE_USD:
            new_oid = ex.limit(symbol, counter_side, qty, counter_price)
            if new_oid:
                state.orders[new_oid] = {
                    "side": counter_side, "price": counter_price, "qty": qty
                }
                log.info(f"  {symbol}: Counter-Order {counter_side} @ ${counter_price:.4f}")
        else:
            log.warning(f"  {symbol}: Exposure ${state.exposure:.1f} ≥ Max → keine Counter-Order")

        del state.orders[oid]


def _needs_recenter(price: float, state: GridState) -> bool:
    """True wenn Preis mehr als RECENTER_THRESH vom Grid-Mitte abgedriftet."""
    if not state.mid:
        return False
    drift = abs(price - state.mid) / state.mid
    return drift > config.RECENTER_THRESH


# ── Pro-Symbol Hauptlogik ─────────────────────────────────────
def run_symbol(symbol: str, ex: Exchange, state: GridState, engine):
    df = load_candles(engine, symbol)
    if len(df) < config.ADX_PERIOD + 10:
        log.warning(f"  {symbol}: Zu wenige Candles ({len(df)})")
        return

    price = ex.price(symbol)
    if not price:
        return

    reg = analytics.regime(
        df,
        atr_period=config.ATR_PERIOD, adx_period=config.ADX_PERIOD,
        atr_pct_max=config.ATR_PCT_MAX, adx_max=config.ADX_MAX
    )

    # ── 1. Breakout-Check ────────────────────────────────────
    if state.active:
        if price > state.upper_band or price < state.lower_band:
            direction = "↑" if price > state.upper_band else "↓"
            log.warning(
                f"[BREAKOUT {direction}] {symbol}: "
                f"${price:.4f} | Bands ${state.lower_band:.4f}–${state.upper_band:.4f}"
            )
            _shutdown_grid(symbol, ex, state)
            return

    # ── 2. Fill-Sync ─────────────────────────────────────────
    if state.active:
        _sync_fills(symbol, ex, state)

    # ── 3. Re-Centering ──────────────────────────────────────
    if state.active and _needs_recenter(price, state):
        drift_pct = abs(price - state.mid) / state.mid * 100
        log.info(f"  {symbol}: Re-Center – Drift {drift_pct:.2f}% → neues Grid @ ${price:.4f}")
        ex.cancel_all(symbol)
        upper, lower = analytics.breakout_bands(df, config.ATR_PERIOD, config.BREAKOUT_MULT)
        state.upper_band = upper
        state.lower_band = lower
        _place_grid(symbol, price, ex, state)
        return

    # ── 4. Regime-Check ──────────────────────────────────────
    status = (
        f"ranging={reg['ranging']} "
        f"ATR%={reg['atr_pct']:.4f} "
        f"ADX={reg['adx']:.1f} "
        f"Price=${price:.4f}"
    )
    log.info(f"  {symbol}: {status}")

    if not reg["ranging"]:
        if state.active:
            log.info(f"  {symbol}: Range verlassen → Grid OFF")
            _shutdown_grid(symbol, ex, state)
        return

    # ── 5. Grid starten ──────────────────────────────────────
    if not state.active:
        upper, lower = analytics.breakout_bands(df, config.ATR_PERIOD, config.BREAKOUT_MULT)
        state.upper_band = upper
        state.lower_band = lower
        _place_grid(symbol, price, ex, state)


# ── Main Loop ─────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("GRID BOT  ·  ADX + ATR  ·  Multi-Coin")
    log.info(f"  Levels:      {config.GRID_LEVELS}×2  ({config.GRID_DISTANCE*100:.1f}% Abstand)")
    log.info(f"  Risk/Level:  ${config.RISK_PER_LEVEL}  |  Max: ${config.MAX_EXPOSURE_USD}")
    log.info(f"  ADX max:     {config.ADX_MAX}  |  ATR% max: {config.ATR_PCT_MAX*100:.1f}%")
    log.info(f"  Breakout:    {config.BREAKOUT_MULT}×ATR  |  Re-Center: {config.RECENTER_THRESH*100:.1f}%")
    log.info("=" * 60)

    engine = create_engine(config.DB_URL)
    ex     = Exchange(config.API_KEY, config.API_SECRET, config.TESTNET)
    states: dict[str, GridState] = {}

    while _running:
        try:
            # Coin-Selector: offen (Fallback config.SYMBOLS)
            symbols = config.SYMBOLS
            if not symbols:
                log.warning("Keine Coins konfiguriert – warte...")
                time.sleep(config.CHECK_INTERVAL)
                continue

            for symbol in symbols:
                if symbol not in states:
                    states[symbol] = GridState()
                try:
                    run_symbol(symbol, ex, states[symbol], engine)
                except Exception as e:
                    log.error(f"{symbol}: Fehler: {e}", exc_info=True)

        except Exception as e:
            log.error(f"Main Loop Error: {e}", exc_info=True)
            time.sleep(30)

        time.sleep(config.CHECK_INTERVAL)

    # ── Graceful Shutdown ─────────────────────────────────────
    log.info("Bot wird beendet – alle Grids schließen...")
    for symbol, state in states.items():
        if state.active:
            _shutdown_grid(symbol, ex, state)
    log.info("Bye.")


if __name__ == "__main__":
    main()