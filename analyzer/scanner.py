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
from datetime import datetime, timezone

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


def _price_precision(sym, sym_map=None):
    """Return the number of decimal places for a symbol's price (from Binance exchangeInfo).
    Defaults to 8 if unknown."""
    if sym_map is None:
        try:
            sym_map = {s['s']: s for s in load_json(data_path('symbols.json'), {}).get('symbols', [])}
        except Exception:
            sym_map = {}
    return sym_map.get(sym, {}).get('p', 8)


def _round_price(price, precision):
    """Round a price to the given number of decimal places."""
    if price is None:
        return None
    return round(price, precision)
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


def _select_universe(rows, max_symbols):
    """Return all qualified rows when max_symbols is 0/None, else cap them.

    The primary swing scanner can still apply its own deep-candidate limit;
    this universe is also the complete input set for the quantitative agent.
    """
    limit = int(max_symbols or 0)
    return list(rows) if limit <= 0 else list(rows[:limit])


def btc_regime_bullish(df):
    """BTC daily regime used as a gate for NEW long setups on altcoins:
    bullish only while BTC closes above its own daily EMA200. Altcoins lose
    far more often when the market leader itself is in a downtrend."""
    st = tf_state(df, k=3)
    return bool(st['close'] > st['ema200'])


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
    universe = _select_universe(rows, uni.get('max_symbols_to_screen', 0))
    agent_universe = [(r['sym'], r) for r in universe]
    if verbose:
        scope = 'all qualified' if not uni.get('max_symbols_to_screen') else f"top {len(universe)}"
        print(f"[1/6] Universe: {len(rows)} liquid symbols -> screening {scope}")

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

    # ---------- 2b. BTC daily regime gate (fail-open when data is missing)
    btc_cfg = cfg.get('btc_filter', {})
    btc_info = {'enabled': bool(btc_cfg.get('enabled', False)), 'bullish': True}
    if btc_info['enabled']:
        btc_df = daily.get('BTCUSDT')
        if btc_df is None or len(btc_df) < 200:
            try:
                k = _get_klines('BTCUSDT', '1d', 400)
                if k:
                    btc_df = enrich(klines_to_df(k), st_period=stp['period'], st_mult=stp['multiplier'])
            except Exception:
                btc_df = None
        if btc_df is not None and len(btc_df) >= 200:
            try:
                btc_info['bullish'] = btc_regime_bullish(btc_df)
                btc_info['close'] = round(float(btc_df['c'].iloc[-1]), 2)
                btc_info['ema200'] = round(float(btc_df['ema200'].iloc[-1]), 2)
            except Exception:
                btc_info['bullish'] = True  # fail-open on compute errors
        if verbose and not btc_info['bullish']:
            print(f"[btc-gate] BTC {btc_info.get('close')} below daily EMA200 "
                  f"{btc_info.get('ema200')} -> new long setups suppressed this cycle")

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

    # ---------- 4. intraday klines for all qualified pairs (+ tracked symbols)
    tracked_prev = load_json(data_path('opportunities.json'), [])
    tracked_syms = [o['symbol'] for o in tracked_prev if o.get('status') not in TERMINAL]
    # The quantitative agent evaluates every qualified USDT pair. Fetch each
    # execution timeframe once for the full universe; the primary scanner then
    # reuses the same frames for its smaller daily-screened candidate set.
    need_syms = [r['sym'] for r in universe]
    need_set = set(need_syms)
    tracked_only = [s for s in tracked_syms if s not in need_set]
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
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '15m', 500), tracked_only).values():
        if k is not None:
            intraday.setdefault(sym, {})['15m'] = klines_to_df(k)
    # also refresh 1h/4h for tracked (triggered) setups so their ANALYSIS
    # section stays current (levels remain frozen after trigger)
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '1h', 400), tracked_only).values():
        if k is not None:
            intraday.setdefault(sym, {})['1h'] = klines_to_df(k)
    for sym, tf, k in _fetch_many(lambda s: _get_klines(s, '4h', 400), tracked_only).values():
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
    btc_blocked = btc_info['enabled'] and not btc_info['bullish']
    if verbose and gate_active:
        print(f"[gate] breadth {market_early['breadth_pct_above_ema50']}% < {gate_min_breadth}% "
              f"-> new setups suppressed this cycle (existing ones still tracked)")
    weights = cfg['scoring']
    fresh = []
    for sym, meta in cand:
        if gate_active or btc_blocked:
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
            brk = detect_breakout(enrich(d4h), tf['4h'],
                                  vol_min=cfg_risk.get('breakout_vol_ratio', 1.5),
                                  close_pos_min=cfg_risk.get('breakout_close_position_min', 0.0))
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
    market['new_setups_gated'] = gate_active or btc_blocked
    if gate_active:
        market['gate_reason'] = f"breadth {market['breadth_pct_above_ema50']}% below min {gate_min_breadth}%"
    elif btc_blocked:
        market['gate_reason'] = (f"BTC {btc_info.get('close')} below daily EMA200 "
                                 f"{btc_info.get('ema200')} (btc_filter)")
    market['btc_filter'] = btc_info
    # daily performance heatmap: top coins by quote volume, colored grid of 24h
    # % change. Reuses the meta captured during universe screening (quoteVol +
    # chg24 settle (or changePercentFallback) without any extra API calls).
    heat_rows = []
    for sym, meta in sorted(meta_by_sym.items(), key=lambda kv: -kv[1].get('quoteVol', 0))[:40]:
        chg = meta.get('chg24')
        if chg is None:
            for c in meta.get('changePercentFallback', []) or []:
                if isinstance(c, dict) and c.get('value') is not None:
                    chg = c['value']
                    break
        if chg is None or meta.get('quoteVol', 0) < cfg['universe'].get('min_quote_volume_24h', 0):
            continue
        heat_rows.append({'s': sym.replace('USDT', ''), 'c24': round(float(chg), 2),
                          'vol': round(meta.get('quoteVol', 0) / 1e6, 1)})
    market['heatmap'] = heat_rows[:36]
    st_hourly = {
        sym: enrich(f['1h'].copy(), st_period=stp['period'], st_mult=stp['multiplier'])
        for sym, f in intraday.items() if '1h' in f
    }
    # max_signal_age_days is configured in days; on the hourly board bars_held
    # counts hours, so convert days -> hours before filtering.
    max_st_age_hours = int(stp.get('max_signal_age_days', 30)) * 24
    st_board = _build_st_signals(st_hourly, meta_by_sym, now_iso,
                                 max_age=max_st_age_hours)
    if verbose:
        print(f"[ST] supertrend daily BUY signals: {st_board['count']}")

    # The scalp agent reuses the candles already fetched above. It is a strict,
    # deterministic validator (EMA200 + SuperTrend + price action + R:R), not an
    # LLM and not a second market-data client. A failure here never blocks the
    # primary opportunity scanner.
    try:
        agent_scan = run_quant_agent(agent_universe, intraday, daily, market, cfg, now_iso)
    except Exception as e:
        agent_scan = {
            'schema_version': 'scalp-supertrend-1.1',
            'scan_timestamp': now_iso,
            'source_data_timestamp': now_iso,
            'status': 'error',
            'market': market,
            'timeframes_scanned': ['15m', '1h', '4h'],
            'symbols_scanned': len(agent_universe),
            'total_scanned': len(agent_universe) * 3,
            'opportunities_found': 0,
            'opportunities_by_timeframe': {'15m': 0, '1h': 0, '4h': 0},
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
    _record_agent_history(agent_scan, now_iso)
    # history: dedupe by opportunity id (concurrent runs can double-record the
    # same closure), then append this cycle's closures and cap the file
    hist = _dedupe_history(load_json(data_path('history.json'), []), closed)
    save_json(data_path('history.json'), hist)
    save_json(data_path('market.json'), market)
    save_json(data_path('st_signals.json'), st_board)
    save_json(data_path('fear_greed.json'), _fetch_fear_greed(now_iso))
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
            'btc_filter': {'enabled': btc_info['enabled'],
                           'bullish': btc_info['bullish'],
                           'gated_this_cycle': btc_blocked},
            'quant_agent': {
                'enabled': bool(cfg.get('quant_agent', {}).get('enabled', True)),
                'timeframes': agent_scan.get('timeframes_scanned', ['15m', '1h', '4h']),
                'signals': agent_scan.get('opportunities_found', 0),
                'signals_by_timeframe': agent_scan.get('opportunities_by_timeframe', {}),
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
            rows.append({'s': s['symbol'], 'b': s['baseAsset'], 'q': s['quoteAsset'],
                         'p': s.get('pricePrecision', 8)})
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


def _closed_tf(df, period_seconds=3600):
    """Drop the still-open candle if present (Binance returns the in-progress
    candle as the last row). The SuperTrend board must reflect CLOSED candles
    only, otherwise signals can appear and disappear before the candle
    confirms. `period_seconds` is the candle length (3600 = 1h, 86400 = 1d).
    """
    if df is None or len(df) < 2:
        return df
    last_open = df['t'].iloc[-1].to_pydatetime()
    if (datetime.now(timezone.utc) - last_open).total_seconds() < period_seconds:
        return df.iloc[:-1]
    return df


def _st_run_info(df):
    """Info about the current SuperTrend UP-run of the given frame, or
    None if not UP. The caller passes closed candles only (see _closed_tf),
    so the result is deterministic within the candle."""
    sd = [int(x) if x == x else 0 for x in df['st_dir'].tolist()]
    if not sd or sd[-1] != 1:
        return None
    i = len(sd) - 1
    while i > 0 and sd[i - 1] == 1:
        i -= 1
    return {
        'bars_held': len(sd) - i,
        'signal_at': df['t'].iloc[i].isoformat(),
        'price_at_signal': round(float(df['c'].iloc[i]), 8),
    }


def _support_flags(df):
    """Score the bullish case for the current candle with 4 context flags.

    Each returns one of 'ok' / 'warn' / 'no' so the frontend can render a
    coloured marker (✅ / ⚠️ / ❌). Flags (for a LONG entry on the 1h board):
      - trend:   price above EMA50 AND EMA20>EMA50 = strong uptrend
      - volume:  vol_ratio relative to its 1h average
      - timing:  RSI in a comfortable re-trace / early momentum zone
      - momentum: MACD above its signal line (ideally above zero)
    """
    def _n(key):
        try:
            v = float(df[key].iloc[-1])
        except Exception:
            return None
        return v if v == v else None

    c = _n('c')
    e20 = _n('ema20')
    e50 = _n('ema50')
    vr = _n('vol_ratio')
    rsi = _n('rsi')
    macd = _n('macd')
    macd_s = _n('macd_s')

    trend = 'ok'
    if c is None or e20 is None or e50 is None:
        trend = 'no'
    elif c > e50 and e20 > e50:
        trend = 'ok'
    elif c > e50 or e20 > e50:
        trend = 'warn'
    else:
        trend = 'no'

    if vr is None:
        volume = 'no'
    elif vr >= 2.0:
        volume = 'ok'
    elif vr >= 1.0:
        volume = 'warn'
    else:
        volume = 'no'

    if rsi is None:
        timing = 'no'
    elif 40 <= rsi <= 60:
        timing = 'ok'
    elif 60 < rsi <= 70 or 35 <= rsi < 40:
        timing = 'warn'
    else:
        timing = 'no'

    if macd is None or macd_s is None:
        momentum = 'no'
    elif macd > macd_s and macd > 0:
        momentum = 'ok'
    elif macd > macd_s:
        momentum = 'warn'
    else:
        momentum = 'no'

    return {
        'trend': trend,
        'volume': volume,
        'timing': timing,
        'momentum': momentum,
    }


def _build_st_signals(frames, meta_by_sym, now_iso, cap=120, max_age=None,
                      timeframe='1h', period_seconds=3600, min_bars=200):
    """SuperTrend board: every screened symbol whose SuperTrend on `timeframe`
    (default 1h) is currently bullish. Listed from signal start until it flips
    to SELL. Recomputed deterministically each cycle — no extra state file.

    Asymmetric policy (fast exits, confirmed entries):
      * ENTRIES need the candle CLOSED (see _closed_tf) — no phantom signals
        from intraday flips.
      * EXITS are immediate: if the still-open candle has already flipped
        SuperTrend DOWN, the symbol is removed from the board right away,
        until a new confirmed signal appears.
    """
    signals = []
    try:
        sym_map = {s['s']: s for s in load_json(data_path('symbols.json'), {}).get('symbols', [])}
    except Exception:
        sym_map = {}
    for sym, df in frames.items():
        try:
            live_dir = df['st_dir'].iloc[-1] if len(df) else None
            df = _closed_tf(df, period_seconds)
            if len(df) < min_bars:
                continue
            info = _st_run_info(df)
            if info is None:
                continue
            # drop signals older than the entry window (max_age in the same
            # candle units as bars_held): an aged trend that has run for many
            # candles is no longer a fresh entry opportunity
            if max_age is not None and info['bars_held'] > int(max_age):
                continue
            # immediate removal: flip DOWN on the open candle
            if live_dir == live_dir and live_dir is not None and int(live_dir) == -1:
                continue
            c_now = float(df['c'].iloc[-1])
            e50 = float(df['ema50'].iloc[-1])
            rsi_v = float(df['rsi'].iloc[-1])
            atr_v = float(df['atr'].iloc[-1]) if 'atr' in df.columns else None
            st_line = float(df['st_line'].iloc[-1]) if 'st_line' in df.columns else None
            sig_p = info['price_at_signal']
            if not (c_now == c_now) or not (sig_p == sig_p) or sig_p <= 0:
                continue
            m = meta_by_sym.get(sym)
            cur = m['last'] if m else c_now
            # SL = SuperTrend line (natural ATR-based stop); R = distance from entry to SL
            sl = st_line if st_line and st_line < cur else (cur - atr_v * 2 if atr_v else None)
            R = abs(cur - sl) if sl and sl > 0 else None
            pp = _price_precision(sym, sym_map)
            tp1 = _round_price(cur + R * 1.5, pp) if R else None
            tp2 = _round_price(cur + R * 2.5, pp) if R else None
            tp3 = _round_price(cur + R * 4.0, pp) if R else None
            sl = _round_price(sl, pp) if sl else None
            sig_p = _round_price(sig_p, pp)
            cur = _round_price(cur, pp)
            signals.append({
                'symbol': sym,
                'pair': sym.replace('USDT', '/USDT'),
                'signal_at': info['signal_at'],
                'bars_held': info['bars_held'],
                'price_at_signal': sig_p,
                'current_price': cur,
                'change_pct': round((cur - sig_p) / sig_p * 100, 2),
                'rsi': round(rsi_v, 1) if rsi_v == rsi_v else None,
                'above_ema50': bool(c_now > e50),
                'support': _support_flags(df),
                'stop_loss': round(sl, 8) if sl else None,
                'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
                'rr_tp1': round((tp1 - cur) / R, 2) if R and tp1 else None,
                'rr_tp2': round((tp2 - cur) / R, 2) if R and tp2 else None,
            })
        except Exception:
            continue
    signals.sort(key=lambda s: s['signal_at'], reverse=True)
    return {
        'updated_at': now_iso,
        'timeframe': timeframe,
        'count': len(signals),
        'signals': signals[:cap],
    }


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


def _fetch_fear_greed(now_iso):
    """Fetch the public Crypto Fear & Greed Index (5 min delay, best-effort).
    Returns a small JSON payload or None; never throws. Used on the Market
    tab as a broad sentiment signal alongside the internal breadth/regime."""
    try:
        import urllib.request
        url = 'https://api.alternative.me/fng/'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode('utf-8'))
        row = (data.get('data') or [{}])[0]
        if not row or not row.get('value'):
            return None
        value = int(row['value'])
        label = row.get('value_classification', 'Neutral')
        return {
            'value': value,
            'label': label,
            'updated_at': now_iso,
        }
    except Exception:
        return None


def _pick_universe_rows(rows, uni, verbose):
    return rows


def _record_agent_history(agent_scan, now_iso):
    """Keep a rolling log of the quant agent's signals so disappearing
    recommendations leave a trace. Each (symbol, timeframe) has a single live
    row that is updated while the signal persists and stamped `ended_at` when
    it leaves the scan. Capped so the repo stays small."""
    try:
        hist = load_json(data_path('agent_history.json'), [])
        signals = agent_scan.get('signals') or []
        day = now_iso[:10]

        active = set()
        for s in signals:
            bar = s.get('bars') or []
            b = bar[-1] if bar else None
            key = (s.get('symbol', ''), s.get('timeframe', ''))
            if not key[0] or not key[1]:
                continue
            active.add(key)
            rec = {
                'ts': now_iso,
                'symbol': s.get('symbol'),
                'tf': s.get('timeframe'),
                'score': s.get('score'),
                'price': b.get('close') if b else None,
                'ended_at': None,
            }
            # refresh the open row for this pair, else start a new one
            existing = next((r for r in hist
                             if r.get('symbol') == key[0] and r.get('tf') == key[1]
                             and not r.get('ended_at')), None)
            if existing:
                existing.update({k: v for k, v in rec.items() if k != 'ended_at'})
            else:
                hist.append(rec)

        # any open row from an earlier cycle that is no longer active gets ended
        for r in hist:
            if not r.get('ended_at') and (r.get('symbol'), r.get('tf')) not in active:
                r['ended_at'] = now_iso

        save_json(data_path('agent_history.json'), hist[-2000:])
    except Exception:
        pass  # history recording never breaks the cycle


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
