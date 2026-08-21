# -*- coding: utf-8 -*-
"""Deterministic unit tests: indicators, signal rules, scoring bounds, tracker lifecycle."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.indicators import enrich, klines_to_df, rsi, ema
from analyzer.signal import tf_state, generate_plans, detect_breakout
from analyzer.scoring import score_plan
from analyzer.tracker import track, performance_stats
from analyzer.storage import save_json, load_json, data_path


def make_df(n=400, start=100.0, drift=0.1, noise=0.5, seed=7):
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(rng.normal(drift, noise, n))
    closes = np.maximum(closes, 1.0)
    opens = closes + rng.normal(0, noise / 2, n)
    highs = np.maximum(opens, closes) + rng.uniform(0, noise, n)
    lows = np.minimum(opens, closes) - rng.uniform(0, noise, n)
    vols = rng.uniform(50, 150, n)
    t = pd.date_range('2026-01-01', periods=n, freq='4h', tz='UTC')
    return pd.DataFrame({'t': t, 'o': opens, 'h': highs, 'l': lows, 'c': closes, 'v': vols})


def state_for(df):
    return tf_state(enrich(df), k=3)


# ---------------- indicators ----------------
def test_ema_known_value():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10.0])
    e = ema(s, 5)
    assert abs(e.iloc[-1] - 8.052) < 0.01


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    r = rsi(s)
    assert r.between(0, 100).all()
    # trending up strongly -> RSI high
    up = pd.Series(np.linspace(100, 200, 100))
    assert rsi(up).iloc[-1] > 70


def test_enrich_columns():
    df = enrich(make_df())
    for c in ['ema20', 'ema50', 'ema200', 'rsi', 'macd', 'macd_h', 'atr', 'vwap', 'vol_ratio']:
        assert c in df.columns


# ---------------- signal rules ----------------
def test_pullback_plan_invariants():
    cfg = {'atr_sl_min': 0.8, 'atr_sl_max': 2.2, 'pullback_zone_atr': 0.6, 'min_rr_tp1': 1.0}
    # strong uptrend with a mild pullback at the end
    df = make_df(drift=0.8, noise=0.4)
    df.iloc[-8:, df.columns.get_loc('c')] *= 0.94  # pullback
    dfe = enrich(df)
    tf = {'1d': state_for(dfe.iloc[::6].reset_index(drop=True)), '4h': state_for(dfe),
          '1h': state_for(dfe.iloc[::4].reset_index(drop=True)), '15m': state_for(dfe.iloc[::1].reset_index(drop=True))}
    # force daily bullish state
    tf['1d']['above20'] = True; tf['1d']['e20_gt_e50'] = True; tf['1d']['rsi'] = 60.0; tf['1d']['macd_h'] = 1.0
    tf['4h']['close'] = tf['4h']['ema20'] * 1.001
    tf['4h']['above20'] = True; tf['4h']['rsi'] = 65.0
    tf['1h']['rsi'] = 55.0
    plans = generate_plans(tf, None, cfg)
    longs = [p for p in plans if p['direction'] == 'LONG']
    if longs:
        p = longs[0]
        assert p['entry_zone'][0] < p['entry_zone'][1]
        assert p['stop_loss'] < p['entry_zone'][0]
        assert p['tp1'] < p['tp2'] < p['tp3']
        assert p['tp1'] > p['entry_mid']
        R = p['entry_mid'] - p['stop_loss']
        assert (p['tp1'] - p['entry_mid']) / R >= cfg['min_rr_tp1'] - 1e-9
        assert 0.7 * dfe['atr'].iloc[-1] <= R <= 2.3 * dfe['atr'].iloc[-1]


def test_breakout_detection():
    df = make_df(drift=0.05, noise=0.3)
    base_hi = df['h'].iloc[-60:-8].max()
    # breakout closes in the last 8 bars, outside the base window
    df.iloc[-6:-3, df.columns.get_loc('c')] = base_hi * 1.02
    df.iloc[-6:-3, df.columns.get_loc('h')] = base_hi * 1.05
    df.iloc[-6:-3, df.columns.get_loc('v')] = 300  # volume burst
    e = enrich(df)
    brk = detect_breakout(e, None)
    assert brk is not None and brk['dir'] == 'UP'


# ---------------- scoring ----------------
def test_score_components_sum_to_100():
    weights = {'trend_alignment': 20, 'structure': 15, 'support_resistance': 15, 'volume': 15,
               'momentum': 10, 'entry_quality': 10, 'risk_reward': 10, 'liquidity': 5}
    assert sum(weights.values()) == 100
    df = enrich(make_df(drift=0.5))
    tf = {'15m': state_for(df), '1h': state_for(df), '4h': state_for(df), '1d': state_for(df)}
    tf['1d']['above20'] = tf['1d']['above50'] = tf['1d']['above200'] = tf['1d']['e20_gt_e50'] = True
    tf['1d']['macd_h'] = 1.0; tf['1d']['rsi'] = 60.0
    tf['4h']['above20'] = tf['4h']['above50'] = tf['4h']['above200'] = True
    plan = {'direction': 'LONG', 'setup_type': 'PULLBACK', 'entry_zone': [100.0, 101.0], 'entry_mid': 100.5,
            'stop_loss': 98.5, 'tp1': 103.5, 'tp2': 105.5, 'tp3': 108.5, 'invalidation_level': 97.0,
            'confluences': ['4H EMA20']}
    ticker = {'quoteVol': 30e6, 'spread': 0.02, 'trades': 100000, 'chg24': 1.0}
    total, parts = score_plan(tf, plan, ticker, weights)
    assert 0 <= total <= 100
    assert abs(sum(parts.values()) - total) <= 0.6
    for k, v in parts.items():
        assert 0 <= v <= weights[k]


# ---------------- tracker lifecycle ----------------
def test_tracker_transitions():
    import time as _t
    now_ms = int(_t.time() * 1000)
    base = now_ms - 3 * 3600 * 1000
    candles = []
    for i in range(12):
        t = base + i * 900 * 1000
        c = 100 + i  # rising
        candles.append([t, c, c + 1, c - 1, c, 1000])
    opp = {
        'id': 'X', 'symbol': 'TSTUSDT', 'pair': 'TST/USDT', 'direction': 'LONG',
        'status': 'READY', 'entry_zone': [101.0, 102.0], 'entry_mid': 101.5,
        'stop_loss': 98.0, 'tp1': 105.0, 'tp2': 108.0, 'tp3': 115.0,
        'invalidation_level': 97.0, 'created_at': '2026-01-01T00:00:00Z', 'score': 80,
    }
    closed = track([opp], {'TSTUSDT': candles}, 48, '2026-01-01T02:00:00Z')
    # candles rise 101..112 -> triggered, TP1 and TP2 hit (SL 98 never hit)
    assert opp['status'] in ('TP1_HIT', 'TP2_HIT')
    assert opp.get('events')

    # stopped case: closes stay above invalidation but a wick sweeps below SL
    opp2 = dict(opp, id='Y', status='READY', entry_zone=[101.0, 102.0])
    opp2['events'] = []
    bad = []
    for i in range(12):
        c = 100 - 0.2 * i
        low = c - 0.5
        if i == 6:
            low = 95.0  # wick below SL 98, close stays fine
        bad.append([base + i * 900 * 1000, c, c + 1, low, c, 1000])
    track([opp2], {'TSTUSDT': bad}, 48, '2026-01-01T02:00:00Z')
    assert opp2['status'] == 'STOPPED'

    # expired case: old opportunity, no candles after creation
    opp3 = dict(opp, id='Z', status='READY', created_at='2020-01-01T00:00:00Z')
    track([opp3], {'TSTUSDT': []}, 48, '2026-01-01T02:00:00Z')
    assert opp3['status'] == 'EXPIRED'


def test_performance_stats():
    hist = [
        {'result': 'WIN', 'score': 80, 'rr_tp2': 2.5, 'final_status': 'TP2_HIT', 'hold_hours': 5},
        {'result': 'LOSS', 'score': 75, 'rr_tp2': 2.0, 'final_status': 'STOPPED', 'hold_hours': 3},
        {'result': 'WIN', 'score': 85, 'rr_tp2': 3.0, 'final_status': 'TP1_HIT', 'hold_hours': 2},
        {'result': 'EXPIRED', 'score': 70, 'rr_tp2': 2.2, 'final_status': 'EXPIRED', 'hold_hours': 48},
    ]
    s = performance_stats(hist)
    assert s['total'] == 4
    assert s['win_rate'] == pytest.approx(66.7, abs=0.1)
    assert s['tp1_hit_rate'] == pytest.approx(66.7, abs=0.1)
    assert s['avg_score'] == pytest.approx(77.5, abs=0.1)


# ---------------- storage ----------------
def test_storage_roundtrip(tmp_path):
    p = os.path.join(str(tmp_path), 'x.json')
    save_json(p, {'a': [1, 2], 'b': 'ok'})
    assert load_json(p) == {'a': [1, 2], 'b': 'ok'}
    assert load_json(os.path.join(str(tmp_path), 'missing.json'), []) == []


def test_supertrend_direction():
    from analyzer.indicators import supertrend
    # strong uptrend -> SuperTrend direction UP (+1)
    df_up = enrich(make_df(n=300, drift=1.2, noise=0.4))
    assert 'st_line' in df_up.columns and 'st_dir' in df_up.columns
    assert df_up['st_dir'].iloc[-1] == 1, 'uptrend should end with SuperTrend UP'
    # strong downtrend -> SuperTrend direction DOWN (-1)
    df_dn = enrich(make_df(n=300, drift=-1.2, noise=0.4))
    assert df_dn['st_dir'].iloc[-1] == -1, 'downtrend should end with SuperTrend DOWN'
    # line is finite
    assert not df_up['st_line'].isna().any()


def test_supertrend_flip():
    from analyzer.indicators import supertrend
    # uptrend then crash -> direction flips to -1
    df = make_df(n=200, drift=1.0, noise=0.3)
    df.iloc[-20:, df.columns.get_loc('c')] *= 0.75
    e = enrich(df)
    assert e['st_dir'].iloc[-1] == -1, 'crash should flip SuperTrend to DOWN'


# ---------------- backtest simulation ----------------
def _mk_bars(rows):
    import pandas as pd
    return pd.DataFrame(rows, columns=['t', 'o', 'h', 'l', 'c', 'v'])


def test_simulate_tp2_hit():
    from analyzer.backtest import simulate_lifecycle
    bars = _mk_bars([(i * 14400000, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i, 1000) for i in range(10)])
    plan = {'entry_zone': [101.0, 102.0], 'stop_loss': 98.0, 'tp1': 105.0, 'tp2': 108.0,
            'tp3': 115.0, 'invalidation_level': 97.0}
    status, end = simulate_lifecycle(bars, plan)
    assert status == 'TP2_HIT', f'got {status}'


def test_simulate_stopped_first():
    from analyzer.backtest import simulate_lifecycle
    # wick goes below SL before touching targets -> STOPPED (conservative)
    bars = _mk_bars([(i * 14400000, 100, 101, 99, 100, 1000) for i in range(4)] +
                    [(4 * 14400000, 100, 101, 97.5, 100, 1000)])
    plan = {'entry_zone': [100.5, 101.0], 'stop_loss': 98.0, 'tp1': 105.0, 'tp2': 108.0,
            'tp3': 115.0, 'invalidation_level': 97.0}
    status, end = simulate_lifecycle(bars, plan)
    assert status == 'STOPPED', f'got {status}'


def test_simulate_invalidated_and_expired():
    from analyzer.backtest import simulate_lifecycle
    plan = {'entry_zone': [101.0, 102.0], 'stop_loss': 98.0, 'tp1': 105.0, 'tp2': 108.0,
            'tp3': 115.0, 'invalidation_level': 97.0}
    # close below invalidation before any touch
    bars = _mk_bars([(i * 14400000, 99, 99.5, 96.8, 96.9, 1000) for i in range(5)])
    status, _ = simulate_lifecycle(bars, plan)
    assert status == 'INVALIDATED', f'got {status}'
    # never touches the zone within expiry window
    bars2 = _mk_bars([(i * 14400000, 103, 104, 103, 103.5, 1000) for i in range(14)])
    status2, end2 = simulate_lifecycle(bars2, plan, expiry_bars=6)
    assert status2 == 'EXPIRED', f'got {status2}'


def test_calibration_bands():
    from analyzer.backtest import calibration
    recs = [
        {'score': 72, 'result': 'WIN', 'final_status': 'TP2_HIT'},
        {'score': 74, 'result': 'LOSS', 'final_status': 'STOPPED'},
        {'score': 86, 'result': 'WIN', 'final_status': 'TP1_HIT'},
        {'score': 90, 'result': 'WIN', 'final_status': 'TP3_HIT'},
        {'score': 83, 'result': 'EXPIRED', 'final_status': 'EXPIRED'},
    ]
    rows = calibration(recs)
    assert len(rows) == 5
    band_70 = next(r for r in rows if r['band'] == '70-74')
    assert band_70['decided'] == 2 and band_70['win_rate'] == 50.0
    band_90 = next(r for r in rows if r['band'] == '90-100')
    assert band_90['win_rate'] == 100.0
