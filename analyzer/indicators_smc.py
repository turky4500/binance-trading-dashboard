# -*- coding: utf-8 -*-
"""
Smart Money Concepts (SMC) indicator — inspired by LuxAlgo & Quantum Algo.
Detects: Order Blocks, Fair Value Gaps, Break of Structure, Change of Character,
Liquidity Sweeps. Pure pandas/numpy, deterministic.
"""
import numpy as np
import pandas as pd


def detect_order_blocks(df, lookback=20):
    """Detect bullish and bearish Order Blocks.
    Bullish OB: last bearish candle before a strong bullish move.
    Bearish OB: last bullish candle before a strong bearish move.
    Returns list of dicts with price, type, strength, timestamp."""
    blocks = []
    n = len(df)
    if n < lookback + 5:
        return blocks
    h, l, c, o = df['h'].values, df['l'].values, df['c'].values, df['o'].values
    t = df['t'].values
    atr_val = _atr(df, 14)
    if atr_val <= 0:
        return blocks

    for i in range(lookback, n - 3):
        # Bullish OB: bearish candle followed by strong bullish impulse
        if c[i] < o[i]:  # bearish candle
            # Check if next 2-3 candles form a strong bullish move
            move = c[min(i + 3, n - 1)] - c[i]
            if move > 1.5 * atr_val:
                # Check it's a significant level (price reacted before)
                significance = _count_reactions(df, l[i], atr_val, i)
                strength = min(100, int(50 + significance * 10 + (move / atr_val) * 10))
                blocks.append({
                    'price': float(l[i]),
                    'type': 'BULLISH_OB',
                    'direction': 'LONG',
                    'strength': strength,
                    'timestamp': str(t[i]),
                    'top': float(h[i]),
                    'bottom': float(l[i]),
                })

        # Bearish OB: bullish candle followed by strong bearish impulse
        if c[i] > o[i]:  # bullish candle
            move = c[i] - c[min(i + 3, n - 1)]
            if move > 1.5 * atr_val:
                significance = _count_reactions(df, h[i], atr_val, i)
                strength = min(100, int(50 + significance * 10 + (move / atr_val) * 10))
                blocks.append({
                    'price': float(h[i]),
                    'type': 'BEARISH_OB',
                    'direction': 'SHORT',
                    'strength': strength,
                    'timestamp': str(t[i]),
                    'top': float(h[i]),
                    'bottom': float(l[i]),
                })

    # Return only the most recent and strongest OBs
    return sorted(blocks, key=lambda b: b['strength'], reverse=True)[:10]


def detect_fvg(df, lookback=30):
    """Detect Fair Value Gaps (FVG) — imbalance between 3 candles.
    Bullish FVG: candle[2].low > candle[0].high (gap up).
    Bearish FVG: candle[2].high < candle[0].low (gap down)."""
    fvgs = []
    n = len(df)
    if n < lookback + 3:
        return fvgs
    h, l, c = df['h'].values, df['l'].values, df['c'].values
    t = df['t'].values
    atr_val = _atr(df, 14)

    for i in range(max(4, lookback), n - 1):
        # Bullish FVG: gap between candle[0] high and candle[2] low
        if l[i + 1] > h[i - 1] and atr_val > 0:
            gap_size = l[i + 1] - h[i - 1]
            if gap_size > 0.3 * atr_val:
                strength = min(100, int(40 + (gap_size / atr_val) * 20))
                fvgs.append({
                    'top': float(l[i + 1]),
                    'bottom': float(h[i - 1]),
                    'type': 'BULLISH_FVG',
                    'direction': 'LONG',
                    'strength': strength,
                    'timestamp': str(t[i]),
                    'mid': float((l[i + 1] + h[i - 1]) / 2),
                })

        # Bearish FVG: gap between candle[0] low and candle[2] high
        if h[i + 1] < l[i - 1] and atr_val > 0:
            gap_size = l[i - 1] - h[i + 1]
            if gap_size > 0.3 * atr_val:
                strength = min(100, int(40 + (gap_size / atr_val) * 20))
                fvgs.append({
                    'top': float(l[i - 1]),
                    'bottom': float(h[i + 1]),
                    'type': 'BEARISH_FVG',
                    'direction': 'SHORT',
                    'strength': strength,
                    'timestamp': str(t[i]),
                    'mid': float((l[i - 1] + h[i + 1]) / 2),
                })

    return sorted(fvgs, key=lambda f: f['strength'], reverse=True)[:10]


def detect_bos(df, k=3):
    """Detect Break of Structure (BOS).
    Bullish BOS: price closes above the most recent swing high.
    Bearish BOS: price closes below the most recent swing low."""
    from .indicators import swings
    signals = []
    n = len(df)
    if n < k * 2 + 5:
        return signals
    c = df['c'].values
    t = df['t'].values
    highs, lows = swings(df, k=k)

    # Find last swing high and swing low before recent bars
    if len(highs) >= 2 and len(lows) >= 2:
        last_sh = highs[-1][1]
        prev_sh = highs[-2][1]
        last_sl = lows[-1][1]
        prev_sl = lows[-2][1]

        # Bullish BOS: close above last swing high
        if c[-1] > last_sh and c[-2] <= last_sh:
            strength = min(100, int(60 + ((c[-1] - last_sh) / last_sh) * 500))
            signals.append({
                'type': 'BULLISH_BOS',
                'direction': 'LONG',
                'level': float(last_sh),
                'strength': strength,
                'timestamp': str(t[-1]),
            })

        # Bearish BOS: close below last swing low
        if c[-1] < last_sl and c[-2] >= last_sl:
            strength = min(100, int(60 + ((last_sl - c[-1]) / last_sl) * 500))
            signals.append({
                'type': 'BEARISH_BOS',
                'direction': 'SHORT',
                'level': float(last_sl),
                'strength': strength,
                'timestamp': str(t[-1]),
            })

    return signals[:5]


def detect_choch(df, k=3):
    """Detect Change of Character (CHoCH) — trend reversal signal.
    Bullish CHoCH: price breaks above the last lower high in a downtrend.
    Bearish CHoCH: price breaks below the last higher low in an uptrend."""
    from .indicators import swings
    signals = []
    n = len(df)
    if n < k * 2 + 10:
        return signals
    c = df['c'].values
    t = df['t'].values
    highs, lows = swings(df, k=k)

    if len(highs) >= 3 and len(lows) >= 3:
        # Check for higher highs / lower lows pattern break
        h_prices = [h[1] for h in highs[-4:]]
        l_prices = [l[1] for l in lows[-4:]]

        # Downtrend: series of lower highs, then break above last LH
        if len(h_prices) >= 3:
            if h_prices[-2] < h_prices[-3] and c[-1] > h_prices[-1]:
                strength = min(100, int(55 + ((c[-1] - h_prices[-1]) / h_prices[-1]) * 400))
                signals.append({
                    'type': 'BULLISH_CHOCH',
                    'direction': 'LONG',
                    'level': float(h_prices[-1]),
                    'strength': strength,
                    'timestamp': str(t[-1]),
                })

        # Uptrend: series of higher lows, then break below last HL
        if len(l_prices) >= 3:
            if l_prices[-2] > l_prices[-3] and c[-1] < l_prices[-1]:
                strength = min(100, int(55 + ((l_prices[-1] - c[-1]) / l_prices[-1]) * 400))
                signals.append({
                    'type': 'BEARISH_CHOCH',
                    'direction': 'SHORT',
                    'level': float(l_prices[-1]),
                    'strength': strength,
                    'timestamp': str(t[-1]),
                })

    return signals[:5]


def detect_liquidity_sweep(df, lookback=20):
    """Detect liquidity sweeps — price spikes above/below key levels then reverses.
    Bullish sweep: wick below recent lows, then close above.
    Bearish sweep: wick above recent highs, then close below."""
    sweeps = []
    n = len(df)
    if n < lookback + 3:
        return sweeps
    h, l, c, o = df['h'].values, df['l'].values, df['c'].values, df['o'].values
    t = df['t'].values
    atr_val = _atr(df, 14)

    recent_lows = l[-lookback:-2].min()
    recent_highs = h[-lookback:-2].max()

    # Bullish sweep: wick below recent lows, close above
    if l[-1] < recent_lows and c[-1] > recent_lows and atr_val > 0:
        sweep_depth = (recent_lows - l[-1]) / atr_val
        if sweep_depth > 0.2:
            strength = min(100, int(50 + sweep_depth * 15))
            sweeps.append({
                'type': 'BULLISH_SWEEP',
                'direction': 'LONG',
                'swept_level': float(recent_lows),
                'wick_low': float(l[-1]),
                'strength': strength,
                'timestamp': str(t[-1]),
            })

    # Bearish sweep: wick above recent highs, close below
    if h[-1] > recent_highs and c[-1] < recent_highs and atr_val > 0:
        sweep_depth = (h[-1] - recent_highs) / atr_val
        if sweep_depth > 0.2:
            strength = min(100, int(50 + sweep_depth * 15))
            sweeps.append({
                'type': 'BEARISH_SWEEP',
                'direction': 'SHORT',
                'swept_level': float(recent_highs),
                'wick_high': float(h[-1]),
                'strength': strength,
                'timestamp': str(t[-1]),
            })

    return sweeps[:5]


def analyze_smc(df, timeframe='4h'):
    """Full SMC analysis for a single timeframe. Returns a dict with all signals."""
    obs = detect_order_blocks(df)
    fvgs = detect_fvg(df)
    bos = detect_bos(df)
    choch = detect_choch(df)
    sweeps = detect_liquidity_sweep(df)

    # Overall SMC bias
    long_signals = sum(1 for s in (obs + fvgs + bos + choch + sweeps) if s.get('direction') == 'LONG')
    short_signals = sum(1 for s in (obs + fvgs + bos + choch + sweeps) if s.get('direction') == 'SHORT')

    if long_signals > short_signals:
        bias = 'BULLISH'
        confidence = min(100, 40 + long_signals * 12)
    elif short_signals > long_signals:
        bias = 'BEARISH'
        confidence = min(100, 40 + short_signals * 12)
    else:
        bias = 'NEUTRAL'
        confidence = 30

    return {
        'timeframe': timeframe,
        'bias': bias,
        'confidence': confidence,
        'order_blocks': obs[:5],
        'fvg': fvgs[:5],
        'bos': bos[:3],
        'choch': choch[:3],
        'sweeps': sweeps[:3],
        'long_signals': long_signals,
        'short_signals': short_signals,
    }


def analyze_smc_multi(tf_dict):
    """Analyze SMC across multiple timeframes. Returns consolidated result."""
    results = {}
    all_signals = []
    for tf_name, df in tf_dict.items():
        r = analyze_smc(df, tf_name)
        results[tf_name] = r
        all_signals.extend(r.get('order_blocks', []))
        all_signals.extend(r.get('fvg', []))
        all_signals.extend(r.get('bos', []))
        all_signals.extend(r.get('choch', []))
        all_signals.extend(r.get('sweeps', []))

    # Consolidated bias from all timeframes
    long_count = sum(1 for s in all_signals if s.get('direction') == 'LONG')
    short_count = sum(1 for s in all_signals if s.get('direction') == 'SHORT')

    if long_count > short_count:
        overall_bias = 'BULLISH'
        overall_confidence = min(100, 35 + long_count * 8)
    elif short_count > long_count:
        overall_bias = 'BEARISH'
        overall_confidence = min(100, 35 + short_count * 8)
    else:
        overall_bias = 'NEUTRAL'
        overall_confidence = 25

    return {
        'overall_bias': overall_bias,
        'overall_confidence': overall_confidence,
        'per_timeframe': results,
        'total_long': long_count,
        'total_short': short_count,
        'top_signals': sorted(all_signals, key=lambda s: s.get('strength', 0), reverse=True)[:10],
    }


def _atr(df, n=14):
    """Average True Range."""
    h, l, c = df['h'], df['l'], df['c']
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    val = tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    return float(val) if val == val else 0.0


def _count_reactions(df, level, atr_val, before_idx):
    """Count how many times price reacted near a level (support/resistance)."""
    count = 0
    h, l = df['h'].values, df['l'].values
    start = max(0, before_idx - 30)
    for i in range(start, before_idx):
        if abs(h[i] - level) < 0.5 * atr_val or abs(l[i] - level) < 0.5 * atr_val:
            count += 1
    return count
