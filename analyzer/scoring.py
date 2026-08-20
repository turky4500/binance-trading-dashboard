# -*- coding: utf-8 -*-
"""
Deterministic 100-point opportunity scoring. Weights are configurable in
config/settings.json (default sum = 100).
"""
from .indicators import clamp

COMPONENT_LABELS = {
    'trend_alignment': 'Trend Alignment',
    'structure': 'Market Structure',
    'support_resistance': 'Support / Resistance',
    'volume': 'Volume',
    'momentum': 'Momentum',
    'entry_quality': 'Entry Quality',
    'risk_reward': 'Risk / Reward',
    'liquidity': 'Liquidity',
}


def _trend(tf, direction, w):
    pts, tfs = 0, ['1h', '4h', '1d']
    if direction == 'LONG':
        for k in tfs:
            pts += 1 if tf[k]['above20'] else 0
            pts += 1 if tf[k]['e20_gt_e50'] else 0
        pts += 2 if tf['1d']['above200'] else 0
        pts += 1 if tf['1d']['macd_h'] > 0 else 0
        pts += 1 if tf['4h']['above200'] else 0
    else:
        for k in tfs:
            pts += 1 if not tf[k]['above20'] else 0
            pts += 1 if not tf[k]['e20_gt_e50'] else 0
        pts += 2 if not tf['1d']['above200'] else 0
        pts += 1 if tf['1d']['macd_h'] < 0 else 0
        pts += 1 if not tf['4h']['above200'] else 0
    return round(w * clamp(pts / 10.0, 0, 1), 1)


def _structure(tf, direction, w):
    t4 = tf['4h']
    hs = [p for p, _ in t4['sw_highs']]
    ls = [p for p, _ in t4['sw_lows']]
    pts = 0
    if direction == 'LONG':
        for seq in (ls[-3:], hs[-3:]):
            seq = [x for x in seq]
            for i in range(1, len(seq)):
                if seq[i] > seq[i - 1]:
                    pts += 1
    else:
        for seq in (hs[-3:], ls[-3:]):
            seq = [x for x in seq]
            for i in range(1, len(seq)):
                if seq[i] < seq[i - 1]:
                    pts += 1
    return round(w * clamp(pts / 6.0, 0, 1), 1)


def _structure_breakout(tf, plan, direction, w):
    """Fresh breakouts have no confirmed fractal swings yet: score the base,
    the impulse and the retest strength instead."""
    t4 = tf['4h']
    atr = t4['atr'] or 1e-9
    pts = 0
    if direction == 'LONG':
        R = plan['invalidation_level'] + 0.9 * atr  # approx breakout level
        pts += 3 if t4['close'] > R else 0
        pts += 2 if t4['lo6'] >= R - 0.6 * atr else 0   # shallow retest = strength
        pts += 1 if t4['above20'] else 0
    else:
        S = plan['invalidation_level'] - 0.9 * atr
        pts += 3 if t4['close'] < S else 0
        pts += 2 if t4['hi6'] <= S + 0.6 * atr else 0
        pts += 1 if not t4['above20'] else 0
    return round(w * clamp(pts / 6.0, 0, 1), 1)


def _sr(tf, plan, direction, w):
    t4 = tf['4h']
    atr = t4['atr'] or 1e-9
    lo, hi = plan['entry_zone']
    pts = 0
    checks = [t4['ema20'], t4['ema50'], t4['vwap'], t4['last_low'], t4['last_high']]
    for lv in checks:
        if lv and lo - 0.5 * atr <= lv <= hi + 0.5 * atr:
            pts += 1
    for cname in plan['confluences']:
        pts += 0.5
    return round(w * clamp(pts / 4.0, 0, 1), 1)


def _volume(tf, ticker, w):
    vr = tf['4h']['vol_ratio3']
    if vr >= 2.5:
        b = 10
    elif vr >= 1.8:
        b = 8
    elif vr >= 1.3:
        b = 6
    elif vr >= 1.0:
        b = 4
    else:
        b = 2
    qv = ticker.get('quoteVol', 0) or 0
    if qv >= 50e6:
        l = 5
    elif qv >= 25e6:
        l = 4
    elif qv >= 10e6:
        l = 3
    elif qv >= 5e6:
        l = 2
    else:
        l = 1
    return round(w * clamp((b + l) / 15.0, 0, 1), 1)


def _momentum(tf, direction, w):
    t4, t1 = tf['4h'], tf['1h']
    pts = 0
    if direction == 'LONG':
        pts += 3 if (t4['macd_h'] > 0 and t4['macd_h'] > t4['macd_h_prev']) else (1 if t4['macd_h'] > 0 else 0)
        pts += 2 if t1['macd_h'] > 0 else 0
        pts += 2 if 50 <= t4['rsi'] <= 78 else 0
        pts += 3 if 40 <= t1['rsi'] <= 65 else 0
    else:
        pts += 3 if (t4['macd_h'] < 0 and t4['macd_h'] < t4['macd_h_prev']) else (1 if t4['macd_h'] < 0 else 0)
        pts += 2 if t1['macd_h'] < 0 else 0
        pts += 2 if 22 <= t4['rsi'] <= 50 else 0
        pts += 3 if 35 <= t1['rsi'] <= 60 else 0
    return round(w * clamp(pts / 10.0, 0, 1), 1)


def _entry_quality(tf, plan, direction, w):
    t4 = tf['4h']
    atr = t4['atr'] or 1e-9
    price = tf['1h']['close']
    lo, hi = plan['entry_zone']
    if lo <= price <= hi:
        dist = 0
    else:
        dist = min(abs(price - lo), abs(price - hi)) / atr
    if dist <= 0.2:
        pts = 10
    elif dist <= 0.5:
        pts = 8
    elif dist <= 1.2:
        pts = 6
    elif dist <= 2.5:
        pts = 4
    else:
        pts = 2
    entry = plan['entry_mid']
    sl_dist = abs(entry - plan['stop_loss']) / atr
    if 1.0 <= sl_dist <= 1.7:
        pts = min(10, pts + 1)
    return round(w * clamp(pts / 10.0, 0, 1), 1)


def _risk_reward(plan, w):
    entry = plan['entry_mid']
    sl = plan['stop_loss']
    R = abs(entry - sl)
    if R <= 0:
        return 0.0
    rr2 = abs(plan['tp2'] - entry) / R
    if rr2 >= 2.5:
        pts = 10
    elif rr2 >= 2.0:
        pts = 8
    elif rr2 >= 1.6:
        pts = 6
    elif rr2 >= 1.2:
        pts = 4
    else:
        pts = 2
    return round(w * pts / 10.0, 1)


def _liquidity(ticker, w):
    pts = 0
    spread = ticker.get('spread', 99) or 99
    pts += 2.5 if spread <= 0.03 else (2 if spread <= 0.10 else 1)
    trades = ticker.get('trades', 0) or 0
    pts += 2.5 if trades >= 50000 else (2 if trades >= 10000 else 1)
    return round(w * clamp(pts / 5.0, 0, 1), 1)


def score_plan(tf, plan, ticker, weights):
    d = plan['direction']
    if plan['setup_type'] in ('BREAKOUT_RETEST', 'BREAKDOWN_RETEST'):
        struct = _structure_breakout(tf, plan, d, weights['structure'])
    else:
        struct = _structure(tf, d, weights['structure'])
    parts = {
        'trend_alignment': _trend(tf, d, weights['trend_alignment']),
        'structure': struct,
        'support_resistance': _sr(tf, plan, d, weights['support_resistance']),
        'volume': _volume(tf, ticker, weights['volume']),
        'momentum': _momentum(tf, d, weights['momentum']),
        'entry_quality': _entry_quality(tf, plan, d, weights['entry_quality']),
        'risk_reward': _risk_reward(plan, weights['risk_reward']),
        'liquidity': _liquidity(ticker, weights['liquidity']),
    }
    total = round(sum(parts.values()))
    return total, parts


def grade(score):
    if score >= 90:
        return 'EXCELLENT'
    if score >= 80:
        return 'STRONG'
    if score >= 70:
        return 'GOOD'
    return 'WEAK'
