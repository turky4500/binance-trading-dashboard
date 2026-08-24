# -*- coding: utf-8 -*-
"""Tests for the deterministic multi-timeframe SuperTrend agent."""
import numpy as np
import pandas as pd
import pytest

from analyzer.quant_agent import (
    SCHEMA_VERSION,
    TIMEFRAME_CHAINS,
    _flip_count,
    _upper_wick_rejection,
    evaluate_candidate,
    run_quant_agent,
)


def trend_frame(n=260):
    closes = np.linspace(100.0, 108.5, n)
    # Last three closed candles hold above the prior 20-bar resistance. The
    # final row represents the still-forming Binance candle and is discarded.
    closes[-5:] = [108.30, 108.65, 108.90, 109.10, 109.20]
    opens = closes - 0.05
    highs = closes + 0.05
    lows = opens - 0.05
    volumes = np.full(n, 1000.0)
    times = pd.date_range('2026-01-01', periods=n, freq='15min', tz='UTC')
    return pd.DataFrame({'t': times, 'o': opens, 'h': highs, 'l': lows, 'c': closes, 'v': volumes})


def settings(**overrides):
    qa = {
        'timeframes': ['15m', '1h', '4h'],
        'min_score': 82,
        'min_rr_tp1': 1.5,
        'max_signals': 12,
        'max_signals_per_timeframe': 4,
    }
    qa.update(overrides)
    return {
        'supertrend': {'period': 10, 'multiplier': 3.0},
        'market_filter': {'enabled': True, 'min_breadth_pct': 40.0},
        'quant_agent': qa,
    }


def ticker():
    return {'last': 109.10, 'quoteVol': 50e6, 'spread': 0.02,
            'trades': 100000, 'chg24': 2.0}


def frames():
    df = trend_frame()
    return {tf: df.copy() for tf in ('15m', '1h', '4h', '1d')}


def test_flip_counter_detects_chop():
    assert _flip_count([1, 1, 1]) == 0
    assert _flip_count([1, -1, 1]) == 2
    assert _flip_count([-1, -1, 1]) == 1


@pytest.mark.parametrize('primary', ['15m', '1h', '4h'])
def test_each_requested_timeframe_can_emit_a_strict_plan(primary):
    signal, rejection, diagnostics = evaluate_candidate(
        'TESTUSDT', ticker(), frames(), settings(), '2026-01-01T12:00:00Z',
        primary_timeframe=primary)
    assert rejection is None
    assert signal is not None
    assert signal['setup_type'] == 'SCALP_SUPERTREND'
    assert signal['direction'] == 'LONG' and signal['status'] == 'READY'
    assert signal['primary_timeframe'] == primary
    assert signal['timeframes'] == list(TIMEFRAME_CHAINS[primary])
    assert signal['score'] >= 82
    assert signal['stop_loss'] < signal['entry_mid'] < signal['tp1'] < signal['tp2'] < signal['tp3']
    assert signal['rr_tp1'] >= 1.5
    assert signal['entry_zone'][0] <= signal['current_price'] <= signal['entry_zone'][1]
    assert diagnostics['entry_trigger'] == 'BREAKOUT_HOLD'
    assert diagnostics['primary_timeframe'] == primary
    assert signal['reason']['ar'] and signal['reason']['en']


def test_overextended_price_is_rejected():
    hot = ticker()
    hot['last'] = 125.0
    signal, rejection, _ = evaluate_candidate(
        'HOTUSDT', hot, frames(), settings(), '2026-01-01T12:00:00Z')
    assert signal is None
    assert rejection['codes'] == ['EMA200_OVEREXTENDED']
    assert rejection['primary_timeframe'] == '15m'


def test_live_price_chase_is_rejected():
    chased = ticker()
    chased['last'] = 109.8  # below EMA extension ceiling, but too far from the closed bar in ATR units
    signal, rejection, diagnostics = evaluate_candidate(
        'CHASEUSDT', chased, frames(), settings(), '2026-01-01T12:00:00Z')
    assert signal is None
    assert rejection['codes'] == ['LIVE_PRICE_CHASE']
    assert diagnostics['live_chase_atr'] > 0.75


def test_upper_wick_filter_at_resistance():
    df = trend_frame(80)
    i = len(df) - 2
    df.loc[i, 'o'] = 108.9
    df.loc[i, 'c'] = 109.0
    df.loc[i, 'h'] = 111.0
    assert _upper_wick_rejection(df.iloc[:-1].reset_index(drop=True), {
        'max_upper_wick_body_ratio': 1.5,
        'resistance_proximity_pct': 0.30,
    }) is True


def test_run_scans_all_three_timeframes_independently():
    raw = frames()
    market = {'status': 'BULLISH', 'breadth_pct_above_ema50': 75.0, 'new_setups_gated': False}
    doc = run_quant_agent(
        [('TESTUSDT', ticker())],
        {'TESTUSDT': {k: raw[k] for k in ('15m', '1h', '4h')}},
        {'TESTUSDT': raw['1d']}, market, settings(), '2026-01-01T12:00:00Z')
    assert doc['schema_version'] == SCHEMA_VERSION
    assert doc['timeframes_scanned'] == ['15m', '1h', '4h']
    assert doc['symbols_scanned'] == 1
    assert doc['total_scanned'] == 3
    assert doc['opportunities_found'] == 3
    assert doc['opportunities_by_timeframe'] == {'15m': 1, '1h': 1, '4h': 1}
    assert {s['primary_timeframe'] for s in doc['signals']} == {'15m', '1h', '4h'}


def test_market_gate_returns_one_rejection_per_timeframe():
    market = {'status': 'BEARISH', 'breadth_pct_above_ema50': 25.0, 'new_setups_gated': True}
    doc = run_quant_agent(
        [('TESTUSDT', ticker())], {}, {}, market, settings(), '2026-01-01T12:00:00Z')
    assert doc['schema_version'] == SCHEMA_VERSION
    assert doc['symbols_scanned'] == 1
    assert doc['total_scanned'] == 3
    assert doc['opportunities_found'] == 0
    assert doc['signals'] == []
    assert len(doc['rejections']) == 3
    assert {r['primary_timeframe'] for r in doc['rejections']} == {'15m', '1h', '4h'}
    assert all(r['codes'] == ['MARKET_BREADTH_GATE'] for r in doc['rejections'])
    assert doc['no_opportunity_reason']['ar']


def test_missing_frames_do_not_break_any_timeframe_scan():
    market = {'status': 'BULLISH', 'breadth_pct_above_ema50': 75.0, 'new_setups_gated': False}
    doc = run_quant_agent(
        [('TESTUSDT', ticker())], {}, {}, market, settings(), '2026-01-01T12:00:00Z')
    assert doc['status'] == 'ok'
    assert doc['opportunities_found'] == len(doc['signals']) == 0
    assert len(doc['rejections']) == 3
    assert {r['primary_timeframe'] for r in doc['rejections']} == {'15m', '1h', '4h'}
    assert all(r['codes'] == ['MISSING_DATA'] for r in doc['rejections'])
