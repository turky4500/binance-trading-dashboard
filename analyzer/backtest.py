# -*- coding: utf-8 -*-
"""
Historical simulation (backtest) of the SAME deterministic rules used live.

Run daily by GitHub Actions (backtest.yml) or manually:
    python -m analyzer.backtest

Results feed the Performance page and the score-calibration report, so the
dashboard has meaningful statistics from day one instead of waiting weeks.

Honest approximations (documented in the UI too):
  * Trade resolution is evaluated on 4H candles (the live tracker uses 15m).
  * Liquidity/volume scoring uses today's 24h metrics as a historical proxy.
  * Only LONG plans are simulated (shorts disabled by configuration).

These are historical simulations — NOT a promise of future performance.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from . import binance_client as bc
from .indicators import klines_to_df, enrich
from .signal import tf_state, detect_breakout, generate_plans
from .scoring import score_plan
from .storage import load_json, save_json, data_path, iso
from .tracker import performance_stats
from .run import load_config

INTERVAL_SEC = {'1h': 3600, '4h': 14400, '1d': 86400}
EXPIRY_BARS_4H = 12  # ~48 hours, mirrors expiry_hours live

SKIP_BASE = {
    'USDC', 'FDUSD', 'TUSD', 'USDP', 'DAI', 'AEUR', 'BUSD', 'PAXG', 'XAUT', 'PYUSD',
    'XUSD', 'BFUSD', 'EURI', 'RLUSD', 'USDE', 'USD1', 'USDR', 'USDX', 'U', 'WBTC', 'BTCB',
    'CBBTC', 'LBTC', 'WETH', 'WBETH', 'WBNB', 'STETH', 'LSTETH', 'EUR', 'USTC',
}


def _paged_klines(symbol, interval, bars_needed, max_pages=14):
    """Fetch `bars_needed` klines going backwards from now (Binance caps 1000/request)."""
    out = {}
    end = None
    for _ in range(max_pages):
        try:
            k = bc.klines(symbol, interval, 1000, end_time=end)
        except Exception:
            break
        if not k:
            break
        for row in k:
            out[row[0]] = row
        if len(k) < 1000:
            break
        end = int(k[0][0]) - 1
        if len(out) >= bars_needed:
            break
    return [out[t] for t in sorted(out)]


def simulate_lifecycle(d4_tail, plan, expiry_bars=EXPIRY_BARS_4H):
    """Evaluate a LONG plan on the 4H bars AFTER the signal bar.

    Returns (final_status, end_bar_index).
    Progressive targets; stop checked first (conservative, same as live tracker).
    """
    zone_lo, zone_hi = plan['entry_zone']
    sl, tp1, tp2, tp3 = plan['stop_loss'], plan['tp1'], plan['tp2'], plan['tp3']
    inv = plan['invalidation_level']
    triggered_idx = None
    best_tp = 0
    for idx in range(len(d4_tail)):
        row = d4_tail.iloc[idx]
        lo, hi, cl = float(row['l']), float(row['h']), float(row['c'])
        if triggered_idx is None:
            if cl < inv:
                return 'INVALIDATED', idx
            if lo <= zone_hi:
                triggered_idx = idx
            elif idx >= expiry_bars:
                return 'EXPIRED', idx
        else:
            if lo <= sl:
                return 'STOPPED', idx
            if hi >= tp3:
                return 'TP3_HIT', idx
            if hi >= tp2:
                best_tp = max(best_tp, 2)
            elif hi >= tp1:
                best_tp = max(best_tp, 1)
    if triggered_idx is None:
        return 'EXPIRED', max(0, len(d4_tail) - 1)
    return {0: 'TRIGGERED', 1: 'TP1_HIT', 2: 'TP2_HIT'}[best_tp], max(0, len(d4_tail) - 1)


def _search_index(ts_arr, target_ns):
    return int(np.searchsorted(ts_arr, target_ns, side='right')) - 1


def backtest_symbol(symbol, meta_row, cfg, btc=None, verbose=False):
    months = cfg.get('backtest', {}).get('months', 6)
    bars_1d = int(months * 30.5) + 30
    bars_4h = int(months * 30.5 * 6) + 40
    bars_1h = int(months * 30.5 * 24) + 40

    raw_d = _paged_klines(symbol, '1d', bars_1d, max_pages=2)
    raw_4 = _paged_klines(symbol, '4h', bars_4h)
    raw_1 = _paged_klines(symbol, '1h', bars_1h)
    if len(raw_d) < 200 or len(raw_4) < 200 or len(raw_1) < 400:
        return []

    st_kw = dict(st_period=cfg.get('supertrend', {}).get('period', 10),
                  st_mult=cfg.get('supertrend', {}).get('multiplier', 3.0))
    ed = enrich(klines_to_df(raw_d), **st_kw)
    e4 = enrich(klines_to_df(raw_4), **st_kw)
    e1 = enrich(klines_to_df(raw_1), **st_kw)

    risk = dict(cfg['risk'])
    risk['min_rr_tp1'] = cfg.get('min_rr_tp1', 1.0)
    risk['disabled_setups'] = list(cfg.get('strategy', {}).get('disabled_setups', []))
    weights = cfg['scoring']
    min_score = cfg.get('min_score_to_show', 70)

    t4_ns = e4['t'].values.astype('int64')
    t1_ns = e1['t'].values.astype('int64')
    td_ns = ed['t'].values.astype('int64')

    # BTC daily regime gate: mirrors the live scanner (fail-open when missing).
    btc_on = cfg.get('btc_filter', {}).get('enabled', False) and btc is not None
    btc_ns = btc['t'].values.astype('int64') if btc_on else None
    btc_bull = (btc['c'] > btc['ema200']).values if btc_on else None

    records = []
    i = 160  # warmup: base window + breakout lookback
    n = len(e4)
    while i < n - 4:
        try:
            t4 = tf_state(e4.iloc[:i + 1], k=3)
        except Exception:
            i += 1
            continue
        j1 = _search_index(t1_ns, t4_ns[i])
        jd = _search_index(td_ns, t4_ns[i])
        if j1 < 120 or jd < 60:
            i += 1
            continue
        if btc_on:
            jb = _search_index(btc_ns, t4_ns[i])
            if jb < 0 or not btc_bull[jb]:
                i += 1
                continue
        try:
            t1 = tf_state(e1.iloc[:j1 + 1], k=2)
            td = tf_state(ed.iloc[:jd + 1], k=3)
            tf = {'1h': t1, '4h': t4, '1d': td}
            brk = detect_breakout(e4.iloc[:i + 1], None,
                                  vol_min=risk.get('breakout_vol_ratio', 1.5),
                                  close_pos_min=risk.get('breakout_close_position_min', 0.0))
            plans = generate_plans(tf, brk, risk)
            plans = [p for p in plans if p['direction'] == 'LONG']
            if not plans:
                i += 1
                continue
            plan = plans[0]
            score, parts = score_plan(tf, plan, meta_row, weights)
            if score < min_score:
                i += 1
                continue
            status, end_idx = simulate_lifecycle(e4.iloc[i + 1:], plan)
            signal_ts = e4['t'].iloc[i]
            end_ts = e4['t'].iloc[min(i + 1 + end_idx, n - 1)]
            entry = plan['entry_mid']
            R = abs(entry - plan['stop_loss'])
            rr2 = round(abs(plan['tp2'] - entry) / R, 2) if R > 0 else 0
            records.append({
                'symbol': symbol, 'pair': symbol.replace('USDT', '/USDT'),
                'direction': 'LONG', 'setup_type': plan['setup_type'],
                'setup_label': plan['setup_label'],
                'entry_mid': round(entry, 8), 'stop_loss': plan['stop_loss'],
                'tp1': plan['tp1'], 'tp2': plan['tp2'], 'tp3': plan['tp3'],
                'rr_tp1': round(abs(plan['tp1'] - entry) / R, 2) if R > 0 else 0,
                'rr_tp2': rr2,
                'score': score, 'grade': 'BACKTEST',
                'final_status': status,
                'result': 'WIN' if status in ('TP1_HIT', 'TP2_HIT', 'TP3_HIT') else
                          ('LOSS' if status == 'STOPPED' else status),
                'signal_at': signal_ts.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'closed_at': end_ts.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'hold_hours': round((end_ts - signal_ts).total_seconds() / 3600, 1),
                'backtest': True,
            })
            i += max(1, end_idx) + 2  # no overlapping signals
        except Exception:
            i += 1
            continue
    return records


def calibration(records):
    """Win-rate per score band — does a higher score actually win more often?"""
    bands = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 100)]
    rows = []
    for lo, hi in bands:
        sel = [r for r in records if lo <= (r.get('score') or 0) <= hi]
        decided = [r for r in sel if r.get('result') in ('WIN', 'LOSS')]
        wins = sum(1 for r in decided if r['result'] == 'WIN')
        tp1 = sum(1 for r in sel if r['final_status'] in ('TP1_HIT', 'TP2_HIT', 'TP3_HIT'))
        rows.append({
            'band': f'{lo}-{hi}', 'count': len(sel), 'decided': len(decided),
            'win_rate': round(100 * wins / len(decided), 1) if decided else None,
            'tp1_rate': round(100 * tp1 / len(sel), 1) if sel else None,
        })
    return rows


def setup_stats(records):
    """Win rate per setup type."""
    rows = []
    for st in sorted({r['setup_type'] for r in records}):
        sel = [r for r in records if r['setup_type'] == st]
        decided = [r for r in sel if r.get('result') in ('WIN', 'LOSS')]
        wins = sum(1 for r in decided if r['result'] == 'WIN')
        rows.append({
            'setup_type': st, 'label': sel[0]['setup_label'],
            'count': len(sel), 'decided': len(decided),
            'win_rate': round(100 * wins / len(decided), 1) if decided else None,
            'avg_score': round(sum(r['score'] for r in sel) / len(sel), 1) if sel else None,
        })
    return rows


def run(cfg=None, verbose=True):
    cfg = cfg or load_config()
    t0 = time.time()
    months = cfg.get('backtest', {}).get('months', 6)
    top = cfg.get('backtest', {}).get('top_symbols', 12)

    # universe: current top-liquid symbols (approximation for historical liquidity)
    try:
        tickers = bc.ticker_24h()
        books = bc.book_ticker()
    except Exception as e:
        raise RuntimeError(f"Binance snapshot failed: {e}")
    bk = {x['symbol']: x for x in books}
    rows = []
    for x in tickers:
        sym = x['symbol']
        if not sym.endswith('USDT'):
            continue
        base = sym.replace('USDT', '')
        if base in SKIP_BASE or base in set(cfg['universe'].get('exclude_assets', [])):
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
        if qv < cfg['universe']['min_quote_volume_24h'] or spread > cfg['universe']['max_spread_pct']:
            continue
        rows.append({'sym': sym, 'quoteVol': qv, 'spread': spread,
                     'trades': int(x['count']), 'chg24': float(x['priceChangePercent'])})
    rows.sort(key=lambda r: -r['quoteVol'])
    universe = rows[:top]
    if verbose:
        print(f"[backtest] symbols ({months} months): {[u['sym'] for u in universe]}")

    # BTC daily series for the regime gate (same rule as the live scanner)
    btc_df = None
    if cfg.get('btc_filter', {}).get('enabled', False):
        try:
            raw_btc = _paged_klines('BTCUSDT', '1d', int(months * 30.5) + 230, max_pages=2)
            if len(raw_btc) >= 200:
                btc_df = enrich(klines_to_df(raw_btc),
                                st_period=cfg.get('supertrend', {}).get('period', 10),
                                st_mult=cfg.get('supertrend', {}).get('multiplier', 3.0))
        except Exception:
            btc_df = None
        if verbose:
            print(f"[backtest] BTC regime gate: {'loaded' if btc_df is not None else 'UNAVAILABLE (fail-open)'}")

    all_records = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(backtest_symbol, u['sym'], u, cfg, btc_df): u['sym'] for u in universe}
        for f in as_completed(futs):
            sym = futs[f]
            try:
                recs = f.result()
                all_records.extend(recs)
                if verbose:
                    wins = sum(1 for r in recs if r['result'] == 'WIN')
                    print(f"  {sym:12s} -> {len(recs):3d} signals | {wins} wins | "
                          f"{sum(1 for r in recs if r['result']=='LOSS')} losses | "
                          f"{sum(1 for r in recs if r['result']=='EXPIRED')} expired")
            except Exception as e:
                if verbose:
                    print(f"  {sym:12s} -> FAILED: {type(e).__name__} {e}")

    all_records.sort(key=lambda r: r['signal_at'])
    stats = performance_stats(all_records)
    payload = {
        'updated_at': iso(),
        'months': months,
        'symbols': [u['sym'] for u in universe],
        'approximations': [
            'Trade resolution evaluated on 4H candles (live tracker uses 15m).',
            'Liquidity/volume scoring uses current 24h metrics as a historical proxy.',
            'Only LONG plans are simulated (shorts disabled by configuration).',
        ],
        'disclaimer': 'Historical simulation. Past results do NOT guarantee future performance.',
        'stats': stats,
        'calibration': calibration(all_records),
        'setup_stats': setup_stats(all_records),
    }
    save_json(data_path('backtest.json'), all_records)
    save_json(data_path('performance_backtest.json'), payload)
    if verbose:
        print(f"[backtest] saved: {len(all_records)} signals | win_rate={stats.get('win_rate')}% | "
              f"tp1={stats.get('tp1_hit_rate')}% | avg_score={stats.get('avg_score')} | "
              f"runtime {time.time()-t0:.0f}s")
    return all_records, payload


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cfg = load_config()
    run(cfg, verbose='--quiet' not in argv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
