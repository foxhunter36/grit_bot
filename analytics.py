"""
Pure-function technische Indikatoren.
Kein State, keine Seiteneffekte – nur Daten rein, Werte raus.
"""
import pandas as pd
import numpy as np


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    c, h, l = df["close"], df["high"], df["low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return _ema(tr, period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pdm = h.diff().clip(lower=0).where(h.diff() > (-l.diff()).clip(lower=0), 0.0)
    ndm = (-l.diff()).clip(lower=0).where((-l.diff()) > h.diff().clip(lower=0), 0.0)
    _atr = atr(df, period)
    pdi  = 100 * _ema(pdm, period) / _atr
    ndi  = 100 * _ema(ndm, period) / _atr
    dx   = (100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9))
    return _ema(dx, period)


def regime(df: pd.DataFrame,
           atr_period: int = 14, adx_period: int = 14,
           atr_pct_max: float = 0.015, adx_max: float = 22) -> dict:
    """
    Gibt Marktregime zurück.
    ranging=True  → Grid erlaubt
    ranging=False → Grid off / warten
    """
    last_close  = float(df["close"].iloc[-1])
    last_atr    = float(atr(df, atr_period).iloc[-1])
    last_adx    = float(adx(df, adx_period).iloc[-1])
    atr_pct     = last_atr / last_close if last_close else 999.0

    ranging = atr_pct <= atr_pct_max and last_adx <= adx_max
    return {
        "ranging":  ranging,
        "atr":      last_atr,
        "atr_pct":  atr_pct,
        "adx":      last_adx,
        "close":    last_close,
        "upper":    last_close + last_atr,
        "lower":    last_close - last_atr,
    }


def breakout_bands(df: pd.DataFrame,
                   atr_period: int = 14, mult: float = 1.5) -> tuple[float, float]:
    last_close = float(df["close"].iloc[-1])
    last_atr   = float(atr(df, atr_period).iloc[-1])
    return last_close + mult * last_atr, last_close - mult * last_atr


def grid_levels(mid: float, n: int, dist: float) -> dict[str, list[float]]:
    """Symmetrisches Grid um mid. Buy absteigend, Sell aufsteigend."""
    return {
        "buy":  [round(mid * (1 - i * dist), 8) for i in range(1, n + 1)],
        "sell": [round(mid * (1 + i * dist), 8) for i in range(1, n + 1)],
    }


def qty_for_level(risk_usd: float, price: float, step: float = 0.01) -> float:
    raw = risk_usd / price
    qty = round(raw - (raw % step), 8)
    return max(qty, step)