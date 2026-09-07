# -*- coding: utf-8 -*-
"""
Liquidity Zones indicator — inspired by Zeiierman & AlgoAlpha.
Detects: Swing-based liquidity zones, equal highs/lows,
liquidity grabs, stop hunt patterns.
"""
import numpy as np
import pandas as pd
from .indicators import swings, atr


def detect_liquidity_zones(df, k=3):
    """Detect liquidity zones from swing highs and lows.
    Returns zones where stop losses are likely clustered."""
    zones = []
    n = len(df)
    if n < k * 2 + 10:
        return zones

    h, l, c = df['h'].values, df['l'].values, df['c'].values
    t = df['t'].values
    atr_val = float(atr(df).iloc[-1]) if atr_val_valid(df) else 0
    if atr_val <= 0:
        return zones

    highs, lows = swings(df, k=k)

    # Zone 1: Swing highs as resistance liquidity (short stops above)
    for idx, price, timestamp in highs[-5:]:
        strength = _zone_strength(df, price, atr_val, idx, 'resistance')
        zones.append({
            'type': 'RESISTANCE_LIQUIDITY',
            'direction': 'SHORT',
            'price': float(price),
            'strength': strength,
            'timestamp': str(timestamp),
            'label': f'Swing High {float(price):.4f}',
        })

    # Zone 2: Swing lows as support liquidity (long stops below)
    for idx, price, timestamp in lows[-5:]:
        strength = _zone_strength(df, price, atr_val, idx, 'support')
        zones.append({
            'type': 'SUPPORT_LIQUIDITY',
            'direction': 'LONG',
            'price': float(price),
            'strength': strength,
            'timestamp': str(timestamp),
            'label': f'Swing Low {float(price):.4f}',
        })

    # Zone 3: Equal highs/lows (tight clusters = high liquidity)
    eq_highs = _find_equal_levels(highs, atr_val, tolerance=0.1)
    eq_lows = _find_equal_levels(lows, atr_val, tolerance=0.1)

    for price, count in eq_highs:
        zones.append({
            'type': 'EQUAL_HIGHS',
            'direction': 'SHORT',
            'price': float(price),
            'strength': min(100, 50 + count * 15),
            'label': f'{count}x Equal Highs @ {float(price):.4f}',
        })

    for price, count in eq_lows:
        zones.append({
            'type': 'EQUAL_LOWS',
            'direction': 'LONG',
            'price': float(price),
            'strength': min(100, 50 + count * 15),
            'label': f'{count}x Equal Lows @ {float(price):.4f}',
        })

    return sorted(zones, key=lambda z: z['strength'], reverse=True)[:10]


def detect_stop_hunts(df, lookback=20):
    """Detect stop hunt patterns — price spikes through a level then reverses.
    This is when institutions grab retail stop losses."""
    hunts = []
    n = len(df)
    if n < lookback + 3:
        return hunts

    h, l, c, o = df['h'].values, df['l'].values, df['c'].values, df['o'].values
    t = df['t'].values
    atr_val = float(atr(df).iloc[-1]) if atr_val_valid(df) else 0
    if atr_val <= 0:
        return hunts

    recent_highs = h[-lookback:-2]
    recent_lows = l[-lookback:-2]

    max_high = recent_highs.max()
    min_low = recent_lows.min()

    # Bullish stop hunt: wick below recent lows, then close above
    if l[-1] < min_low and c[-1] > min_low:
        hunt_depth = (min_low - l[-1]) / atr_val
        if hunt_depth > 0.3:
            hunts.append({
                'type': 'BULLISH_STOP_HUNT',
                'direction': 'LONG',
                'swept_level': float(min_low),
                'wick_extreme': float(l[-1]),
                'strength': min(100, int(60 + hunt_depth * 15)),
                'timestamp': str(t[-1]),
                'detail': f'Swept {hunt_depth:.1f} ATR below {float(min_low):.4f}',
            })

    # Bearish stop hunt: wick above recent highs, then close below
    if h[-1] > max_high and c[-1] < max_high:
        hunt_depth = (h[-1] - max_high) / atr_val
        if hunt_depth > 0.3:
            hunts.append({
                'type': 'BEARISH_STOP_HUNT',
                'direction': 'SHORT',
                'swept_level': float(max_high),
                'wick_extreme': float(h[-1]),
                'strength': min(100, int(60 + hunt_depth * 15)),
                'timestamp': str(t[-1]),
                'detail': f'Swept {hunt_depth:.1f} ATR above {float(max_high):.4f}',
            })

    return hunts[:5]


def detect_liquidity_grab(df, k=3):
    """Detect liquidity grab patterns — price takes out a swing level then reverses strongly."""
    grabs = []
    n = len(df)
    if n < k * 2 + 5:
        return grabs

    h, l, c, o = df['h'].values, df['l'].values, df['c'].values, df['o'].values
    t = df['t'].values
    atr_val = float(atr(df).iloc[-1]) if atr_val_valid(df) else 0
    if atr_val <= 0:
        return grabs

    highs, lows = swings(df, k=k)

    # Check if last candle grabbed a swing level and reversed
    for _, price, timestamp in lows[-3:]:
        # Bullish grab: wick below swing low, close above with strong body
        if l[-1] < price and c[-1] > price:
            body = abs(c[-1] - o[-1])
            wick = price - l[-1]
            if body > wick and body > 0.5 * atr_val:
                grabs.append({
                    'type': 'BULLISH_LIQUIDITY_GRAB',
                    'direction': 'LONG',
                    'grabbed_level': float(price),
                    'strength': min(100, int(65 + (body / atr_val) * 15)),
                    'timestamp': str(t[-1]),
                    'detail': f'Grabbed liquidity at {float(price):.4f} with strong reversal',
                })

    for _, price, timestamp in highs[-3:]:
        # Bearish grab: wick above swing high, close below with strong body
        if h[-1] > price and c[-1] < price:
            body = abs(c[-1] - o[-1])
            wick = h[-1] - price
            if body > wick and body > 0.5 * atr_val:
                grabs.append({
                    'type': 'BEARISH_LIQUIDITY_GRAB',
                    'direction': 'SHORT',
                    'grabbed_level': float(price),
                    'strength': min(100, int(65 + (body / atr_val) * 15)),
                    'timestamp': str(t[-1]),
                    'detail': f'Grabbed liquidity at {float(price):.4f} with strong reversal',
                })

    return grabs[:5]


def analyze_liquidity(df, timeframe='4h'):
    """Full liquidity analysis for a single timeframe."""
    zones = detect_liquidity_zones(df)
    hunts = detect_stop_hunts(df)
    grabs = detect_liquidity_grab(df)

    all_signals = zones + hunts + grabs
    long_signals = sum(1 for s in all_signals if s.get('direction') == 'LONG')
    short_signals = sum(1 for s in all_signals if s.get('direction') == 'SHORT')

    if long_signals > short_signals:
        bias = 'BULLISH'
        confidence = min(100, 35 + long_signals * 12)
    elif short_signals > long_signals:
        bias = 'BEARISH'
        confidence = min(100, 35 + short_signals * 12)
    else:
        bias = 'NEUTRAL'
        confidence = 25

    return {
        'timeframe': timeframe,
        'bias': bias,
        'confidence': confidence,
        'zones': zones[:5],
        'stop_hunts': hunts[:3],
        'liquidity_grabs': grabs[:3],
        'long_signals': long_signals,
        'short_signals': short_signals,
    }


def analyze_liquidity_multi(tf_dict):
    """Analyze liquidity across multiple timeframes."""
    results = {}
    all_signals = []
    for tf_name, df in tf_dict.items():
        r = analyze_liquidity(df, tf_name)
        results[tf_name] = r
        all_signals.extend(r.get('zones', []))
        all_signals.extend(r.get('stop_hunts', []))
        all_signals.extend(r.get('liquidity_grabs', []))

    long_count = sum(1 for s in all_signals if s.get('direction') == 'LONG')
    short_count = sum(1 for s in all_signals if s.get('direction') == 'SHORT')

    if long_count > short_count:
        overall_bias = 'BULLISH'
        overall_confidence = min(100, 30 + long_count * 10)
    elif short_count > long_count:
        overall_bias = 'BEARISH'
        overall_confidence = min(100, 30 + short_count * 10)
    else:
        overall_bias = 'NEUTRAL'
        overall_confidence = 25

    return {
        'overall_bias': overall_bias,
        'overall_confidence': overall_confidence,
        'per_timeframe': results,
        'top_signals': sorted(all_signals, key=lambda s: s.get('strength', 0), reverse=True)[:10],
    }


def _zone_strength(df, price, atr_val, candle_idx, zone_type):
    """Calculate strength of a liquidity zone based on reactions."""
    count = 0
    h, l = df['h'].values, df['l'].values
    n = len(df)
    start = max(0, candle_idx - 30)
    for i in range(start, min(candle_idx, n)):
        if zone_type == 'resistance':
            if abs(h[i] - price) < 0.5 * atr_val:
                count += 1
        else:
            if abs(l[i] - price) < 0.5 * atr_val:
                count += 1
    return min(100, 40 + count * 15)


def _find_equal_levels(swings_list, atr_val, tolerance=0.1):
    """Find clusters of swing points at similar prices (equal highs/lows)."""
    if not swings_list or atr_val <= 0:
        return []
    levels = []
    used = set()
    for i, (_, p1, _) in enumerate(swings_list):
        if i in used:
            continue
        count = 1
        for j, (_, p2, _) in enumerate(swings_list):
            if j <= i or j in used:
                continue
            if abs(p1 - p2) / p1 < tolerance:
                count += 1
                used.add(j)
        if count >= 2:
            levels.append((p1, count))
        used.add(i)
    return levels


def atr_val_valid(df):
    """Check if ATR can be computed."""
    try:
        v = float(atr(df).iloc[-1])
        return v == v and v > 0
    except Exception:
        return False
