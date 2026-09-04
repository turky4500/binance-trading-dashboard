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


def _breakout_df(close_mult, high_mult, vol):
    df = make_df(drift=0.05, noise=0.3)
    base_hi = float(df['h'].iloc[-60:-8].max())
    ci, hi, vi = (df.columns.get_loc(x) for x in ('c', 'h', 'v'))
    df.iloc[-5, ci] = base_hi * close_mult
    df.iloc[-5, hi] = base_hi * high_mult
    df.iloc[-5, vi] = vol
    return enrich(df)


def test_breakout_close_position_filter_rejects_wick():
    # closes just above the base high but leaves a long upper wick (weak close)
    e = _breakout_df(close_mult=1.002, high_mult=1.06, vol=400)
    assert detect_breakout(e, None, close_pos_min=0.0) is not None  # filter off
    assert detect_breakout(e, None, close_pos_min=0.6) is None      # wick rejected


def test_breakout_strong_close_passes_position_filter():
    e = _breakout_df(close_mult=1.035, high_mult=1.04, vol=400)  # closes near the top
    brk = detect_breakout(e, None, close_pos_min=0.6)
    assert brk is not None and brk['dir'] == 'UP'


def test_breakout_volume_threshold_from_cfg():
    # modest volume burst: vol_ratio ~1.6 -> passes 1.5, rejected at 2.0
    e = _breakout_df(close_mult=1.03, high_mult=1.035, vol=165)
    assert detect_breakout(e, None, vol_min=1.5, close_pos_min=0.6) is not None
    assert detect_breakout(e, None, vol_min=2.0, close_pos_min=0.6) is None


def test_btc_regime_bullish_helper():
    from analyzer.scanner import btc_regime_bullish
    up = enrich(make_df(n=400, start=100.0, drift=0.5, noise=0.4))
    down = enrich(make_df(n=400, start=400.0, drift=-0.5, noise=0.4))
    assert btc_regime_bullish(up) is True
    assert btc_regime_bullish(down) is False


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


# ---------------- universe selection ----------------
def test_universe_zero_cap_keeps_every_qualified_pair():
    from analyzer.scanner import _select_universe
    rows = [{'sym': f'C{i}USDT', 'quoteVol': 100 - i} for i in range(7)]
    assert _select_universe(rows, 0) == rows
    assert _select_universe(rows, None) == rows
    assert _select_universe(rows, 3) == rows[:3]


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


# ---------------- lifecycle alerts ----------------
def test_notify_key_mapping():
    from analyzer.run import _notify_key
    assert _notify_key('TP1_HIT') == 'tp_hit'
    assert _notify_key('TP2_HIT') == 'tp_hit'
    assert _notify_key('TRIGGERED') == 'triggered'
    assert _notify_key('READY') == 'ready'
    assert _notify_key('STOPPED') == 'stopped'
    assert _notify_key('EXPIRED') == 'expired'
    assert _notify_key('INVALIDATED') == 'invalidated'
    assert _notify_key('UNKNOWN') is None


def test_lifecycle_text_builders():
    from analyzer.run import lifecycle_text
    opp = {'pair': 'BTC/USDT', 'direction': 'LONG', 'entry_zone': [100.0, 101.0],
           'entry_mid': 100.5, 'stop_loss': 98.0, 'tp1': 104.0, 'tp2': 108.0, 'tp3': 114.0,
           'score': 85, 'invalidation_level': 97.0}
    t1 = lifecycle_text(opp, 'READY', 'TRIGGERED')
    assert 'TRIGGERED' in t1 and 'BTC/USDT' in t1 and '100.0 - 101.0' in t1
    t2 = lifecycle_text(opp, 'TRIGGERED', 'TP1_HIT')
    assert 'TP1 HIT' in t2 and '+1.4R' in t2  # (104-100.5)/2.5 = 1.4
    t3 = lifecycle_text(opp, 'TRIGGERED', 'STOPPED')
    assert 'STOPPED' in t3
    t4 = lifecycle_text(opp, 'READY', 'INVALIDATED')
    assert 'INVALIDATED' in t4


def test_extract_transitions():
    from analyzer.scanner import _extract_transitions
    opps = [
        {'id': 'a', 'status': 'TRIGGERED'},
        {'id': 'b', 'status': 'READY'},
        {'id': 'c', 'status': 'READY'},
    ]
    tr = _extract_transitions({'a': 'READY', 'b': 'READY', 'c': 'WAITING_CONFIRMATION'}, opps)
    assert len(tr) == 2
    by_id = {t['opp']['id']: t for t in tr}
    assert by_id['a']['to'] == 'TRIGGERED'
    assert by_id['c']['from'] == 'WAITING_CONFIRMATION'


# ---------------- frozen-trade merge protection (regression) ----------------
def test_frozen_trade_not_replaced_by_fresh_plan():
    from analyzer.scanner import _carry_over_fresh
    frozen_statuses = ('TRIGGERED', 'TP1_HIT', 'TP2_HIT')
    # a triggered trade exists for POL
    frozen_op = {'id': 'POL_old', 'symbol': 'POLUSDT', 'direction': 'LONG',
                 'status': 'TRIGGERED', 'created_at': '2026-01-01T00:00:00Z', 'events': []}
    active = {'POLUSDT|LONG': frozen_op}
    fresh_plan = {'id': 'POL_new', 'symbol': 'POLUSDT', 'direction': 'LONG',
                  'status': 'WAITING_CONFIRMATION', 'entry_zone': [1, 2], 'events': []}
    new = _carry_over_fresh(active, [fresh_plan], '2026-01-01T01:00:00Z', frozen_statuses)
    assert new == []
    assert active['POLUSDT|LONG'] is frozen_op, 'frozen trade must be untouched'
    assert frozen_op['status'] == 'TRIGGERED'
    assert frozen_op['id'] == 'POL_old'


def test_frozen_tp_hit_not_replaced():
    from analyzer.scanner import _carry_over_fresh
    frozen_op = {'id': 'X_old', 'symbol': 'XUSDT', 'direction': 'LONG',
                 'status': 'TP1_HIT', 'created_at': 'x', 'events': []}
    active = {'XUSDT|LONG': frozen_op}
    fresh_plan = {'id': 'X_new', 'symbol': 'XUSDT', 'direction': 'LONG',
                  'status': 'READY', 'entry_zone': [1, 2], 'events': []}
    _carry_over_fresh(active, [fresh_plan], '2026-01-01T01:00:00Z', ('TRIGGERED', 'TP1_HIT', 'TP2_HIT'))
    assert active['XUSDT|LONG'] is frozen_op and frozen_op['status'] == 'TP1_HIT'


def test_non_frozen_refresh_keeps_identity():
    from analyzer.scanner import _carry_over_fresh
    ready_op = {'id': 'X_old', 'symbol': 'XUSDT', 'direction': 'LONG',
                'status': 'READY', 'created_at': '2026-01-01T00:00:00Z', 'events': []}
    active = {'XUSDT|LONG': ready_op}
    fresh_plan = {'id': 'X_new', 'symbol': 'XUSDT', 'direction': 'LONG',
                  'status': 'READY', 'entry_zone': [1, 2], 'events': []}
    new = _carry_over_fresh(active, [fresh_plan], '2026-01-01T01:00:00Z', ('TRIGGERED',))
    assert new == []
    merged = active['XUSDT|LONG']
    assert merged['id'] == 'X_old', 'identity carried over'
    assert merged['entry_zone'] == [1, 2], 'plan refreshed'
    assert merged['events'][-1]['from'] == 'READY'


def test_brand_new_opportunity_detected():
    from analyzer.scanner import _carry_over_fresh
    active = {}
    fresh_plan = {'id': 'N_new', 'symbol': 'NUSDT', 'direction': 'LONG',
                  'status': 'READY', 'entry_zone': [1, 2], 'events': []}
    new = _carry_over_fresh(active, [fresh_plan], '2026-01-01T01:00:00Z', ('TRIGGERED',))
    assert new == [fresh_plan]
    assert active['NUSDT|LONG'] is fresh_plan


# ---------------- market history recording ----------------
def test_record_market_history_caps_and_dedup(tmp_path, monkeypatch):
    from analyzer import scanner as sc
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))
    market = {'breadth_pct_above_ema50': 62.3, 'status': 'BULLISH', 'btc': {'price': 77000.0}}
    sc._record_market_history(market, '2026-01-01T00:00:00Z', 35.2)
    sc._record_market_history(market, '2026-01-01T00:00:00Z', 35.2)  # duplicate ignored
    for i in range(1, 25):
        sc._record_market_history({'breadth_pct_above_ema50': 50.0 + i, 'status': 'BULLISH', 'btc': {'price': 77000.0 + i}},
                                  f'2026-01-01T{i:02d}:00:00Z', 40.0)
    bh = st.load_json(st.data_path('breadth_history.json'))
    ul = st.load_json(st.data_path('update_log.json'))
    assert len(bh) == 25, f'cap + dedup (got {len(bh)})'
    assert len(ul) == 25
    assert bh[0]['breadth'] == 62.3
    assert ul[0]['ok'] is True and ul[0]['duration'] == 35.2


def test_record_market_history_never_raises(tmp_path, monkeypatch):
    from analyzer import scanner as sc
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))
    # even with a poisoned market payload, recording must not raise
    sc._record_market_history({'breadth_pct_above_ema50': None, 'status': None, 'btc': None}, 'x', None)
    assert True  # no exception = pass


# ---------------- publish-time freshness guards ----------------
def test_freshness_guard_suppresses_reached_tp1():
    from analyzer.scanner import _freshness_guard
    plan = {'direction': 'LONG', 'tp1': 100.0, 'status': 'READY', 'entry_zone': [95.0, 96.0]}
    assert _freshness_guard(plan, 100.5, 1.0) is None  # live >= TP1 -> suppress
    assert _freshness_guard(plan, 100.0, 1.0) is None  # exactly at TP1 -> suppress
    assert _freshness_guard(plan, 99.0, 1.0) is plan   # below TP1 -> keep


def test_freshness_guard_downgrades_runaway_ready():
    from analyzer.scanner import _freshness_guard
    plan = {'direction': 'LONG', 'tp1': 100.0, 'status': 'READY', 'entry_zone': [95.0, 96.0]}
    # live price far beyond the zone (96 + 1.2*atr) but below TP1 -> WAITING
    p = _freshness_guard(plan, 98.5, 1.5)
    assert p is plan and p['status'] == 'WAITING_CONFIRMATION'
    assert 'retest' in p['confirmation']
    # short side: below zone -> downgrade
    plan2 = {'direction': 'SHORT', 'tp1': 90.0, 'status': 'READY', 'entry_zone': [94.0, 95.0]}
    p2 = _freshness_guard(plan2, 91.5, 1.5)
    assert p2['status'] == 'WAITING_CONFIRMATION'


def test_freshness_guard_keeps_in_zone_ready():
    from analyzer.scanner import _freshness_guard
    plan = {'direction': 'LONG', 'tp1': 100.0, 'status': 'READY', 'entry_zone': [95.0, 96.0]}
    assert _freshness_guard(plan, 95.5, 1.0)['status'] == 'READY'


def test_min_tp1_distance_enforced():
    from analyzer import signal as sig
    t4 = {'last_high': 105, 'hi20': 106, 'hi50': 108}
    td = {'last_high': 110}
    cfg = {'min_tp1_distance_atr': 1.2, 'tp_snap_tolerance': 0.25}
    entry, R, atr = 100.0, 1.0, 1.0
    tp1, tp2, tp3 = sig._targets(entry, R, t4, td, 'LONG', atr, cfg)
    assert tp1 >= entry + 1.2 * atr, f'tp1 too close: {tp1}'
    assert tp1 < tp2 < tp3, f'ordering broken: {tp1}, {tp2}, {tp3}'
    # short side
    t4s = {'last_low': 95, 'lo20': 94, 'lo50': 92}
    tds = {'last_low': 90}
    tp1s, tp2s, tp3s = sig._targets(entry, R, t4s, tds, 'SHORT', atr, cfg)
    assert entry - tp1s >= 1.2 * atr
    assert tp1s > tp2s > tp3s


def test_parse_tolerant_formats():
    from analyzer.tracker import _parse
    from datetime import timezone
    assert _parse("2026-08-21T18:00:00Z") is not None
    assert _parse("2026-08-21 18:00:00Z") is not None, 'space separator must parse'
    assert _parse("2026-08-21T18:00:00.123Z") is not None
    assert _parse("2026-08-21 18:00:00+00:00") is not None
    assert _parse(None) is None and _parse("garbage") is None
    d = _parse("2026-08-21 18:00:00Z")
    assert d.tzinfo is not None and d.hour == 18


def test_history_dedupe_by_id():
    from analyzer.scanner import _dedupe_history
    hist = [
        {'id': 'A', 'pair': 'X/USDT', 'result': 'WIN', 'closed_at': '2026-01-01T00:00:00Z'},
        {'id': 'B', 'pair': 'Y/USDT', 'result': 'LOSS', 'closed_at': '2026-01-01T00:00:00Z'},
        {'id': 'A', 'pair': 'X/USDT', 'result': 'WIN', 'closed_at': '2026-01-01T01:00:00Z'},  # duplicate
    ]
    closed = [{'id': 'A', 'pair': 'X/USDT', 'result': 'WIN', 'closed_at': '2026-01-01T02:00:00Z'},
              {'id': 'C', 'pair': 'Z/USDT', 'result': 'WIN', 'closed_at': '2026-01-01T02:00:00Z'}]
    out = _dedupe_history(hist, closed)
    ids = [r['id'] for r in out]
    assert ids == ['A', 'B', 'C'], f'got {ids}'
    assert out[0]['closed_at'] == '2026-01-01T00:00:00Z', 'earliest record kept'


# ---------------- supertrend board ----------------

def _st_df(st_dirs, start='2026-05-01'):
    n = len(st_dirs)
    t = pd.date_range(start, periods=n, freq='1D', tz='UTC')
    c = [100.0 + i for i in range(n)]
    return pd.DataFrame({'t': t, 'c': c, 'st_dir': st_dirs,
                         'rsi': [55.0] * n, 'ema50': [90.0] * n})


def test_st_run_info_finds_run_start():
    from analyzer.scanner import _st_run_info
    df = _st_df([-1, -1, 1, 1, 1])
    info = _st_run_info(df)
    assert info is not None
    assert info['bars_held'] == 3
    assert info['price_at_signal'] == 102.0


def test_st_run_info_none_when_bearish():
    from analyzer.scanner import _st_run_info
    assert _st_run_info(_st_df([1, 1, -1])) is None


def test_st_board_build_and_removal():
    from analyzer.scanner import _build_st_signals
    daily = {
        'AAAUSDT': _st_df([-1] * 30 + [1, 1, 1]),
        'BBBUSDT': _st_df([1, 1] + [-1] * 32),
    }
    meta = {s: {'last': 100.0 + i} for i, s in enumerate(daily)}
    out = _build_st_signals(daily, meta, '2026-08-24T18:00:00+00:00',
                            period_seconds=86400, min_bars=30)
    assert out['count'] == 1
    assert [s['symbol'] for s in out['signals']] == ['AAAUSDT']
    rec = out['signals'][0]
    assert rec['bars_held'] == 3
    assert rec['change_pct'] is not None
    assert 'T' in rec['signal_at']


def test_st_board_newest_first():
    from analyzer.scanner import _build_st_signals
    daily = {
        'OLDUSDT': _st_df([1] * 40, start='2026-04-01'),
        'NEWUSDT': _st_df([-1] * 39 + [1], start='2026-04-01'),
    }
    meta = {s: {'last': 100.0} for s in daily}
    out = _build_st_signals(daily, meta, '2026-08-24T18:00:00+00:00',
                            period_seconds=86400, min_bars=30)
    syms = [s['symbol'] for s in out['signals']]
    assert syms == ['NEWUSDT', 'OLDUSDT'], f'got {syms}'


def test_st_board_skips_short_history():
    from analyzer.scanner import _build_st_signals
    daily = {'TINYUSDT': _st_df([1] * 5)}
    meta = {'TINYUSDT': {'last': 100.0}}
    out = _build_st_signals(daily, meta, '2026-08-24T18:00:00+00:00',
                            period_seconds=86400, min_bars=30)
    assert out['count'] == 0


def test_st_board_drops_signals_older_than_max_age():
    from analyzer.scanner import _build_st_signals
    # OLD ran UP for 40 days (aged trend, not a fresh entry), NEW is 1 day old
    daily = {
        'OLDUSDT': _st_df([1] * 40, start='2026-04-01'),
        'NEWUSDT': _st_df([-1] * 39 + [1], start='2026-04-01'),
    }
    meta = {s: {'last': 100.0} for s in daily}
    # no cap -> both listed
    out = _build_st_signals(daily, meta, '2026-08-24T18:00:00+00:00',
                            period_seconds=86400, min_bars=30)
    assert [s['symbol'] for s in out['signals']] == ['NEWUSDT', 'OLDUSDT']
    # cap of 30 (daily candles) -> the 40-day-old run is dropped
    out = _build_st_signals(daily, meta, '2026-08-24T18:00:00+00:00', max_age=30,
                            period_seconds=86400, min_bars=30)
    assert [s['symbol'] for s in out['signals']] == ['NEWUSDT']


# ---------------- SuperTrend daily board: closed-candles correctness -------
def _daily_frame(n=150, start=100.0, drift=0.9, noise=0.35, end_offset_days=1, seed=11):
    """Daily candles ending `end_offset_days` days ago (1 = last closed day)."""
    df = make_df(n=n, start=start, drift=drift, noise=noise, seed=seed)
    end = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=end_offset_days))
    t = pd.date_range(end=end, periods=n, freq='1D', tz='UTC')
    df['t'] = t
    return df


def _today_bar(last_close, mult):
    t0 = pd.Timestamp.utcnow().normalize()
    return {'t': t0, 'o': last_close, 'h': last_close * max(mult, 1.0),
            'l': last_close * min(mult, 1.0), 'c': last_close * mult, 'v': 500.0}


def test_st_board_lists_confirmed_uptrend():
    from analyzer.scanner import _build_st_signals
    df = enrich(_daily_frame())
    assert int(df['st_dir'].iloc[-1]) == 1  # sanity: confirmed UP run
    board = _build_st_signals({'TESTUSDT': df}, {'TESTUSDT': {'last': 999}}, 'now',
                              period_seconds=86400, min_bars=30)
    assert board['count'] == 1 and board['signals'][0]['symbol'] == 'TESTUSDT'


def test_st_board_removes_signal_immediately_on_intraday_flip_down():
    from analyzer.scanner import _build_st_signals
    df = enrich(_daily_frame())
    # today's OPEN candle crashes hard -> live SuperTrend flips DOWN
    crash = pd.DataFrame([_today_bar(float(df['c'].iloc[-1]), 0.80)])
    live = enrich(pd.concat([df[['t', 'o', 'h', 'l', 'c', 'v']], crash],
                            ignore_index=True))
    assert int(live['st_dir'].iloc[-1]) == -1  # the flip exists intraday
    board = _build_st_signals({'TESTUSDT': live}, {'TESTUSDT': {'last': 999}}, 'now',
                              period_seconds=86400, min_bars=30)
    # fast-exit policy: removed from the board immediately, even though the
    # confirmed daily run is still UP until the candle closes
    assert board['count'] == 0


def test_st_board_keeps_signal_while_open_candle_stays_up():
    from analyzer.scanner import _build_st_signals
    df = enrich(_daily_frame())
    cont = pd.DataFrame([_today_bar(float(df['c'].iloc[-1]), 1.02)])
    live = enrich(pd.concat([df[['t', 'o', 'h', 'l', 'c', 'v']], cont],
                            ignore_index=True))
    assert int(live['st_dir'].iloc[-1]) == 1
    board = _build_st_signals({'TESTUSDT': live}, {'TESTUSDT': {'last': 999}}, 'now',
                              period_seconds=86400, min_bars=30)
    assert board['count'] == 1  # no flip -> stays listed


def test_st_board_no_phantom_signal_from_intraday_flip_up():
    from analyzer.scanner import _build_st_signals
    df = enrich(_daily_frame(start=200.0, drift=-0.5, seed=13))  # confirmed DOWN
    assert int(df['st_dir'].iloc[-1]) == -1
    pump = pd.DataFrame([_today_bar(float(df['c'].iloc[-1]), 3.0)])
    live = enrich(pd.concat([df[['t', 'o', 'h', 'l', 'c', 'v']], pump],
                            ignore_index=True))
    assert int(live['st_dir'].iloc[-1]) == 1  # the open candle flips UP live
    board = _build_st_signals({'TESTUSDT': live}, {'TESTUSDT': {'last': 999}}, 'now',
                              period_seconds=86400, min_bars=30)
    # no signal may appear before the daily candle confirms the flip
    assert board['count'] == 0


# ---------------- SuperTrend hourly board (default timeframe) ----------------
def test_st_board_hourly_default_and_days_to_hours_age():
    from analyzer.scanner import _build_st_signals
    # hourly bars with >= min_bars so the board includes them all
    def hourly(dirs):
        n = len(dirs)
        t = pd.date_range('2026-08-01', periods=n, freq='1h', tz='UTC')
        c = [100.0 + i for i in range(n)]
        return pd.DataFrame({'t': t, 'c': c, 'st_dir': dirs,
                             'rsi': [55.0] * n, 'ema50': [90.0] * n})
    frames = {
        'FRESHUSDT': hourly([-1] * 250 + [1] * 50),   # UP run of 50h (fresh)
        'AGEDUSDT': hourly([1] * 800),                # UP run of 800h (aged)
        'DOWNUSDT': hourly([-1] * 300),               # not in an UP run
    }
    meta = {s: {'last': 100.0} for s in frames}
    # default board is hourly; max_age is in hours here (30 days = 720h)
    board = _build_st_signals(frames, meta, '2026-08-24T18:00:00+00:00',
                              max_age=30 * 24)
    assert board['timeframe'] == '1h'
    syms = [s['symbol'] for s in board['signals']]
    assert 'FRESHUSDT' in syms, f'got {syms}'
    assert 'AGEDUSDT' not in syms, f'got {syms}'
    assert 'DOWNUSDT' not in syms, f'got {syms}'


# ---------------- quant-agent signal history ----------------
def test_record_agent_history_dedupe_and_ends(tmp_path, monkeypatch):
    from analyzer import scanner as sc
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))

    def signal(sym, tf, score, price):
        return {'symbol': sym, 'timeframe': tf, 'score': score,
                'bars': [{'close': price}]}

    sc._record_agent_history({'signals': [signal('BTCUSDT', '1h', 90, 70000)]},
                             '2026-01-01T10:00:00Z')
    # same symbol+tf again -> deduped (no duplicate ts row appended)
    sc._record_agent_history({'signals': [signal('BTCUSDT', '1h', 91, 70100)]},
                             '2026-01-01T11:00:00Z')
    # different symbol+tf -> appended
    sc._record_agent_history({'signals': [signal('ETHUSDT', '15m', 85, 3200)]},
                             '2026-01-01T12:00:00Z')
    hist = st.load_json(st.data_path('agent_history.json'))
    assert len(hist) == 2, f'expect 2 unique (sym|tf) rows, got {len(hist)}'
    assert hist[0]['symbol'] == 'BTCUSDT' and hist[0]['score'] == 91  # updated on 2nd scan
    assert hist[1]['symbol'] == 'ETHUSDT'

    # when the BTC signal disappears from the scan, the same-day row gets ended_at
    sc._record_agent_history({'signals': [signal('ETHUSDT', '15m', 86, 3210)]},
                             '2026-01-01T13:00:00Z')
    hist = st.load_json(st.data_path('agent_history.json'))
    btc = [r for r in hist if r['symbol'] == 'BTCUSDT'][0]
    assert btc.get('ended_at') == '2026-01-01T12:00:00Z'
    eth = [r for r in hist if r['symbol'] == 'ETHUSDT'][0]
    assert eth.get('ended_at') is None


def test_record_agent_history_never_raises(tmp_path, monkeypatch):
    from analyzer import scanner as sc
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))
    sc._record_agent_history({'signals': [{'bars': []}], 'broken': True}, 'x')
    assert True


# ---------------- WhatsApp alerts ----------------
def test_whatsapp_filter_new_st_signals(tmp_path, monkeypatch):
    from analyzer import whatsapp as wa
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))
    board1 = {'signals': [{'symbol': 'BTCUSDT'}, {'symbol': 'ETHUSDT'}]}
    new1 = wa.filter_new_st_signals(board1)
    assert {s['symbol'] for s in new1} == {'BTCUSDT', 'ETHUSDT'}
    # second cycle with no new symbol -> nothing new
    new2 = wa.filter_new_st_signals({'signals': [{'symbol': 'BTCUSDT'}, {'symbol': 'ETHUSDT'}]})
    assert new2 == []
    # new symbol added -> only the newcomer is reported
    board3 = {'signals': [{'symbol': 'BTCUSDT'}, {'symbol': 'ETHUSDT'}, {'symbol': 'SOLUSDT'}]}
    new3 = {s['symbol'] for s in wa.filter_new_st_signals(board3)}
    assert new3 == {'SOLUSDT'}


def test_whatsapp_only_sends_fresh_st_signals(tmp_path, monkeypatch):
    from analyzer import whatsapp as wa
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))
    # a fresh 2h-old signal should alert; a 30h-old one must NOT
    board = {'signals': [
        {'symbol': 'FRESHUSDT', 'bars_held': 2},
        {'symbol': 'AGEDUSDT', 'bars_held': 30},
        {'symbol': 'ALSOFRESHUSDT', 'bars_held': 23},
    ]}
    got = {s['symbol'] for s in wa.filter_new_st_signals(board, max_fresh_hours=24)}
    assert got == {'FRESHUSDT', 'ALSOFRESHUSDT'}, f'got {got}'
    # the aged symbol was still marked seen, so it never re-alerts
    again = wa.filter_new_st_signals(board, max_fresh_hours=24)
    assert again == []


def test_whatsapp_filter_new_opportunities(tmp_path, monkeypatch):
    from analyzer import whatsapp as wa
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))

    def opp(sym, status, score):
        return {'symbol': sym, 'direction': 'LONG', 'status': status, 'score': score}

    first = [opp('BTCUSDT', 'READY', 88), opp('LOWUSDT', 'READY', 60),
             opp('WAITUSDT', 'WAITING_CONFIRMATION', 90), opp('DRAFTUSDT', 'DRAFT', 95)]
    out = wa.filter_new_opportunities(first, min_score=84)
    got = {(o['symbol']) for o in out}
    # only READY/WAITING above min_score are notified
    assert got == {'BTCUSDT', 'WAITUSDT'}
    # same opp again -> not re-notified
    again = wa.filter_new_opportunities([opp('BTCUSDT', 'READY', 89)], min_score=84)
    assert again == []


def test_whatsapp_send_requires_token_and_cfg(tmp_path, monkeypatch):
    from analyzer import whatsapp as wa
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))
    monkeypatch.delenv('WHATSAPP_TOKEN', raising=False)
    cfg = {'whatsapp': {'enabled': True, 'to': '966533170332',
                        'endpoint': 'https://example.invalid'}}
    # no token -> never sends
    assert wa.send_whatsapp('hi', cfg) is False
    # disabled -> never sends even with token
    monkeypatch.setenv('WHATSAPP_TOKEN', 'tok')
    cfg2 = {'whatsapp': {'enabled': False, 'to': 'x', 'endpoint': 'https://example.invalid'}}
    assert wa.send_whatsapp('hi', cfg2) is False


def test_whatsapp_send_posts_to_endpoint(tmp_path, monkeypatch):
    from analyzer import whatsapp as wa
    from analyzer import storage as st
    monkeypatch.setattr(st, 'DATA_DIR', str(tmp_path))
    monkeypatch.setenv('WHATSAPP_TOKEN', 'sekret')
    captured = {}

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        captured['url'] = req.full_url
        captured['method'] = req.get_method()
        captured['auth'] = req.get_header('Authorization')
        captured['body'] = req.data.decode('utf-8')
        return FakeResp()

    monkeypatch.setattr(wa.urllib.request, 'urlopen', fake_urlopen)
    cfg = {'whatsapp': {'enabled': True, 'to': '966533170332',
                        'endpoint': 'https://wa.example/api/v1/send'}}
    assert wa.send_whatsapp('hello', cfg) is True
    assert captured['url'] == 'https://wa.example/api/v1/send'
    assert captured['method'] == 'POST'
    assert captured['auth'] == 'Bearer sekret'
    assert '"to": "966533170332"' in captured['body']
    assert '"message": "hello"' in captured['body']
