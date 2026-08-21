# -*- coding: utf-8 -*-
"""
Deterministic technical indicators — pure pandas/numpy, no LLM involved.
Every number on the dashboard is computed from real Binance market data.
"""
import numpy as np
import pandas as pd


def klines_to_df(k):
    cols = ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tb', 'tq', 'ig']
    df = pd.DataFrame(k)
    df.columns = cols[:df.shape[1]]  # accept 6-field (minimal) or 12-field (Binance) rows
    for col in ['o', 'h', 'l', 'c', 'v']:
        df[col] = df[col].astype(float)
    if 'qv' in df.columns:
        df['qv'] = df['qv'].astype(float)
    df['t'] = pd.to_datetime(df['t'], unit='ms', utc=True)
    return df


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    ru = up.ewm(alpha=1 / n, adjust=False).mean()
    rd = dn.ewm(alpha=1 / n, adjust=False).mean()
    rs = ru / rd.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.mask((rd == 0) & (ru > 0), 100.0)  # no losses at all -> RSI 100
    return out.fillna(50.0)


def macd(s, f=12, sl=26, sg=9):
    m = ema(s, f) - ema(s, sl)
    sig = m.ewm(span=sg, adjust=False).mean()
    return m, sig, m - sig


def atr(df, n=14):
    h, l, c = df['h'], df['l'], df['c']
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, period=10, mult=3.0):
    """Classic ATR-based SuperTrend. Returns (line, direction) where direction is +1 (up) / -1 (down)."""
    a = atr(df, period)
    hl2 = (df['h'] + df['l']) / 2
    ub = (hl2 + mult * a).values
    lb = (hl2 - mult * a).values
    close = df['c'].values
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float), pd.Series(dtype=int)
    fu = np.empty(n)
    fl = np.empty(n)
    fu[0], fl[0] = ub[0], lb[0]
    trend = np.ones(n, dtype=int)
    for i in range(1, n):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if close[i] > fu[i - 1]:
            trend[i] = 1
        elif close[i] < fl[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    line = np.where(trend == 1, fl, fu)
    return pd.Series(line, index=df.index), pd.Series(trend, index=df.index)


def enrich(df, st_period=10, st_mult=3.0):
    """Add EMA20/50/200, RSI(14), MACD, ATR(14), session VWAP, volume ratios, SuperTrend."""
    d = df.copy()
    d['ema20'] = ema(d['c'], 20)
    d['ema50'] = ema(d['c'], 50)
    d['ema200'] = ema(d['c'], 200)
    d['rsi'] = rsi(d['c'])
    d['macd'], d['macd_s'], d['macd_h'] = macd(d['c'])
    d['atr'] = atr(d)
    d['vma20'] = d['v'].rolling(20).mean()
    d['vol_ratio'] = (d['v'] / d['vma20']).fillna(1.0)
    d['st_line'], d['st_dir'] = supertrend(d, period=st_period, mult=st_mult)
    day = d['t'].dt.floor('D')
    tp = (d['h'] + d['l'] + d['c']) / 3
    cumv = d.groupby(day)['v'].cumsum()
    cumtpv = (tp * d['v']).groupby(day).cumsum()
    d['vwap'] = cumtpv / cumv.replace(0, np.nan)
    return d


def swings(df, k=3):
    """Fractal swing highs/lows: [list of (index, price, timestamp)]."""
    h = df['h'].values
    l = df['l'].values
    n = len(df)
    highs, lows = [], []
    for i in range(k, n - k):
        if h[i] == max(h[i - k:i + k + 1]):
            highs.append((int(i), float(h[i]), df['t'].iloc[i]))
        if l[i] == min(l[i - k:i + k + 1]):
            lows.append((int(i), float(l[i]), df['t'].iloc[i]))
    return highs, lows


def round_to(price):
    """Snap a price to a human-friendly tick for targets/levels."""
    p = float(price)
    if p >= 5000:
        return round(p / 50) * 50
    if p >= 500:
        return round(p / 5) * 5
    if p >= 50:
        return round(p)
    if p >= 5:
        return round(p * 2) / 2
    if p >= 1:
        return round(p, 1)
    if p >= 0.1:
        return round(p, 3)
    if p >= 0.01:
        return round(p, 4)
    return round(p, 6)


def fmt_price(p):
    """Price formatting with sensible precision for any magnitude."""
    p = float(p)
    if p >= 1000:
        return f"{p:,.1f}"
    if p >= 100:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.3f}"
    if p >= 0.1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.5f}"
    return f"{p:.7f}"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def snap_to_level(target, levels, tolerance_pct=0.25):
    """Snap `target` to the nearest level in `levels` within tolerance (ratio)."""
    best, best_dist = target, tolerance_pct
    for lv in levels:
        if lv and lv > 0:
            d = abs(lv - target) / target
            if d < best_dist:
                best_dist, best = d, lv
    return float(best)
