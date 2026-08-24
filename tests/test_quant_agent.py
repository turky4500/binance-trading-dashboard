# -*- coding: utf-8 -*-
"""Tests for the deterministic SuperTrend scalp agent."""
import numpy as np
import pandas as pd

from analyzer.quant_agent import (
    SCHEMA_VERSION,
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
    qa = {'min_score': 82, 'min_rr_tp1': 1.5}
    qa.update(overrides)
    return {
        'supertrend': {'period': 10, 'multiplier': 3.0},
        'market_filter': {'enabled': True, 'min_breadth_pct': 40.0},
        'quant_agent': qa,
    }


def ticker():
    return {'last': 109.10, 'quoteVol': 50e6, 'spread': 0.02,
            'trades': 100000, 'chg24': 2.0}


def test_flip_counter_detects_chop():
    assert _flip_count([1, 1, 1]) == 0
    assert _flip_count([1, -1, 1]) == 2
    assert _flip_count([-1, -1, 1]) == 1


def test_valid_supertrend_scalp_emits_strict_plan():
    df = trend_frame()
    frames = {tf: df.copy() for tf in ('15m', '1h', '4h', '1d')}
    signal, rejection, diagnostics = evaluate_candidate(
        'TESTUSDT', ticker(), frames, settings(), '2026-01-01T12:00:00Z')
    assert rejection is None
    assert signal is not None
    assert signal['setup_type'] == 'SCALP_SUPERTREND'
    assert signal['direction'] == 'LONG' and signal['status'] == 'READY'
    assert signal['score'] >= 82
    assert signal['stop_loss'] < signal['entry_mid'] < signal['tp1'] < signal['tp2'] < signal['tp3']
    assert signal['rr_tp1'] >= 1.5
    assert signal['entry_zone'][0] <= signal['current_price'] <= signal['entry_zone'][1]
    assert diagnostics['entry_trigger'] == 'BREAKOUT_HOLD'
    assert signal['reason']['ar'] and signal['reason']['en']


def test_overextended_price_is_rejected():
    df = trend_frame()
    frames = {tf: df.copy() for tf in ('15m', '1h', '4h', '1d')}
    hot = ticker()
    hot['last'] = 125.0
    signal, rejection, _ = evaluate_candidate(
        'HOTUSDT', hot, frames, settings(), '2026-01-01T12:00:00Z')
    assert signal is None
    assert rejection['codes'] == ['EMA200_OVEREXTENDED']


def test_live_price_chase_is_rejected():
    df = trend_frame()
    frames = {tf: df.copy() for tf in ('15m', '1h', '4h', '1d')}
    chased = ticker()
    chased['last'] = 109.8  # below the EMA extension ceiling, but far beyond the closed bar in ATR units
    signal, rejection, diagnostics = evaluate_candidate(
        'CHASEUSDT', chased, frames, settings(), '2026-01-01T12:00:00Z')
    assert signal is None
    assert rejection['codes'] == ['LIVE_PRICE_CHASE']
    assert diagnostics['live_chase_atr'] > 0.75


def test_upper_wick_filter_at_resistance():
    df = trend_frame(80)
    # Put a large rejection wick on the latest inspected candle at resistance.
    i = len(df) - 2
    df.loc[i, 'o'] = 108.9
    df.loc[i, 'c'] = 109.0
    df.loc[i, 'h'] = 111.0
    assert _upper_wick_rejection(df.iloc[:-1].reset_index(drop=True), {
        'max_upper_wick_body_ratio': 1.5,
        'resistance_proximity_pct': 0.30,
    }) is True


def test_market_gate_returns_valid_empty_document():
    market = {'status': 'BEARISH', 'breadth_pct_above_ema50': 25.0, 'new_setups_gated': True}
    doc = run_quant_agent(
        [('TESTUSDT', ticker())], {}, {}, market, settings(), '2026-01-01T12:00:00Z')
    assert doc['schema_version'] == SCHEMA_VERSION
    assert doc['total_scanned'] == 1
    assert doc['opportunities_found'] == 0
    assert doc['signals'] == []
    assert doc['rejections'][0]['codes'] == ['MARKET_BREADTH_GATE']
    assert doc['no_opportunity_reason']['ar']


def test_missing_frames_do_not_break_scan():
    market = {'status': 'BULLISH', 'breadth_pct_above_ema50': 75.0, 'new_setups_gated': False}
    doc = run_quant_agent(
        [('TESTUSDT', ticker())], {}, {}, market, settings(), '2026-01-01T12:00:00Z')
    assert doc['status'] == 'ok'
    assert doc['opportunities_found'] == len(doc['signals']) == 0
    assert doc['rejections'][0]['codes'] == ['MISSING_DATA']
