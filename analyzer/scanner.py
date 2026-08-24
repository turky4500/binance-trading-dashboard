# -*- coding: utf-8 -*-
"""
Market scanner orchestration:

   Binance public data -> liquidity filter -> daily trend screen
   -> multi-timeframe technical analysis -> deterministic trade plans
   -> 100-point scoring -> filtering -> lifecycle tracking -> JSON storage
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import binance_client as bc
from .indicators import klines_to_df, enrich
from .signal import tf_state, detect_breakout, generate_plans
from .scoring import score_plan, grade, COMPONENT_LABELS
from .explain import (reason_entry, reason_sl, reason_tp, reason_invalidation,
                      volume_note, momentum_note)
from .tracker import track, performance_stats, TERMINAL
from .storage import load_json, save_json, data_path, iso
from .quant_agent import run_quant_agent

TFS = ['15m', '1h', '4h', '1d']
SKIP_BASE = {
    'USDC', 'FDUSD', 'TUSD', 'USDP', 'DAI', 'AEUR', 'BUSD', 'PAXG', 'XAUT', 'PYUSD',
    'XUSD', 'BFUSD', 'EURI', 'RLUSD', 'USDE', 'USD1', 'USDR', 'USDX', 'U', 'WBTC', 'BTCB', 'CBBTC',
    'LBTC', 'WETH', 'WBETH', 'WBNB', 'STETH', 'LSTETH', 'EUR', 'USTC',
}


def _fetch_many(fn, items, workers=6, desc=""):
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for f in as_completed(futs):
            it = futs[f]
            try:
                res = f.result()
                if res is not None:
                    out[it] = res
            except Exception:
                continue
    return out


def _get_klines(sym, tf, limit):
    try:
        return sym, tf, bc.klines(sym, tf, limit)
    except Exception:
        return sym, tf, None


def scan(cfg, now_iso=None, verbose=True):
    now_iso = now_iso or iso()
    errors = []
    uni = cfg['universe']
    stp = cfg.get('supertrend', {'period': 10, 'multiplier': 3.0})
    t0 = time.time()

    # ---------- 1. market snapshot: tickers + spreads + tradable symbols
    try:
        tickers = bc.ticker_24h()
        books = bc.book_ticker()
        einfo = bc.exchange_info()
    except Exception as e:
        raise RuntimeError(f"Binance market snapshot failed: {e}")

    allowed = {s['symbol'] for s in einfo['symbols']
               if s['status'] == 'TRADING' and s.get('isSpotTradingAllowed')}
    tk = {x['symbol']: x for x in tickers}
    bk = {x['symbol']: x for x in books}

    rows = []
    for sym, x in tk.items():
        if sym not in allowed or not sym.endswith(uni['quote_asset']):
            continue
        base = sym.replace(uni['quote_asset'], '')
        if base in SKIP_BASE or base in set(uni.get('exclude_assets', [])):
            continue
        b = bk.get(sym)
        if not b:
            continue
        last = float(x['lastPrice'])
        bid, ask = float(b['bidPrice']), float(b['askPrice'])
        if last <= 0 or bid <= 0:
            continue
        spread = (ask - bid) / last * 100
        qv = float(x['quoteVolume'])
        chg = float(x['priceChangePercent'])
        trades = int(x['count'])
        if qv < uni['min_quote_volume_24h'] or trades < uni['min_trades_24h']:
            continue
        if spread > uni['max_spread_pct']:
            continue
        if chg > uni['exclude_24h_change_gt'] or chg < uni['exclude_24h_change_lt']:
            continue
        rows.append({'sym': sym, 'quoteVol': qv, 'spread': spread, 'chg24': chg,
                     'trades': trades, 'last': last})
    rows.sort(key=lambda r: -r['quoteVol'])
    universe = rows[:uni['max_symbols_to_screen']]
    if verbose:
        print(f"[1/6] Universe: {len(rows)} liquid symbols -> screening {len(universe)}")

    # ---------- 2. daily klines for the universe
    daily = {}
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '1d', 400), [r['sym'] for r in universe]).values():
        if k is None:
            continue
        try:
            daily[sym] = enrich(klines_to_df(k), st_period=stp['period'], st_mult=stp['multiplier'])
        except Exception:
            continue
    if verbose:
        print(f"[2/6] Daily klines: {len(daily)} symbols")

    # ---------- 3. daily screen -> candidates
    cand = []
    meta_by_sym = {r['sym']: r for r in universe}
    for sym, df in daily.items():
        if len(df) < uni['min_daily_candles']:
            continue
        c = df['c'].iloc[-1]
        e20, e50 = df['ema20'].iloc[-1], df['ema50'].iloc[-1]
        r = df['rsi'].iloc[-1]
        mh = df['macd_h'].iloc[-1]
        bullish = c > e20 > e50 and 45 <= r <= 80 and mh > 0
        bearish = c < e20 < e50 and 20 <= r <= 58 and mh < 0
        if bullish or bearish:
            cand.append((sym, meta_by_sym[sym]))
    cand.sort(key=lambda x: -x[1]['quoteVol'])
    cand = cand[:uni['max_candidates_deep']]
    if verbose:
        print(f"[3/6] Candidates for deep analysis: {len(cand)}")

    # ---------- 4. intraday klines for candidates (+ tracked symbols)
    tracked_prev = load_json(data_path('opportunities.json'), [])
    tracked_syms = [o['symbol'] for o in tracked_prev if o.get('status') not in TERMINAL]
    need_syms = [s for s, _ in cand]
    intraday = {}
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '15m', 500), need_syms).values():
        if k is not None:
            intraday.setdefault(sym, {})['15m'] = klines_to_df(k)
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '1h', 400), need_syms).values():
        if k is not None:
            intraday.setdefault(sym, {})['1h'] = klines_to_df(k)
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '4h', 400), need_syms).values():
        if k is not None:
            intraday.setdefault(sym, {})['4h'] = klines_to_df(k)
    # 15m refresh for previously published (still open) setups
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '15m', 500), tracked_syms).values():
        if k is not None:
            intraday.setdefault(sym, {})['15m'] = klines_to_df(k)
    # also refresh 1h/4h for tracked (triggered) setups so their ANALYSIS
    # section stays current (levels remain frozen after trigger)
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '1h', 400), tracked_syms).values():
        if k is not None:
            intraday.setdefault(sym, {})['1h'] = klines_to_df(k)
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '4h', 400), tracked_syms).values():
        if k is not None:
            intraday.setdefault(sym, {})['4h'] = klines_to_df(k)
    if verbose:
        print(f"[4/6] Intraday klines: {len(intraday)} symbols")

    # ---------- 5. build plans, score, track lifecycle
    cfg_risk = dict(cfg['risk'])
    cfg_risk['min_rr_tp1'] = cfg.get('min_rr_tp1', 1.0)
    cfg_risk['disabled_setups'] = list(cfg.get('strategy', {}).get('disabled_setups', []))
    # market-regime gate: suppress NEW setups when broad-market breadth is weak
    mfilter = cfg.get('market_filter', {})
    gate_min_breadth = float(mfilter.get('min_breadth_pct', 0)) if mfilter.get('enabled', False) else 0.0
    market_early = _market_status(daily, meta_by_sym)  # same inputs as step 6 -> identical result
    gate_active = gate_min_breadth > 0 and market_early['breadth_pct_above_ema50'] < gate_min_breadth
    if verbose and gate_active:
        print(f"[gate] breadth {market_early['breadth_pct_above_ema50']}% < {gate_min_breadth}% "
              f"-> new setups suppressed this cycle (existing ones still tracked)")
    weights = cfg['scoring']
    fresh = []
    for sym, meta in cand:
        if gate_active:
            break
        frames = intraday.get(sym)
        if not frames or not all(t in frames for t in ('15m', '1h', '4h')):
            continue
        try:
            d15, d1h, d4h = frames['15m'], frames['1h'], frames['4h']
            dd = daily[sym]
            st_kw = dict(st_period=stp['period'], st_mult=stp['multiplier'])
            tf = {'15m': tf_state(enrich(d15, **st_kw), k=2), '1h': tf_state(enrich(d1h, **st_kw), k=2),
                  '4h': tf_state(enrich(d4h, **st_kw), k=3), '1d': tf_state(enrich(dd, **st_kw), k=3)}
            brk = detect_breakout(enrich(d4h), tf['4h'])
            plans = generate_plans(tf, brk, cfg_risk)
            if not cfg.get('strategy', {}).get('allow_shorts', True):
                plans = [p for p in plans if p['direction'] == 'LONG']
            for plan in plans:
                # publish-time freshness guard: never publish a plan whose
                # TP1 is already reached by the LIVE price (stale on arrival)
                plan = _freshness_guard(plan, meta['last'], tf['4h']['atr'])
                if plan is None:
                    continue
                # post-pump cooldown: never chase a coin that pumped hard intraday
                if meta['chg24'] > 12 and plan['status'] == 'READY':
                    plan['status'] = 'WAITING_CONFIRMATION'
                    plan['confirmation'] = 'wait for a pullback into the entry zone that holds before entering'
                score, parts = score_plan(tf, plan, meta, weights)
                if score < cfg['min_score_to_show']:
                    continue
                entry = plan['entry_mid']
                R = abs(entry - plan['stop_loss'])
                if R <= 0:
                    continue
                fresh.append(_build_opportunity(sym, meta, tf, plan, score, parts, R, now_iso))
        except Exception as e:
            errors.append(f"{sym}: {type(e).__name__} {e}")

    # merge: keep lifecycle for triggered trades (levels frozen after trigger);
    # refresh levels/status for setups that never triggered.
    prev = load_json(data_path('opportunities.json'), [])
    before_status = {o['id']: o.get('status') for o in prev}
    k15 = {s: _df_to_klines(f['15m']) for s, f in intraday.items() if '15m' in f}
    closed = track(prev, k15, cfg['expiry_hours'], now_iso)
    transitions = _extract_transitions(before_status, prev)
    # fresh multi-timeframe state for tracked setups (analysis refresh only —
    # entry/SL/TP of triggered trades stay frozen)
    st_kw = dict(st_period=stp['period'], st_mult=stp['multiplier'])
    tracked_tf = {}
    for sym in tracked_syms:
        frames = intraday.get(sym)
        dd = daily.get(sym)
        if frames and all(t in frames for t in ('15m', '1h', '4h')) and dd is not None:
            try:
                tracked_tf[sym] = {
                    '15m': tf_state(enrich(frames['15m'], **st_kw), k=2),
                    '1h': tf_state(enrich(frames['1h'], **st_kw), k=2),
                    '4h': tf_state(enrich(frames['4h'], **st_kw), k=3),
                    '1d': tf_state(enrich(dd, **st_kw), k=3),
                }
            except Exception:
                continue
    FROZEN = ('TRIGGERED', 'TP1_HIT', 'TP2_HIT')
    active_by_key = {}
    for o in prev:
        if o.get('status') in FROZEN:
            o['updated_at'] = now_iso
            tfo = tracked_tf.get(o['symbol'])
            if tfo:
                o['analysis'] = _analysis_dict(tfo)
            cur = next((r for r in rows if r['sym'] == o['symbol']), None)
            if cur:
                o['current_price'] = cur['last']
                o['change_24h'] = cur['chg24']
            # backfill fields introduced in later engine versions
            if o.get('triggered_at') and ' ' in o['triggered_at']:
                o['triggered_at'] = o['triggered_at'].replace(' ', 'T')
            if 'distance_to_tp1_pct' not in o:
                sgn = 1 if o.get('direction') == 'LONG' else -1
                cp = o.get('current_price') or 0
                if cp > 0:
                    o['distance_to_tp1_pct'] = round(sgn * (o['tp1'] - cp) / cp * 100, 2)
            active_by_key[f"{o['symbol']}|{o['direction']}"] = o
            continue
        # terminal opportunities were moved to history by the tracker
        if o.get('status') in TERMINAL:
            continue
    new_ops = _carry_over_fresh(active_by_key, fresh, now_iso, FROZEN)
    merged = list(active_by_key.values())
    # remove opportunities that disappeared from the tradable universe
    merged = [o for o in merged if any(r['sym'] == o['symbol'] for r in rows)]
    merged.sort(key=lambda o: -(o['score'] or 0))
    merged = merged[:cfg['max_opportunities']]
    if verbose:
        print(f"[5/6] Plans: {len(fresh)} fresh | {len(new_ops)} new | {len(closed)} closed | {len(merged)} active displayed")

    # ---------- 6. market status + deterministic quantitative agent + persist
    market = market_early
    market['new_setups_gated'] = gate_active
    if gate_active:
        market['gate_reason'] = f"breadth {market['breadth_pct_above_ema50']}% below min {gate_min_breadth}%"

    # The scalp agent reuses the candles already fetched above. It is a strict,
    # deterministic validator (EMA200 + SuperTrend + price action + R:R), not an
    # LLM and not a second market-data client. A failure here never blocks the
    # primary opportunity scanner.
    try:
        agent_scan = run_quant_agent(cand, intraday, daily, market, cfg, now_iso)
    except Exception as e:
        agent_scan = {
            'schema_version': 'scalp-supertrend-1.0',
            'scan_timestamp': now_iso,
            'source_data_timestamp': now_iso,
            'status': 'error',
            'market': market,
            'total_scanned': len(cand),
            'opportunities_found': 0,
            'signals': [],
            'rejections': [],
            'no_opportunity_reason': {
                'ar': 'تعذر تشغيل الوكيل الكمي في هذه الدورة.',
                'en': 'The quantitative agent could not run in this cycle.',
            },
            'errors': [f"{type(e).__name__}: {e}"],
        }
        errors.append(f"quant_agent: {type(e).__name__} {e}")

    runtime = round(time.time() - t0, 1)
    _record_market_history(market, now_iso, runtime)
    # chart data for displayed symbols
    _write_chart_cache(merged, intraday, daily, now_iso, stp)

    save_json(data_path('opportunities.json'), merged)
    save_json(data_path('agent_scan.json'), agent_scan)
    # history: dedupe by opportunity id (concurrent runs can double-record the
    # same closure), then append this cycle's closures and cap the file
    hist = _dedupe_history(load_json(data_path('history.json'), []), closed)
    save_json(data_path('history.json'), hist)
    save_json(data_path('market.json'), market)
    save_json(data_path('performance.json'), performance_stats(hist))
    # engine config for the in-browser Coin Analyzer (JS mirror must match)
    save_json(data_path('config.json'), {
        'min_score_to_show': cfg['min_score_to_show'],
        'min_rr_tp1': cfg.get('min_rr_tp1', 1.0),
        'allow_shorts': cfg.get('strategy', {}).get('allow_shorts', True),
        'disabled_setups': cfg_risk['disabled_setups'],
        'scoring': cfg['scoring'],
        'risk': cfg['risk'],
        'supertrend': cfg.get('supertrend', {'period': 10, 'multiplier': 3.0}),
        'quant_agent': cfg.get('quant_agent', {}),
        'universe': {'exclude_assets': cfg['universe'].get('exclude_assets', [])},
        'engine_version': cfg.get('engine_version', '1.0.0'),
    })
    # symbol list for the analyzer autocomplete (static; identical rewrites produce no commit)
    _save_symbol_list(einfo, now_iso)
    save_json(data_path('meta.json'), {
        'data_timestamp': now_iso,
        'server_time': iso(),
        'source': bc.source_host(),
        'update_interval_minutes': cfg['update_interval_minutes'],
        'next_update_at': iso(__import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                              + __import__('datetime').timedelta(minutes=cfg['update_interval_minutes'])),
        'market_status': market['status'],
        'engine_version': cfg.get('engine_version', '1.0.0'),
        'config': {
            'min_score_to_show': cfg['min_score_to_show'],
            'max_opportunities': cfg['max_opportunities'],
            'stale_after_minutes': cfg['stale_after_minutes'],
            'expiry_hours': cfg['expiry_hours'],
            'allow_shorts': cfg.get('strategy', {}).get('allow_shorts', True),
            'market_filter': {'enabled': bool(mfilter.get('enabled', False)),
                              'min_breadth_pct': gate_min_breadth,
                              'gated_this_cycle': gate_active},
            'quant_agent': {
                'enabled': bool(cfg.get('quant_agent', {}).get('enabled', True)),
                'signals': agent_scan.get('opportunities_found', 0),
                'status': agent_scan.get('status', 'error'),
            },
        },
        'errors': errors[-10:],
        'runtime_seconds': runtime,
    })
    if verbose:
        print(f"[6/6] Saved. Source: {bc.source_host()} | runtime {time.time()-t0:.1f}s")
    return merged, market, {'new': new_ops, 'transitions': transitions}


def _dedupe_history(hist, closed):
    """Keep the earliest record per opportunity id, then append new closures."""
    seen = set()
    deduped = []
    for rec in hist:
        rid = rec.get('id')
        if rid in seen:
            continue
        seen.add(rid)
        deduped.append(rec)
    return (deduped + [c for c in closed if c.get('id') not in seen])[-500:]


def _extract_transitions(before_status, opps):
    """Diff statuses before/after the lifecycle tracker — lifecycle events only."""
    out = []
    for o in opps:
        b = before_status.get(o['id'])
        if b is not None and b != o.get('status'):
            out.append({'opp': o, 'from': b, 'to': o['status']})
    return out


def _freshness_guard(plan, live_price, atr):
    """Suppress plans whose first target is already reached by the live price,
    and downgrade READY plans that moved beyond the entry zone before publication.

    Returns the (possibly adjusted) plan or None when it must not be published.
    """
    if not plan or not live_price or live_price <= 0 or not atr or atr <= 0:
        return plan
    tp1 = plan['tp1']
    if plan['direction'] == 'LONG':
        if live_price >= tp1:
            return None  # TP1 already reached — publishing it would be misleading
        if plan['status'] == 'READY' and live_price > plan['entry_zone'][1] + 1.2 * atr:
            plan['status'] = 'WAITING_CONFIRMATION'
            plan['confirmation'] = 'price moved beyond the entry zone before publication — wait for a retest'
    else:
        if live_price <= tp1:
            return None
        if plan['status'] == 'READY' and live_price < plan['entry_zone'][0] - 1.2 * atr:
            plan['status'] = 'WAITING_CONFIRMATION'
            plan['confirmation'] = 'price moved beyond the entry zone before publication — wait for a retest'
    return plan


def _carry_over_fresh(active_by_key, fresh, now_iso, frozen):
    """Merge freshly computed plans into the active map.

    Frozen trades (TRIGGERED/TP hits) are untouchable — fresh plans for the same
    symbol+direction are dropped so entry/SL/TP stay locked after the trigger.
    Non-frozen setups are refreshed with latest data while keeping their
    identity and lifecycle history. Returns the list of brand-new opportunities.
    """
    new_ops = []
    for op in fresh:
        key = f"{op['symbol']}|{op['direction']}"
        old = active_by_key.get(key)
        if old is not None and old.get('status') in frozen:
            continue  # frozen trade — fresh plans must not touch it
        if old is not None:
            # carry over identity/lifecycle, refresh the plan with fresh data
            op['id'] = old['id']
            op['created_at'] = old['created_at']
            op['events'] = list(old.get('events', []))
            if old.get('status') == 'READY' and op['status'] == 'WAITING_CONFIRMATION':
                op['status'] = 'READY'
            op['events'].append({'at': now_iso, 'from': old.get('status'), 'to': op['status'],
                                 'note': 'plan refreshed with latest data'})
        else:
            new_ops.append(op)
        active_by_key[key] = op
    return new_ops


def _save_symbol_list(einfo, now_iso):
    """Compact symbol list for the analyzer autocomplete (USDT pairs first)."""
    try:
        rows = []
        for s in einfo['symbols']:
            if s['status'] != 'TRADING' or not s.get('isSpotTradingAllowed'):
                continue
            rows.append({'s': s['symbol'], 'b': s['baseAsset'], 'q': s['quoteAsset']})
        rows.sort(key=lambda r: (r['q'] != 'USDT', r['q'] != 'BTC', r['b']))
        save_json(data_path('symbols.json'), {'updated_at': now_iso, 'symbols': rows[:3000]})
    except Exception:
        pass


def _analysis_dict(tf):
    """Per-timeframe analysis snapshot for the dashboard (deterministic values)."""
    analyses = {}
    for tf_name in TFS:
        st = tf[tf_name]
        analyses[tf_name] = {
            'trend': 'Bullish' if st['above20'] and st['e20_gt_e50'] else ('Bearish' if not st['above20'] and not st['e20_gt_e50'] else 'Mixed'),
            'rsi': round(st['rsi'], 1),
            'macd': 'Positive' if st['macd_h'] >= 0 else 'Negative',
            'ema20': round(st['ema20'], 8), 'ema50': round(st['ema50'], 8),
            'ema200': round(st['ema200'], 8), 'vwap': round(st['vwap'], 8),
            'above_ema20': st['above20'], 'above_ema50': st['above50'],
            'atr_pct': round(st['atr'] / st['close'] * 100, 2),
            'vol_ratio': round(st['vol_ratio3'], 2),
            'supertrend': 'UP' if st['st_dir'] > 0 else 'DOWN',
            'supertrend_value': round(st['st_line'], 8),
        }
    return analyses


def _build_opportunity(sym, meta, tf, plan, score, parts, R, now_iso):
    entry = plan['entry_mid']
    dirn = plan['direction']
    rr1 = round(abs(plan['tp1'] - entry) / R, 2)
    rr2 = round(abs(plan['tp2'] - entry) / R, 2)
    rr3 = round(abs(plan['tp3'] - entry) / R, 2)
    sl_pct = round(abs(entry - plan['stop_loss']) / entry * 100, 2)
    sgn = 1 if dirn == 'LONG' else -1
    analyses = _analysis_dict(tf)
    breakdown = {k: v for k, v in parts.items()}
    return {
        'id': f"{sym}_{dirn}_{plan['setup_type']}_{now_iso[:10]}_{entry:.6f}",
        'symbol': sym, 'pair': sym.replace('USDT', '/USDT'),
        'direction': dirn,
        'setup_type': plan['setup_type'],
        'setup_label': plan['setup_label'],
        'status': plan['status'],
        'entry_mid': round(entry, 8),
        'entry_zone': plan['entry_zone'],
        'stop_loss': plan['stop_loss'],
        'tp1': plan['tp1'], 'tp2': plan['tp2'], 'tp3': plan['tp3'],
        'rr_tp1': rr1, 'rr_tp2': rr2, 'rr_tp3': rr3,
        'sl_distance_pct': sl_pct,
        'profit_pct_tp1': round(sgn * (plan['tp1'] - entry) / entry * 100, 2),
        'profit_pct_tp2': round(sgn * (plan['tp2'] - entry) / entry * 100, 2),
        'profit_pct_tp3': round(sgn * (plan['tp3'] - entry) / entry * 100, 2),
        'score': score, 'grade': grade(score),
        'score_breakdown': breakdown,
        'score_breakdown_labels': COMPONENT_LABELS,
        'primary_timeframe': plan['primary_timeframe'],
        'timeframes': TFS,
        'analysis': analyses,
        'supports': plan['supports'], 'resistances': plan['resistances'],
        'confluences': plan['confluences'],
        'invalidation_level': plan['invalidation_level'],
        'confirmation': plan['confirmation'],
        'reason': reason_entry(tf, plan, sym),
        'sl_reason': reason_sl(plan, tf),
        'tp_reason': reason_tp(plan, tf),
        'invalidation_reason': reason_invalidation(plan),
        'volume_note': volume_note(tf, meta),
        'momentum_note': momentum_note(tf, dirn),
        'current_price': meta['last'],
        'change_24h': meta['chg24'],
        'quote_volume_24h': meta['quoteVol'],
        'spread_pct': meta['spread'],
        # freshness transparency: how far the live price was from TP1 at publish
        'distance_to_tp1_pct': round(sgn * (plan['tp1'] - meta['last']) / meta['last'] * 100, 2),
        'created_at': now_iso,
        'updated_at': now_iso,
        'data_timestamp': now_iso,
        'events': [{'at': now_iso, 'from': None, 'to': plan['status']}],
    }


def _df_to_klines(df):
    return [[int(t.timestamp() * 1000), o, h, l, c, v] for t, o, h, l, c, v in
            zip(df['t'], df['o'], df['h'], df['l'], df['c'], df['v'])]


def _write_chart_cache(ops, intraday, daily, now_iso, stp):
    kdir = os.path.join(data_path('klines'), '')
    os.makedirs(kdir, exist_ok=True)
    wanted = set()
    for o in ops:
        sym = o['symbol']
        wanted.add(f"{sym}_4h")
        wanted.add(f"{sym}_1h")
        for tf_name, src in (('4h', intraday.get(sym, {}).get('4h')), ('1h', intraday.get(sym, {}).get('1h'))):
            if src is None:
                continue
            e = enrich(src, st_period=stp['period'], st_mult=stp['multiplier'])
            n = 160
            tail = e.tail(n)
            payload = {
                'symbol': sym, 'timeframe': tf_name,
                'updated_at': now_iso,
                'candles': [[int(t.timestamp() * 1000), round(o, 10), round(h, 10), round(l, 10), round(c, 10), round(v, 2)]
                            for t, o, h, l, c, v in zip(tail['t'], tail['o'], tail['h'], tail['l'], tail['c'], tail['v'])],
                'ema20': [round(x, 10) for x in tail['ema20'].tolist()],
                'ema50': [round(x, 10) for x in tail['ema50'].tolist()],
                'vwap': [round(x, 10) for x in tail['vwap'].fillna(0).tolist()],
                'st_line': [round(x, 10) for x in tail['st_line'].fillna(0).tolist()],
                'st_dir': [int(x) for x in tail['st_dir'].tolist()],
            }
            save_json(os.path.join(kdir, f"{sym}_{tf_name}.json"), payload)
    # prune stale chart cache
    for fn in os.listdir(kdir):
        if fn.endswith('.json') and fn not in {w + '.json' for w in wanted}:
            try:
                os.remove(os.path.join(kdir, fn))
            except OSError:
                pass


def _market_status(daily, meta_by_sym):
    top = sorted(meta_by_sym.items(), key=lambda kv: -kv[1]['quoteVol'])[:30]
    above = 0
    counted = 0
    for sym, _ in top:
        df = daily.get(sym)
        if df is None:
            continue
        counted += 1
        if df['c'].iloc[-1] > df['ema50'].iloc[-1]:
            above += 1
    breadth = above / counted * 100 if counted else 50.0
    status = 'BULLISH' if breadth >= 60 else ('BEARISH' if breadth <= 35 else 'NEUTRAL')
    btc = None
    if 'BTCUSDT' in meta_by_sym:
        m = meta_by_sym['BTCUSDT']
        btc = {'price': m['last'], 'change_24h': m['chg24'], 'quote_volume_24h': m['quoteVol']}
    return {
        'status': status,
        'breadth_pct_above_ema50': round(breadth, 1),
        'coins_analyzed': counted,
        'btc': btc,
        'top_quote_volume_24h': round(sum(v['quoteVol'] for _, v in top) / 1e6, 1),
    }


def _record_market_history(market, now_iso, runtime):
    """Append this cycle's breadth snapshot + a pipeline update-log entry.
    Both files are capped so the repo stays small; the dashboard uses them
    for the Market tab (breadth chart, BTC line, pipeline health)."""
    try:
        bh = load_json(data_path('breadth_history.json'), [])
        if not bh or bh[-1].get('t') != now_iso:
            bh.append({
                't': now_iso,
                'breadth': market['breadth_pct_above_ema50'],
                'status': market['status'],
                'btc': (market.get('btc') or {}).get('price'),
            })
        save_json(data_path('breadth_history.json'), bh[-1000:])

        ul = load_json(data_path('update_log.json'), [])
        if not ul or ul[-1].get('t') != now_iso:
            ul.append({'t': now_iso, 'ok': True, 'duration': runtime})
        save_json(data_path('update_log.json'), ul[-120:])
    except Exception:
        pass  # history recording must never break the analysis cycle
