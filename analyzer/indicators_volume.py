# -*- coding: utf-8 -*-
"""
Volume Profile indicator — inspired by AlgoAlpha & EXCAVO.
Computes: POC (Point of Control), VAH/VAL (Value Area),
HVN/LVN (High/Low Volume Nodes), volume-weighted trend.
"""
import numpy as np
import pandas as pd


def volume_profile(df, num_bins=24):
    """Compute Volume Profile — volume at each price level.
    Returns POC, VAH, VAL, HVN, LVN, and the full profile."""
    if len(df) < 20:
        return None

    h, l, c, v = df['h'].values, df['l'].values, df['c'].values, df['v'].values
    price_min = l.min()
    price_max = h.max()

    if price_max <= price_min:
        return None

    # Create price bins
    bin_size = (price_max - price_min) / num_bins
    if bin_size <= 0:
        return None

    profile = np.zeros(num_bins)
    bin_centers = np.linspace(price_min + bin_size / 2, price_max - bin_size / 2, num_bins)

    # Distribute volume across bins
    for i in range(len(df)):
        candle_range = h[i] - l[i]
        if candle_range <= 0:
            # Doji — all volume at close
            bin_idx = int((c[i] - price_min) / bin_size)
            bin_idx = max(0, min(num_bins - 1, bin_idx))
            profile[bin_idx] += v[i]
        else:
            # Distribute proportionally across the candle range
            low_bin = int((l[i] - price_min) / bin_size)
            high_bin = int((h[i] - price_min) / bin_size)
            low_bin = max(0, min(num_bins - 1, low_bin))
            high_bin = max(0, min(num_bins - 1, high_bin))
            bins_covered = high_bin - low_bin + 1
            vol_per_bin = v[i] / bins_covered
            for b in range(low_bin, high_bin + 1):
                profile[b] += vol_per_bin

    # Point of Control (POC) — highest volume price
    poc_idx = np.argmax(profile)
    poc = float(bin_centers[poc_idx])
    poc_volume = float(profile[poc_idx])

    # Value Area (70% of volume centered on POC)
    total_volume = profile.sum()
    target_volume = total_volume * 0.70
    current_volume = profile[poc_idx]
    upper_idx = poc_idx
    lower_idx = poc_idx

    while current_volume < target_volume and (upper_idx < num_bins - 1 or lower_idx > 0):
        expand_up = profile[upper_idx + 1] if upper_idx < num_bins - 1 else 0
        expand_down = profile[lower_idx - 1] if lower_idx > 0 else 0

        if expand_up >= expand_down and upper_idx < num_bins - 1:
            upper_idx += 1
            current_volume += profile[upper_idx]
        elif lower_idx > 0:
            lower_idx -= 1
            current_volume += profile[lower_idx]
        else:
            break

    vah = float(bin_centers[upper_idx])
    val = float(bin_centers[lower_idx])

    # High Volume Nodes (HVN) — bins with volume > 1.5x average
    avg_volume = profile.mean()
    hvn = []
    lvn = []
    for i in range(num_bins):
        if profile[i] > avg_volume * 1.5:
            hvn.append({
                'price': float(bin_centers[i]),
                'volume': float(profile[i]),
                'strength': min(100, int(profile[i] / avg_volume * 50)),
            })
        elif profile[i] < avg_volume * 0.5:
            lvn.append({
                'price': float(bin_centers[i]),
                'volume': float(profile[i]),
                'strength': min(100, int((1 - profile[i] / avg_volume) * 80)),
            })

    return {
        'poc': poc,
        'poc_volume': poc_volume,
        'vah': vah,
        'val': val,
        'value_area_volume': current_volume,
        'total_volume': total_volume,
        'hvn': sorted(hvn, key=lambda x: x['strength'], reverse=True)[:5],
        'lvn': sorted(lvn, key=lambda x: x['strength'], reverse=True)[:5],
        'profile_bins': [{'price': float(bin_centers[i]), 'volume': float(profile[i])} for i in range(num_bins)],
    }


def volume_trend(df, period=20):
    """Analyze volume trend — is buying or selling pressure dominant?
    Returns volume-weighted trend direction and strength."""
    n = len(df)
    if n < period + 5:
        return {'direction': 'NEUTRAL', 'strength': 30}

    v = df['v'].values
    c = df['c'].values
    o = df['o'].values

    # Buying volume: volume on up candles
    # Selling volume: volume on down candles
    buy_vol = 0
    sell_vol = 0

    for i in range(-period, 0):
        if c[i] >= o[i]:
            buy_vol += v[i]
        else:
            sell_vol += v[i]

    total = buy_vol + sell_vol
    if total <= 0:
        return {'direction': 'NEUTRAL', 'strength': 20}

    buy_ratio = buy_vol / total
    sell_ratio = sell_vol / total

    if buy_ratio > 0.6:
        direction = 'BULLISH'
        strength = min(100, int(buy_ratio * 120))
    elif sell_ratio > 0.6:
        direction = 'BEARISH'
        strength = min(100, int(sell_ratio * 120))
    else:
        direction = 'NEUTRAL'
        strength = 40

    return {
        'direction': direction,
        'strength': strength,
        'buy_volume': round(buy_vol, 2),
        'sell_volume': round(sell_vol, 2),
        'buy_ratio': round(buy_ratio * 100, 1),
        'sell_ratio': round(sell_ratio * 100, 1),
    }


def volume_momentum(df, short=5, long=20):
    """Compare short-term vs long-term volume — rising volume confirms trend."""
    n = len(df)
    if n < long + 5:
        return {'signal': 'NEUTRAL', 'strength': 20}

    v = df['v'].values
    short_avg = np.mean(v[-short:])
    long_avg = np.mean(v[-long:])

    if long_avg <= 0:
        return {'signal': 'NEUTRAL', 'strength': 20}

    ratio = short_avg / long_avg
    c = df['c'].values
    price_up = c[-1] > c[-long]

    if ratio > 1.5 and price_up:
        signal = 'BULLISH_CONFIRMATION'
        strength = min(100, int(ratio * 40))
    elif ratio > 1.5 and not price_up:
        signal = 'BEARISH_CONFIRMATION'
        strength = min(100, int(ratio * 40))
    elif ratio < 0.7 and price_up:
        signal = 'WEAK_BULL'  # price up but volume declining
        strength = 40
    elif ratio < 0.7 and not price_up:
        signal = 'WEAK_BEAR'  # price down but volume declining
        strength = 40
    else:
        signal = 'NEUTRAL'
        strength = 30

    return {
        'signal': signal,
        'strength': strength,
        'volume_ratio': round(ratio, 2),
        'short_avg': round(short_avg, 2),
        'long_avg': round(long_avg, 2),
    }


def analyze_volume(df, timeframe='4h'):
    """Full volume analysis for a single timeframe."""
    vp = volume_profile(df)
    vt = volume_trend(df)
    vm = volume_momentum(df)

    # Overall score
    score = 0

    # Volume trend: +35
    if vt['direction'] == 'BULLISH':
        score += 35
    elif vt['direction'] == 'BEARISH':
        score += 0
    else:
        score += 15

    # Volume momentum: +35
    if 'BULLISH' in vm['signal']:
        score += 35
    elif 'BEARISH' in vm['signal']:
        score += 0
    else:
        score += 15

    # Volume profile context: +30
    if vp:
        close = float(df['c'].iloc[-1])
        if close > vp['poc']:
            score += 20
        else:
            score += 5
        if vp['val'] < close < vp['vah']:
            score += 10  # in value area
    else:
        score += 15

    # Determine bias
    if score >= 60:
        bias = 'BULLISH'
    elif score <= 40:
        bias = 'BEARISH'
    else:
        bias = 'NEUTRAL'

    return {
        'timeframe': timeframe,
        'score': min(100, score),
        'bias': bias,
        'volume_profile': vp,
        'volume_trend': vt,
        'volume_momentum': vm,
    }


def analyze_volume_multi(tf_dict):
    """Analyze volume across multiple timeframes."""
    results = {}
    total_score = 0
    count = 0

    for tf_name, df in tf_dict.items():
        r = analyze_volume(df, tf_name)
        results[tf_name] = r
        total_score += r['score']
        count += 1

    avg_score = total_score // count if count else 0

    # Weighted bias
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

    if weighted_long > weighted_short:
        overall_bias = 'BULLISH'
    elif weighted_short > weighted_long:
        overall_bias = 'BEARISH'
    else:
        overall_bias = 'NEUTRAL'

    return {
        'overall_bias': overall_bias,
        'overall_score': avg_score,
        'per_timeframe': results,
    }
