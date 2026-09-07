# -*- coding: utf-8 -*-
"""
Momentum Pro indicator — inspired by Market Cipher & Quantzee.
Detects: RSI Divergence (regular + hidden), MACD divergence,
Stochastic extremes, WaveTrend oscillator, ROC momentum shifts.
"""
import numpy as np
import pandas as pd
from .indicators import ema, rsi, macd, atr


def _stochastic(df, k_period=14, d_period=3, smooth=3):
    """Stochastic Oscillator (%K and %D)."""
    h = df['h'].rolling(k_period).max()
    l = df['l'].rolling(k_period).min()
    fast_k = 100 * (df['c'] - l) / (h - l).replace(0, np.nan)
    slow_k = fast_k.rolling(smooth).mean()
    slow_d = slow_k.rolling(d_period).mean()
    return slow_k.fillna(50), slow_d.fillna(50)


def _wavetrend(df, hlc3=None, ch1=10, ch2=21):
    """WaveTrend oscillator — detects momentum shifts."""
    if hlc3 is None:
        hlc3 = (df['h'] + df['l'] + df['c']) / 3
    esa = hlc3.ewm(span=ch1, adjust=False).mean()
    d = (hlc3 - esa).abs().ewm(span=ch1, adjust=False).mean()
    ci = (hlc3 - esa) / (2.015 * d).replace(0, np.nan)
    wt1 = ci.ewm(span=ch2, adjust=False).mean()
    wt2 = wt1.rolling(4).mean()
    return wt1.fillna(0), wt2.fillna(0)


def _roc(df, n=12):
    """Rate of Change."""
    return (df['c'] - df['c'].shift(n)) / df['c'].shift(n).replace(0, np.nan) * 100


def _find_divergence(prices, oscillators, lookback=20):
    """Generic divergence detector between price and oscillator.
    Returns list of divergence signals."""
    divergences = []
    n = len(prices)
    if n < lookback + 5:
        return divergences

    prices_arr = prices.values if hasattr(prices, 'values') else np.array(prices)
    osc_arr = oscillators.values if hasattr(oscillators, 'values') else np.array(oscillators)

    for i in range(lookback, n - 1):
        window_p = prices_arr[i - lookback:i + 1]
        window_o = osc_arr[i - lookback:i + 1]

        prev_window_p = prices_arr[max(0, i - 2 * lookback):i - lookback]
        prev_window_o = osc_arr[max(0, i - 2 * lookback):i - lookback]
        if len(prev_window_p) < 5 or len(prev_window_o) < 5:
            continue

        cur_p_min = np.nanmin(window_p)
        prev_p_min = np.nanmin(prev_window_p)
        cur_o_min = np.nanmin(window_o)
        prev_o_min = np.nanmin(prev_window_o)

        # Bullish: lower price low + higher oscillator low
        if cur_p_min < prev_p_min and cur_o_min > prev_o_min:
            strength = min(100, int(60 + abs(cur_o_min - prev_o_min) * 5))
            divergences.append({
                'type': 'BULLISH_DIVERGENCE',
                'direction': 'LONG',
                'strength': strength,
                'price_low': float(cur_p_min),
                'osc_low': float(cur_o_min),
            })

        # Bearish: higher price high + lower oscillator high
        p_max = np.nanmax(window_p)
        o_max = np.nanmax(window_o)
        prev_p_max = np.nanmax(prev_window_p)
        prev_o_max = np.nanmax(prev_window_o)

        if p_max > prev_p_max and o_max < prev_o_max:
            strength = min(100, int(60 + abs(prev_o_max - o_max) * 5))
            divergences.append({
                'type': 'BEARISH_DIVERGENCE',
                'direction': 'SHORT',
                'strength': strength,
                'price_high': float(p_max),
                'osc_high': float(o_max),
            })

    # Deduplicate — keep strongest per type
    seen_types = {}
    for d in divergences:
        key = d['type']
        if key not in seen_types or d['strength'] > seen_types[key]['strength']:
            seen_types[key] = d
    return list(seen_types.values())[:5]


def analyze_momentum(df, timeframe='4h'):
    """Full momentum analysis for a single timeframe."""
    n = len(df)
    if n < 50:
        return {'timeframe': timeframe, 'score': 0, 'bias': 'NEUTRAL', 'signals': []}

    c = df['c']
    close = float(c.iloc[-1])
    signals = []
    score = 0  # out of 100

    # 1. RSI Divergence (regular)
    rsi_vals = rsi(c)
    rsi_div = _find_divergence(c, rsi_vals, lookback=14)
    for d in rsi_div:
        d['indicator'] = 'RSI'
        d['detail'] = f"RSI {d['type'].replace('_', ' ').lower()}"
    signals.extend(rsi_div)

    # 2. MACD Divergence
    macd_v, macd_s, macd_h = macd(c)
    macd_div = _find_divergence(c, macd_h, lookback=14)
    for d in macd_div:
        d['indicator'] = 'MACD'
        d['detail'] = f"MACD histogram {d['type'].replace('_', ' ').lower()}"
    signals.extend(macd_div)

    # 3. WaveTrend Divergence
    wt1, wt2 = _wavetrend(df)
    wt_div = _find_divergence(c, wt1, lookback=14)
    for d in wt_div:
        d['indicator'] = 'WaveTrend'
        d['detail'] = f"WaveTrend {d['type'].replace('_', ' ').lower()}"
    signals.extend(wt_div)

    # 4. Stochastic extremes
    stoch_k, stoch_d = _stochastic(df)
    sk = float(stoch_k.iloc[-1])
    sd = float(stoch_d.iloc[-1])
    sk_prev = float(stoch_k.iloc[-2]) if n > 1 else sk

    if sk < 20 and sd < 20:
        signals.append({
            'type': 'STOCH_OVERSOLD',
            'direction': 'LONG',
            'strength': min(100, int(60 + (20 - sk) * 3)),
            'indicator': 'Stochastic',
            'detail': f'Stoch oversold: %K={sk:.1f} %D={sd:.1f}',
        })
        score += 15
    elif sk > 80 and sd > 80:
        signals.append({
            'type': 'STOCH_OVERBOUGHT',
            'direction': 'SHORT',
            'strength': min(100, int(60 + (sk - 80) * 3)),
            'indicator': 'Stochastic',
            'detail': f'Stoch overbought: %K={sk:.1f} %D={sd:.1f}',
        })
        score += 0

    # Stochastic crossover at extremes
    if sk > sd and sk_prev <= sd and sk < 30:
        signals.append({
            'type': 'STOCH_BULLISH_CROSS',
            'direction': 'LONG',
            'strength': 70,
            'indicator': 'Stochastic',
            'detail': f'Stoch bullish crossover at oversold level',
        })
        score += 10
    elif sk < sd and sk_prev >= sd and sk > 70:
        signals.append({
            'type': 'STOCH_BEARISH_CROSS',
            'direction': 'SHORT',
            'strength': 70,
            'indicator': 'Stochastic',
            'detail': f'Stoch bearish crossover at overbought level',
        })
        score += 0

    # 5. RSI position
    rsi_val = float(rsi_vals.iloc[-1])
    if rsi_val < 30:
        score += 15
    elif rsi_val > 70:
        score += 0
    elif 40 <= rsi_val <= 60:
        score += 8
    else:
        score += 5

    # 6. MACD histogram direction
    macd_hist = float(macd_h.iloc[-1])
    macd_hist_prev = float(macd_h.iloc[-2]) if n > 1 else 0
    if macd_hist > 0 and macd_hist > macd_hist_prev:
        score += 15
    elif macd_hist < 0 and macd_hist < macd_hist_prev:
        score += 0
    elif macd_hist > 0:
        score += 8
    else:
        score += 3

    # 7. WaveTrend position
    wt1_val = float(wt1.iloc[-1])
    wt2_val = float(wt2.iloc[-1])
    if wt1_val > wt2_val and wt1_val < 50:
        score += 10
    elif wt1_val < wt2_val and wt1_val > -50:
        score += 0
    else:
        score += 5

    # 8. Rate of Change
    roc = _roc(df)
    roc_val = float(roc.iloc[-1]) if not pd.isna(roc.iloc[-1]) else 0
    if roc_val > 3:
        score += 10
    elif roc_val < -3:
        score += 0
    else:
        score += 5

    # Determine bias
    bullish_signals = sum(1 for s in signals if s.get('direction') == 'LONG')
    bearish_signals = sum(1 for s in signals if s.get('direction') == 'SHORT')

    if bullish_signals > bearish_signals:
        bias = 'BULLISH'
    elif bearish_signals > bullish_signals:
        bias = 'BEARISH'
    else:
        bias = 'NEUTRAL'

    return {
        'timeframe': timeframe,
        'score': min(100, score),
        'bias': bias,
        'rsi': round(rsi_val, 1),
        'stoch_k': round(sk, 1),
        'stoch_d': round(sd, 1),
        'macd_hist': round(macd_hist, 6),
        'wave_trend': round(wt1_val, 2),
        'roc': round(roc_val, 2),
        'signals': signals[:8],
        'bullish_signals': bullish_signals,
        'bearish_signals': bearish_signals,
    }


def analyze_momentum_multi(tf_dict):
    """Analyze momentum across multiple timeframes."""
    results = {}
    total_score = 0
    count = 0
    all_signals = []

    for tf_name, df in tf_dict.items():
        r = analyze_momentum(df, tf_name)
        results[tf_name] = r
        total_score += r['score']
        count += 1
        all_signals.extend(r.get('signals', []))

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
        'top_signals': sorted(all_signals, key=lambda s: s.get('strength', 0), reverse=True)[:10],
    }
