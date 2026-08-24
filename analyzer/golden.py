# -*- coding: utf-8 -*-
"""
Golden reference generator for the JavaScript Coin Analyzer engine.

Produces tests/golden_reference.json: per symbol, the frozen 24h market meta,
the per-timeframe indicator state, and the best plan + score computed by the
Python engine on the same bars.

The Node golden test (tests/golden_js.test.js) fetches the SAME historical
bars, runs the JavaScript mirror engine, and asserts parity within small
tolerances — so the two engines can never silently drift apart.

Regenerate whenever the engine rules change:
    python -m analyzer.golden
"""
import json
import os

from . import binance_client as bc
from .indicators import klines_to_df, enrich
from .signal import tf_state, detect_breakout, generate_plans
from .scoring import score_plan
from .run import load_config
from .scanner import _freshness_guard

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'TRXUSDT', 'AAVEUSDT', 'SUIUSDT']
TFS = {'15m': (500, 2), '1h': (400, 2), '4h': (400, 3), '1d': (400, 3)}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_dict(st):
    return {
        'close': round(float(st['close']), 8),
        'ema20': round(float(st['ema20']), 8),
        'ema50': round(float(st['ema50']), 8),
        'ema200': round(float(st['ema200']), 8),
        'rsi': round(float(st['rsi']), 3),
        'macd_h': round(float(st['macd_h']), 8),
        'macd_h_prev': round(float(st['macd_h_prev']), 8),
        'atr': round(float(st['atr']), 8),
        'vwap': round(float(st['vwap']), 8) if st['vwap'] == st['vwap'] else None,
        'vol_ratio3': round(float(st['vol_ratio3']), 4),
        'above20': bool(st['above20']), 'above50': bool(st['above50']), 'above200': bool(st['above200']),
        'e20_gt_e50': bool(st['e20_gt_e50']),
        'last_high': round(float(st['last_high']), 8),
        'last_low': round(float(st['last_low']), 8),
        'hi20': round(float(st['hi20']), 8), 'lo20': round(float(st['lo20']), 8),
        'hi50': round(float(st['hi50']), 8), 'lo50': round(float(st['lo50']), 8),
        'hi6': round(float(st['hi6']), 8), 'lo6': round(float(st['lo6']), 8),
        'st_dir': int(st['st_dir']), 'st_line': round(float(st['st_line']), 8),
    }


def generate():
    cfg = load_config()
    risk = dict(cfg['risk'])
    risk['min_rr_tp1'] = cfg.get('min_rr_tp1', 1.0)
    risk['disabled_setups'] = list(cfg.get('strategy', {}).get('disabled_setups', []))
    stp = cfg.get('supertrend', {'period': 10, 'multiplier': 3.0})
    weights = cfg['scoring']

    tickers = {x['symbol']: x for x in bc.ticker_24h()}
    books = {x['symbol']: x for x in bc.book_ticker()}

    ref = {'config': {'risk': cfg['risk'], 'scoring': cfg['scoring'],
                      'min_score_to_show': cfg['min_score_to_show'],
                      'min_rr_tp1': cfg.get('min_rr_tp1', 1.0),
                      'allow_shorts': cfg.get('strategy', {}).get('allow_shorts', True),
                      'disabled_setups': list(cfg.get('strategy', {}).get('disabled_setups', [])),
                      'supertrend': stp},
           'symbols': {}}
    for sym in SYMBOLS:
        frames = {}
        for tf, (limit, _k) in TFS.items():
            d = enrich(klines_to_df(bc.klines(sym, tf, limit)), st_period=stp['period'], st_mult=stp['multiplier'])
            # drop the in-progress (still forming) bar: closed bars are immutable,
            # which makes the golden comparison fully deterministic forever
            frames[tf] = d.iloc[:-1]
        tf_states = {}
        for tf, (limit, k) in TFS.items():
            st = tf_state(frames[tf], k=k)
            tf_states[tf] = {'limit': limit, 'last_ts': int(frames[tf]['t'].iloc[-1].timestamp() * 1000),
                             'state': _state_dict(st)}
        brk = detect_breakout(frames['4h'], None)
        tf = {'15m': tf_state(frames['15m'], k=2), '1h': tf_state(frames['1h'], k=2),
              '4h': tf_state(frames['4h'], k=3), '1d': tf_state(frames['1d'], k=3)}
        plans = generate_plans(tf, brk, risk)
        if not cfg.get('strategy', {}).get('allow_shorts', True):
            plans = [p for p in plans if p['direction'] == 'LONG']
        t = tickers[sym]; b = books[sym]
        last = float(t['lastPrice'])
        meta24 = {
            'quoteVol': float(t['quoteVolume']),
            'spread': (float(b['askPrice']) - float(b['bidPrice'])) / last * 100,
            'trades': int(t['count']),
            'chg24': float(t['priceChangePercent']),
            'currentPrice': last,
        }
        plan_ref = None
        if plans:
            # mirror the pipeline publish-time freshness guard exactly
            atr4h = tf['4h']['atr']
            plans = [p for p in (_freshness_guard(p, last, atr4h) for p in plans) if p]
            scored = []
            for p in plans:
                score, parts = score_plan(tf, p, meta24, weights)
                scored.append((p, score))
            # mirror the live pipeline: only display plans above the threshold
            scored = [(p, s) for p, s in scored if s >= cfg['min_score_to_show']]
            scored.sort(key=lambda x: -x[1])
            if scored:
                p, score = scored[0]
                entry = p['entry_mid']
                R = abs(entry - p['stop_loss'])
                plan_ref = {
                    'setup_type': p['setup_type'], 'direction': p['direction'], 'status': p['status'],
                    'entry_zone': [round(x, 8) for x in p['entry_zone']],
                    'stop_loss': round(p['stop_loss'], 8),
                    'tp1': round(p['tp1'], 8), 'tp2': round(p['tp2'], 8), 'tp3': round(p['tp3'], 8),
                    'invalidation_level': round(p['invalidation_level'], 8),
                    'score': score,
                    'rr_tp1': round(abs(p['tp1'] - entry) / R, 3),
                    'rr_tp2': round(abs(p['tp2'] - entry) / R, 3),
                }
        ref['symbols'][sym] = {'meta24': meta24, 'tfs': tf_states, 'plan': plan_ref}
        print(f"{sym}: plan={plan_ref['setup_type'] if plan_ref else None} score={plan_ref['score'] if plan_ref else '—'}")

    out = os.path.join(ROOT, 'tests', 'golden_reference.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(ref, f, ensure_ascii=False, indent=1)
    print('wrote', out, os.path.getsize(out), 'bytes')


if __name__ == '__main__':
    generate()
