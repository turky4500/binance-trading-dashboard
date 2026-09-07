# -*- coding: utf-8 -*-
"""
Trend Confluence indicator — inspired by ChartPrime & Infinity Algo.
Combines multiple trend indicators and scores their alignment.
The more indicators agree, the stronger the signal.
"""
import numpy as np
import pandas as pd
from .indicators import ema, rsi, macd, atr, supertrend


def _adx(df, n=14):
    """Average Directional Index — measures trend strength regardless of direction."""
    h, l, c = df['h'], df['l'], df['c']
    up = h - h.shift(1)
    down = l.shift(1) - l
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr_val = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_val.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_val.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx = dx.ewm(alpha=1 / n, adjust=False).mean()
    return adx.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def analyze_confluence(df, timeframe='4h'):
    """Analyze trend confluence from multiple indicators.
    Returns a dict with per-indicator votes and overall score."""
    n = len(df)
    if n < 200:
        return {'timeframe': timeframe, 'score': 0, 'bias': 'NEUTRAL', 'indicators': {}}

    c = df['c'].values
    close = float(c[-1])
    ema20 = float(df['ema20'].iloc[-1]) if 'ema20' in df else float(ema(df['c'], 20).iloc[-1])
    ema50 = float(df['ema50'].iloc[-1]) if 'ema50' in df else float(ema(df['c'], 50).iloc[-1])
    ema200 = float(df['ema200'].iloc[-1]) if 'ema200' in df else float(ema(df['c'], 200).iloc[-1])
    rsi_val = float(df['rsi'].iloc[-1]) if 'rsi' in df else float(rsi(df['c']).iloc[-1])
    macd_v, macd_s, macd_h = macd(df['c'])
    macd_val = float(macd_v.iloc[-1])
    macd_sig = float(macd_s.iloc[-1])
    macd_hist = float(macd_h.iloc[-1])
    macd_hist_prev = float(macd_h.iloc[-2]) if n > 1 else 0
    atr_val = float(df['atr'].iloc[-1]) if 'atr' in df else float(atr(df).iloc[-1])
    st_line, st_dir = supertrend(df)
    st_direction = int(st_dir.iloc[-1])
    adx_v, plus_di, minus_di = _adx(df)
    adx_val = float(adx_v.iloc[-1])
    vol_ratio = float(df['vol_ratio'].iloc[-1]) if 'vol_ratio' in df else 1.0

    indicators = {}
    score = 0  # out of 100

    # 1. EMA Cross (9/21) — 15 points
    ema9 = float(ema(df['c'], 9).iloc[-1])
    ema21 = float(ema(df['c'], 21).iloc[-1])
    if ema9 > ema21:
        indicators['ema_cross'] = {'vote': 'BULLISH', 'strength': 80, 'detail': f'EMA9 ({ema9:.4f}) > EMA21 ({ema21:.4f})'}
        score += 15
    elif ema9 < ema21:
        indicators['ema_cross'] = {'vote': 'BEARISH', 'strength': 80, 'detail': f'EMA9 ({ema9:.4f}) < EMA21 ({ema21:.4f})'}
        score += 0
    else:
        indicators['ema_cross'] = {'vote': 'NEUTRAL', 'strength': 30, 'detail': 'EMA9 ≈ EMA21'}

    # 2. EMA Trend (50/200) — 15 points
    if close > ema50 > ema200:
        indicators['ema_trend'] = {'vote': 'BULLISH', 'strength': 90, 'detail': f'Price > EMA50 > EMA200'}
        score += 15
    elif close < ema50 < ema200:
        indicators['ema_trend'] = {'vote': 'BEARISH', 'strength': 90, 'detail': f'Price < EMA50 < EMA200'}
        score += 0
    else:
        indicators['ema_trend'] = {'vote': 'NEUTRAL', 'strength': 40, 'detail': 'Mixed EMA alignment'}
        score += 7

    # 3. SuperTrend — 20 points
    if st_direction == 1:
        indicators['supertrend'] = {'vote': 'BULLISH', 'strength': 85, 'detail': f'SuperTrend UP at {st_line.iloc[-1]:.4f}'}
        score += 20
    elif st_direction == -1:
        indicators['supertrend'] = {'vote': 'BEARISH', 'strength': 85, 'detail': f'SuperTrend DOWN at {st_line.iloc[-1]:.4f}'}
        score += 0
    else:
        indicators['supertrend'] = {'vote': 'NEUTRAL', 'strength': 30, 'detail': 'SuperTrend flat'}
        score += 10

    # 4. ADX strength — 15 points
    if adx_val >= 25:
        adx_dir = 'BULLISH' if plus_di.iloc[-1] > minus_di.iloc[-1] else 'BEARISH'
        indicators['adx'] = {'vote': adx_dir, 'strength': min(100, int(adx_val * 2)), 'detail': f'ADX {adx_val:.1f} (strong trend)'}
        if adx_dir == 'BULLISH':
            score += 15
        else:
            score += 0
    elif adx_val >= 20:
        indicators['adx'] = {'vote': 'WEAK_TREND', 'strength': 50, 'detail': f'ADX {adx_val:.1f} (developing)'}
        score += 7
    else:
        indicators['adx'] = {'vote': 'RANGING', 'strength': 20, 'detail': f'ADX {adx_val:.1f} (no trend)'}
        score += 3

    # 5. MACD histogram — 15 points
    if macd_hist > 0 and macd_hist > macd_hist_prev:
        indicators['macd'] = {'vote': 'BULLISH', 'strength': 85, 'detail': f'MACD histogram rising ({macd_hist:.6f})'}
        score += 15
    elif macd_hist < 0 and macd_hist < macd_hist_prev:
        indicators['macd'] = {'vote': 'BEARISH', 'strength': 85, 'detail': f'MACD histogram falling ({macd_hist:.6f})'}
        score += 0
    elif macd_hist > 0:
        indicators['macd'] = {'vote': 'BULLISH_WEAK', 'strength': 55, 'detail': f'MACD histogram positive but flat'}
        score += 10
    elif macd_hist < 0:
        indicators['macd'] = {'vote': 'BEARISH_WEAK', 'strength': 55, 'detail': f'MACD histogram negative but flat'}
        score += 5
    else:
        indicators['macd'] = {'vote': 'NEUTRAL', 'strength': 30, 'detail': 'MACD at zero'}
        score += 7

    # 6. Volume confirmation — 10 points
    if vol_ratio >= 2.0:
        vol_dir = 'BULLISH' if c[-1] > c[-2] else 'BEARISH'
        indicators['volume'] = {'vote': vol_dir, 'strength': 80, 'detail': f'Volume ratio {vol_ratio:.2f}x (high)'}
        score += 10 if vol_dir == 'BULLISH' else 0
    elif vol_ratio >= 1.2:
        indicators['volume'] = {'vote': 'CONFIRMING', 'strength': 55, 'detail': f'Volume ratio {vol_ratio:.2f}x (normal)'}
        score += 5
    else:
        indicators['volume'] = {'vote': 'WEAK', 'strength': 25, 'detail': f'Volume ratio {vol_ratio:.2f}x (low)'}
        score += 2

    # 7. RSI position — 10 points
    if 50 <= rsi_val <= 70:
        indicators['rsi'] = {'vote': 'BULLISH', 'strength': 70, 'detail': f'RSI {rsi_val:.1f} (healthy momentum)'}
        score += 10
    elif 30 <= rsi_val < 50:
        indicators['rsi'] = {'vote': 'BEARISH', 'strength': 70, 'detail': f'RSI {rsi_val:.1f} (weak momentum)'}
        score += 0
    elif rsi_val > 70:
        indicators['rsi'] = {'vote': 'OVERBOUGHT', 'strength': 40, 'detail': f'RSI {rsi_val:.1f} (overbought)'}
        score += 5
    else:
        indicators['rsi'] = {'vote': 'OVERSOLD', 'strength': 40, 'detail': f'RSI {rsi_val:.1f} (oversold)'}
        score += 5

    # Overall bias
    bullish_votes = sum(1 for ind in indicators.values() if 'BULLISH' in ind.get('vote', ''))
    bearish_votes = sum(1 for ind in indicators.values() if 'BEARISH' in ind.get('vote', ''))

    if bullish_votes > bearish_votes + 1:
        bias = 'BULLISH'
    elif bearish_votes > bullish_votes + 1:
        bias = 'BEARISH'
    else:
        bias = 'NEUTRAL'

    return {
        'timeframe': timeframe,
        'score': min(100, score),
        'bias': bias,
        'bullish_votes': bullish_votes,
        'bearish_votes': bearish_votes,
        'indicators': indicators,
    }


def analyze_confluence_multi(tf_dict):
    """Analyze confluence across multiple timeframes."""
    results = {}
    total_score = 0
    count = 0
    for tf_name, df in tf_dict.items():
        r = analyze_confluence(df, tf_name)
        results[tf_name] = r
        total_score += r['score']
        count += 1

    avg_score = total_score // count if count else 0

    # Weighted bias: higher timeframes matter more
    weights = {'15m': 1, '1h': 2, '4h': 3, '1d': 4}
    weighted_long = 0
    weighted_short = 0
    total_weight = 0
    for tf_name, r in results.items():
        w = weights.get(tf_name, 1)
        total_weight += w
        if r['bias'] == 'BULLISH':
            weighted_long += w
        elif r['bias'] == 'BEARISH':
            weighted_short += w

    if total_weight > 0:
        if weighted_long > weighted_short:
            overall_bias = 'BULLISH'
        elif weighted_short > weighted_long:
            overall_bias = 'BEARISH'
        else:
            overall_bias = 'NEUTRAL'
    else:
        overall_bias = 'NEUTRAL'

    return {
        'overall_bias': overall_bias,
        'overall_score': avg_score,
        'per_timeframe': results,
    }
