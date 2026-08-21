# -*- coding: utf-8 -*-
"""
Deterministic trade-setup detection on multi-timeframe state.

Rules are pure functions of market data (no randomness, no LLM). Each setup
produces a full trade plan: entry zone, stop loss, 3 targets, invalidation.
"""
from .indicators import swings, round_to, snap_to_level, clamp


def tf_state(df, k=3):
    """Summarize one timeframe into a plain-dict state usable by all rules."""
    c = float(df['c'].iloc[-1])
    hs, ls = swings(df, k=k)
    n = len(df)
    hi20 = float(df['h'].iloc[-20:].max()) if n >= 20 else float(df['h'].max())
    lo20 = float(df['l'].iloc[-20:].min()) if n >= 20 else float(df['l'].min())
    hi50 = float(df['h'].iloc[-50:].max()) if n >= 50 else hi20
    lo50 = float(df['l'].iloc[-50:].min()) if n >= 50 else lo20
    hi6 = float(df['h'].iloc[-6:].max())
    lo6 = float(df['l'].iloc[-6:].min())
    # Breakout reference: extremes of the window BEFORE the last 4 candles
    if n >= 64:
        res = float(df['h'].iloc[-60:-4].max())
        sup = float(df['l'].iloc[-60:-4].min())
    else:
        res, sup = hi50, lo50
    return {
        'close': c,
        'ema20': float(df['ema20'].iloc[-1]),
        'ema50': float(df['ema50'].iloc[-1]),
        'ema200': float(df['ema200'].iloc[-1]),
        'rsi': float(df['rsi'].iloc[-1]),
        'macd_h': float(df['macd_h'].iloc[-1]),
        'macd_h_prev': float(df['macd_h'].iloc[-2]),
        'atr': float(df['atr'].iloc[-1]),
        'vwap': float(df['vwap'].iloc[-1]),
        'vol_ratio': float(df['vol_ratio'].iloc[-1]),
        'vol_ratio3': float(df['vol_ratio'].iloc[-3:].mean()),
        'above20': c > float(df['ema20'].iloc[-1]),
        'above50': c > float(df['ema50'].iloc[-1]),
        'above200': c > float(df['ema200'].iloc[-1]),
        'e20_gt_e50': float(df['ema20'].iloc[-1]) > float(df['ema50'].iloc[-1]),
        'sw_highs': [(float(p), str(t)) for _, p, t in hs],
        'sw_lows': [(float(p), str(t)) for _, p, t in ls],
        'last_high': float(hs[-1][1]) if hs else c,
        'last_low': float(ls[-1][1]) if ls else c,
        'hi20': hi20, 'lo20': lo20, 'hi50': hi50, 'lo50': lo50,
        'hi6': hi6, 'lo6': lo6,
        'st_dir': int(df['st_dir'].iloc[-1]),
        'st_line': float(df['st_line'].iloc[-1]),
        'resistance': res, 'support': sup,
    }


def _breakout_cross(state, side='up'):
    """True if a fresh 4H breakout happened recently (detected from state)."""
    return None  # breakout needs candle history -> computed by caller


def detect_breakout(df, s=None, vol_min=1.5, lookback=8):
    """Look for a candle in the last `lookback` bars closing beyond the pre-window
    extreme. The base window ends before those bars so the breakout candle itself
    never contaminates the base extreme."""
    n = len(df)
    if n < 78:
        return None
    base_hi = float(df['h'].iloc[-60:-lookback].max())
    base_lo = float(df['l'].iloc[-60:-lookback].min())
    recent = df.iloc[-lookback:]
    for i in range(len(recent)):
        row = recent.iloc[i]
        if float(row['c']) > base_hi and float(row['vol_ratio']) >= vol_min:
            return {'level': base_hi, 'dir': 'UP', 'at': str(row['t'])}
        if float(row['c']) < base_lo and float(row['vol_ratio']) >= vol_min:
            return {'level': base_lo, 'dir': 'DOWN', 'at': str(row['t'])}
    return None


def _target(entry, mult, R, struct_levels, direction, tp_snap_tol=0.08):
    """Target = multiple of R, snapped to the nearest structural level within
    tolerance (max 8% below / 5% above the raw multiple), never past a floor."""
    raw = entry + mult * R if direction == 'LONG' else entry - mult * R
    best = raw
    for lv in struct_levels:
        if not lv or lv <= 0:
            continue
        d = (lv - raw) / raw
        if -tp_snap_tol <= d <= 0.05 and abs(lv - raw) < abs(best - raw):
            best = lv
    if direction == 'LONG':
        return max(best, entry + (mult - 0.2) * R)
    return min(best, entry - (mult - 0.2) * R)


def _targets(entry, R, t4, td, direction):
    if direction == 'LONG':
        struct = [t4['last_high'], t4['hi20'], t4['hi50'], td['last_high']]
    else:
        struct = [t4['last_low'], t4['lo20'], t4['lo50'], td['last_low']]
    return (_target(entry, 1.5, R, struct, direction),
            _target(entry, 2.5, R, struct, direction),
            _target(entry, 4.0, R, struct, direction))


def _sl_clamp(entry, raw_sl, atr, direction, cfg):
    lo, hi = cfg['atr_sl_min'], cfg['atr_sl_max']
    if direction == 'LONG':
        dist = clamp(entry - raw_sl, lo * atr, hi * atr)
        return entry - dist
    dist = clamp(raw_sl - entry, lo * atr, hi * atr)
    return entry + dist


# ------------------------------------------------------------------ setups

def pullback_long(tf, cfg):
    """Buy the dip: strong daily uptrend + 4H trend + cooled 1H RSI near 4H EMA20."""
    td, t4, t1 = tf['1d'], tf['4h'], tf['1h']
    atr = t4['atr']
    if atr <= 0:
        return None
    # daily trend
    if not (td['above20'] and td['e20_gt_e50'] and 46 <= td['rsi'] <= 78 and td['macd_h'] > 0):
        return None
    # 4H trend still alive + not overbought
    if not (t4['close'] > t4['ema50'] and t4['close'] >= t4['ema20'] - 0.25 * atr):
        return None
    if t4['rsi'] > 78:
        return None
    # 1H cooled
    if not (38 <= t1['rsi'] <= 68):
        return None
    anchor = t4['ema20']
    zone = [anchor, anchor + cfg['pullback_zone_atr'] * atr]
    entry_mid = (zone[0] + zone[1]) / 2
    price = t1['close']
    in_zone = zone[0] - 0.05 * atr <= price <= zone[1] + 0.15 * atr
    sw_low = min([l for l, _ in t1['sw_lows'][-3:]] or [t1['lo20']])
    raw_sl = min(sw_low, t4['ema50']) - 0.2 * atr
    sl = _sl_clamp(entry_mid, raw_sl, atr, 'LONG', cfg)
    if sl >= zone[0]:
        sl = entry_mid - 1.2 * atr
    R = entry_mid - sl
    tp1, tp2, tp3 = _targets(entry_mid, R, t4, td, 'LONG')
    if (tp1 - entry_mid) / R < cfg['min_rr_tp1']:
        return None
    confluences = ['4H EMA20']
    if t4['vwap'] and abs(t4['vwap'] - anchor) < 0.6 * atr:
        confluences.append('Session VWAP')
    if abs(t4['ema50'] - anchor) < 0.5 * atr:
        confluences.append('4H EMA50')
    if abs(sw_low - zone[0]) < 0.8 * atr:
        confluences.append('1H swing low')
    return {
        'direction': 'LONG', 'setup_type': 'PULLBACK',
        'setup_label': 'Pullback to EMA20 (4H)',
        'status': 'READY' if in_zone else 'WAITING_CONFIRMATION',
        'entry_mid': round(entry_mid, 8), 'entry_zone': [round(zone[0], 8), round(zone[1], 8)],
        'stop_loss': round(sl, 8), 'tp1': round(tp1, 8), 'tp2': round(tp2, 8), 'tp3': round(tp3, 8),
        'invalidation_level': round(t4['ema50'], 8),
        'primary_timeframe': '4H', 'confluences': confluences,
        'supports': [(round(t1['sw_lows'][-1][0], 8), '1H swing low'),
                     (round(t4['ema50'], 8), '4H EMA50'),
                     (round(t4['ema20'], 8), '4H EMA20')],
        'resistances': [(round(t4['last_high'], 8), '4H swing high'),
                        (round(t4['hi20'], 8), '4H 20-bar high'),
                        (round(td['last_high'], 8), 'Daily swing high')],
        'confirmation': ('price returns into the entry zone with 1H RSI below 62'
                         if not in_zone else 'price holds inside the entry zone'),
    }


def vwap_hold_long(tf, cfg):
    """Momentum continuation: price holds above/at session VWAP in a strong trend."""
    td, t4, t1 = tf['1d'], tf['4h'], tf['1h']
    atr = t4['atr']
    if atr <= 0:
        return None
    if not (td['above20'] and td['e20_gt_e50'] and 48 <= td['rsi'] <= 80 and td['macd_h'] > 0):
        return None
    if not (t4['above20'] and t4['e20_gt_e50']):
        return None
    if t4['rsi'] > 82:
        return None
    if not (42 <= t1['rsi'] <= 70):
        return None
    vwap = t4['vwap']
    if not vwap or vwap != vwap:
        return None
    if vwap < t4['ema20'] + 0.4 * atr:  # VWAP must be clearly above EMA20 (extended trend)
        return None
    zone = [vwap - 0.3 * atr, vwap + 0.35 * atr]
    entry_mid = (zone[0] + zone[1]) / 2
    price = t1['close']
    in_zone = zone[0] - 0.1 * atr <= price <= zone[1] + 0.1 * atr
    sw_low = min([l for l, _ in t1['sw_lows'][-3:]] or [t1['lo20']])
    raw_sl = min(sw_low - 0.25 * atr, vwap - 1.4 * atr)
    sl = _sl_clamp(entry_mid, raw_sl, atr, 'LONG', cfg)
    if sl >= zone[0]:
        sl = entry_mid - 1.3 * atr
    R = entry_mid - sl
    tp1, tp2, tp3 = _targets(entry_mid, R, t4, td, 'LONG')
    if (tp1 - entry_mid) / R < cfg['min_rr_tp1']:
        return None
    confluences = ['Session VWAP', '4H EMA20 (below)']
    if abs(t1['sw_lows'][-1][0] - zone[0]) < 0.9 * atr:
        confluences.append('1H swing low')
    return {
        'direction': 'LONG', 'setup_type': 'VWAP_HOLD',
        'setup_label': 'Momentum continuation at VWAP (4H)',
        'status': 'READY' if in_zone else 'WAITING_CONFIRMATION',
        'entry_mid': round(entry_mid, 8), 'entry_zone': [round(zone[0], 8), round(zone[1], 8)],
        'stop_loss': round(sl, 8), 'tp1': round(tp1, 8), 'tp2': round(tp2, 8), 'tp3': round(tp3, 8),
        'invalidation_level': round(vwap - 1.2 * atr, 8),
        'primary_timeframe': '4H', 'confluences': confluences,
        'supports': [(round(vwap, 8), 'Session VWAP'),
                     (round(t4['ema20'], 8), '4H EMA20'),
                     (round(t1['sw_lows'][-1][0], 8), '1H swing low')],
        'resistances': [(round(t4['last_high'], 8), '4H swing high'),
                        (round(t4['hi20'], 8), '4H 20-bar high'),
                        (round(td['last_high'], 8), 'Daily swing high')],
        'confirmation': ('price holds above the session VWAP zone'
                         if not in_zone else 'price holds inside the VWAP zone'),
    }



def breakout_long(tf, brk, cfg):
    """Breakout above resistance: READY when price retests the level, else WAITING."""
    td, t4, t1 = tf['1d'], tf['4h'], tf['1h']
    atr = t4['atr']
    if atr <= 0 or brk is None or brk['dir'] != 'UP':
        return None
    R = brk['level']
    if not (td['above20'] or (td['above50'] and td['rsi'] > 50)):
        return None
    if td['macd_h'] < 0 and t4['macd_h'] < 0:
        return None
    if t1['rsi'] > 78:
        return None
    if t4['rsi'] > 84:
        return None  # extremely overbought breakout — do not chase
    price = t1['close']
    if price < R - 0.9 * atr:  # breakout already failed
        return None
    zone = [R - 0.4 * atr, R + 0.35 * atr]
    entry_mid = (zone[0] + zone[1]) / 2
    in_zone = zone[0] <= price <= zone[1]
    near = price <= R + 3.0 * atr
    if not near:
        return None
    # stop under the retest low (last 6 bars) with margin, or 1 ATR under the level
    raw_sl = min(R - 1.0 * atr, t4['lo6'] - 0.25 * atr)
    sl = _sl_clamp(entry_mid, raw_sl, atr, 'LONG', cfg)
    Rr = entry_mid - sl
    tp1, tp2, tp3 = _targets(entry_mid, Rr, t4, td, 'LONG')
    if (tp1 - entry_mid) / Rr < cfg['min_rr_tp1']:
        return None
    status = 'READY' if (in_zone and t4['rsi'] <= 80) else 'WAITING_CONFIRMATION'
    confluences = ['Breakout level ' + str(round(R, 8))]
    if abs(t4['ema20'] - zone[0]) < 0.8 * atr:
        confluences.append('4H EMA20')
    if t4['vwap'] and abs(t4['vwap'] - zone[0]) < 0.8 * atr:
        confluences.append('Session VWAP')
    if t4['rsi'] > 80:
        confluences.append('RSI cooling needed')
    return {
        'direction': 'LONG', 'setup_type': 'BREAKOUT_RETEST',
        'setup_label': 'Breakout + Retest (4H)',
        'status': status,
        'entry_mid': round(entry_mid, 8), 'entry_zone': [round(zone[0], 8), round(zone[1], 8)],
        'stop_loss': round(sl, 8), 'tp1': round(tp1, 8), 'tp2': round(tp2, 8), 'tp3': round(tp3, 8),
        'invalidation_level': round(R - 0.9 * atr, 8),
        'primary_timeframe': '4H', 'confluences': confluences,
        'supports': [(round(R, 8), 'Breakout level (now support)'),
                     (round(t4['lo6'], 8), '4H retest low'),
                     (round(t4['ema20'], 8), '4H EMA20')],
        'resistances': [(round(t4['last_high'], 8), '4H swing high'),
                        (round(t4['hi20'], 8), '4H 20-bar high'),
                        (round(td['last_high'], 8), 'Daily swing high')],
        'confirmation': ('price retests the breakout zone without closing back below it'
                         if not in_zone else 'price holds inside the retest zone'),
    }


def breakdown_short(tf, brk, cfg):
    """Mirror of breakout_long for shorts: breakdown below support + retest."""
    td, t4, t1 = tf['1d'], tf['4h'], tf['1h']
    atr = t4['atr']
    if atr <= 0 or brk is None or brk['dir'] != 'DOWN':
        return None
    S = brk['level']
    if not (td['close'] < td['ema20'] or (td['close'] < td['ema50'] and td['rsi'] < 50)):
        return None
    if td['macd_h'] > 0 and t4['macd_h'] > 0:
        return None
    if t1['rsi'] < 25:
        return None
    if t4['rsi'] < 16:
        return None  # extremely oversold breakdown — do not chase
    price = t1['close']
    if price > S + 0.9 * atr:
        return None
    zone = [S - 0.35 * atr, S + 0.4 * atr]
    entry_mid = (zone[0] + zone[1]) / 2
    in_zone = zone[0] <= price <= zone[1]
    if not (price >= S - 3.0 * atr):
        return None
    raw_sl = max(S + 1.0 * atr, t4['hi6'] + 0.25 * atr)
    sl = _sl_clamp(entry_mid, raw_sl, atr, 'SHORT', cfg)
    Rr = sl - entry_mid
    tp1, tp2, tp3 = _targets(entry_mid, Rr, t4, td, 'SHORT')
    if (entry_mid - tp1) / Rr < cfg['min_rr_tp1']:
        return None
    status = 'READY' if (in_zone and t4['rsi'] >= 20) else 'WAITING_CONFIRMATION'
    return {
        'direction': 'SHORT', 'setup_type': 'BREAKDOWN_RETEST',
        'setup_label': 'Breakdown + Retest (4H)',
        'status': status,
        'entry_mid': round(entry_mid, 8), 'entry_zone': [round(zone[0], 8), round(zone[1], 8)],
        'stop_loss': round(sl, 8), 'tp1': round(tp1, 8), 'tp2': round(tp2, 8), 'tp3': round(tp3, 8),
        'invalidation_level': round(S + 0.9 * atr, 8),
        'primary_timeframe': '4H', 'confluences': ['Breakdown level ' + str(round(S, 8))],
        'supports': [(round(t4['lo20'], 8), '4H 20-bar low'),
                     (round(td['last_low'], 8), 'Daily swing low')],
        'resistances': [(round(S, 8), 'Breakdown level (now resistance)'),
                        (round(t4['hi6'], 8), '4H retest high'),
                        (round(t4['ema20'], 8), '4H EMA20')],
        'confirmation': ('price retests the breakdown zone without closing back above it'
                         if not in_zone else 'price holds inside the retest zone'),
    }


def trend_short(tf, cfg):
    """Sell the rally in a downtrend: rejection at 4H EMA20."""
    td, t4, t1 = tf['1d'], tf['4h'], tf['1h']
    atr = t4['atr']
    if atr <= 0:
        return None
    if not (td['close'] < td['ema20'] and td['close'] < td['ema50'] and td['rsi'] < 58):
        return None
    if not (t4['close'] < t4['ema20'] and t4['close'] < t4['ema50']):
        return None
    if not (44 <= t1['rsi'] <= 68):
        return None
    anchor = t4['ema20']
    zone = [anchor - 0.3 * atr, anchor + 0.35 * atr]
    entry_mid = (zone[0] + zone[1]) / 2
    price = t1['close']
    in_zone = zone[0] - 0.15 * atr <= price <= zone[1] + 0.05 * atr
    sw_hi = max([h for h, _ in t1['sw_highs'][-3:]] or [t1['hi20']])
    raw_sl = max(sw_hi, t4['ema50']) + 0.2 * atr
    sl = _sl_clamp(entry_mid, raw_sl, atr, 'SHORT', cfg)
    if sl <= zone[1]:
        sl = entry_mid + 1.2 * atr
    Rr = sl - entry_mid
    tp1, tp2, tp3 = _targets(entry_mid, Rr, t4, td, 'SHORT')
    if (entry_mid - tp1) / Rr < cfg['min_rr_tp1']:
        return None
    return {
        'direction': 'SHORT', 'setup_type': 'TREND_SHORT',
        'setup_label': 'Trend-follow short at EMA20 (4H)',
        'status': 'READY' if in_zone else 'WAITING_CONFIRMATION',
        'entry_mid': round(entry_mid, 8), 'entry_zone': [round(zone[0], 8), round(zone[1], 8)],
        'stop_loss': round(sl, 8), 'tp1': round(tp1, 8), 'tp2': round(tp2, 8), 'tp3': round(tp3, 8),
        'invalidation_level': round(t4['ema20'] + 1.0 * atr, 8),
        'primary_timeframe': '4H', 'confluences': ['4H EMA20 rejection'],
        'supports': [(round(t4['lo20'], 8), '4H 20-bar low'),
                     (round(td['last_low'], 8), 'Daily swing low')],
        'resistances': [(round(anchor, 8), '4H EMA20'),
                        (round(sw_hi, 8), '1H swing high'),
                        (round(t4['hi20'], 8), '4H 20-bar high')],
        'confirmation': ('price rejects the 4H EMA20 zone with a bearish 1H candle'
                         if not in_zone else 'price holds below the 4H EMA20 zone'),
    }


def generate_plans(tf, brk, cfg):
    """Run all setup rules; return list of candidate plans (max one per direction)."""
    plans = []
    for fn in (pullback_long, vwap_hold_long, breakout_long, breakdown_short, trend_short):
        try:
            p = fn(tf, brk, cfg) if fn in (breakout_long, breakdown_short) else fn(tf, cfg)
            if p:
                plans.append(p)
        except Exception:
            continue
    # one plan per direction: prefer READY; otherwise the zone closest to price
    best = {}
    for p in plans:
        cur = best.get(p['direction'])
        if cur is None:
            best[p['direction']] = p
            continue
        if p['status'] == 'READY' and cur['status'] != 'READY':
            best[p['direction']] = p
            continue
        if p['status'] == cur['status']:
            zone_lo, zone_hi = p['entry_zone']
            mid = (zone_lo + zone_hi) / 2
            cur_lo, cur_hi = cur['entry_zone']
            cur_mid = (cur_lo + cur_hi) / 2
            price = tf['1h']['close']
            if abs(mid - price) < abs(cur_mid - price):
                best[p['direction']] = p
    return list(best.values())
