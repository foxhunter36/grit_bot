"""
Dünne, zustandslose Schicht über pybit.
Gibt immer strukturierte Dicts zurück – kein Exception-Bubbling nach oben.
"""
import logging
from pybit.unified_trading import HTTP

log = logging.getLogger("grid_bot")


class Exchange:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self._c = HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)

    # ── Marktdaten ───────────────────────────────────────────
    def price(self, symbol: str) -> float:
        try:
            r = self._c.get_tickers(category="linear", symbol=symbol)
            return float(r["result"]["list"][0]["lastPrice"])
        except Exception as e:
            log.error(f"price {symbol}: {e}")
            return 0.0

    def positions(self, symbol: str) -> list:
        try:
            return self._c.get_positions(category="linear", symbol=symbol)["result"]["list"]
        except Exception as e:
            log.error(f"positions {symbol}: {e}")
            return []

    def open_orders(self, symbol: str) -> list:
        try:
            return self._c.get_open_orders(category="linear", symbol=symbol)["result"]["list"]
        except Exception as e:
            log.error(f"open_orders {symbol}: {e}")
            return []

    # ── Order-Execution ──────────────────────────────────────
    def limit(self, symbol: str, side: str, qty: float, price: float) -> str | None:
        """Platziert Limit-Order. Gibt order_id zurück oder None."""
        try:
            r = self._c.place_order(
                category="linear", symbol=symbol,
                side=side, orderType="Limit",
                qty=str(qty), price=str(round(price, 6)),
                timeInForce="GTC"
            )
            if r["retCode"] == 0:
                oid = r["result"]["orderId"]
                log.info(f"  {symbol} {side:4s} {qty} @ {price:.4f} → {oid[:8]}")
                return oid
            log.error(f"  {symbol} limit failed: {r['retMsg']}")
        except Exception as e:
            log.error(f"  {symbol} limit exception: {e}", exc_info=True)
        return None

    def cancel_all(self, symbol: str):
        try:
            self._c.cancel_all_orders(category="linear", symbol=symbol)
            log.info(f"  {symbol}: alle Orders gecancelt")
        except Exception as e:
            log.error(f"  cancel_all {symbol}: {e}", exc_info=True)

    def close_market(self, symbol: str, side: str, qty: float):
        """Schließt Position per Market-Order."""
        close_side = "Sell" if side == "Buy" else "Buy"
        try:
            r = self._c.place_order(
                category="linear", symbol=symbol,
                side=close_side, orderType="Market",
                qty=str(qty), reduceOnly=True
            )
            log.info(f"  {symbol}: close {side} {qty} → {r['retCode']}")
        except Exception as e:
            log.error(f"  close_market {symbol}: {e}", exc_info=True)