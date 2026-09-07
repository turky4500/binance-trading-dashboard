# -*- coding: utf-8 -*-
"""
Entry point: python -m analyzer.run

Runs one full analysis cycle and saves results under /data.
Optionally sends Telegram alerts (only if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
are configured via environment variables — never hard-coded, never logged).

Alerts cover the full opportunity lifecycle:
  * new high-score setups   (above telegram.min_score_alert)
  * READY / TRIGGERED / TP1-3_HIT / STOPPED / EXPIRED / INVALIDATED
    (per-event toggles in config/settings.json -> telegram.notify)
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from .scanner import scan, TFS
from .storage import iso, load_json, save_json, data_path
from . import whatsapp
from .indicators import klines_to_df, enrich
from .indicators_smc import analyze_smc_multi
from .indicators_confluence import analyze_confluence_multi
from .indicators_momentum import analyze_momentum_multi
from .indicators_liquidity import analyze_liquidity_multi
from .indicators_adaptive import analyze_adaptive_multi
from .indicators_volume import analyze_volume_multi
from .indicators_unified import analyze_unified

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, 'config', 'settings.json')

NOTIFY_KEY = {
    'READY': 'ready',
    'TRIGGERED': 'triggered',
    'TP1_HIT': 'tp_hit',
    'TP2_HIT': 'tp_hit',
    'TP3_HIT': 'tp_hit',
    'STOPPED': 'stopped',
    'EXPIRED': 'expired',
    'INVALIDATED': 'invalidated',
}


def _notify_key(status):
    return NOTIFY_KEY.get(status)


def load_config(path=None):
    path = path or os.environ.get('DASHBOARD_CONFIG', DEFAULT_CONFIG)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def send_telegram(text, cfg):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not (token and chat) or not cfg.get('telegram', {}).get('enabled'):
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': chat, 'text': text}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def alert_text(op):
    z = op['entry_zone']
    return (
        f"🚨 New High-Quality Setup\n\n"
        f"{op['pair']}\n{op['direction']}\n\n"
        f"Setup: {op['setup_label']}\n"
        f"Entry: {z[0]} - {z[1]}\n"
        f"SL: {op['stop_loss']}\n"
        f"TP1: {op['tp1']}\n"
        f"TP2: {op['tp2']}\n"
        f"TP3: {op['tp3']}\n"
        f"R:R (TP1/TP2): {op['rr_tp1']} / {op['rr_tp2']}\n"
        f"Score: {op['score']}/100 ({op['grade']})\n"
        f"Status: {op['status']}"
    )


def lifecycle_text(opp, frm, to):
    p = opp.get('pair') or opp.get('symbol')
    d = opp.get('direction', '')
    z = opp.get('entry_zone') or [opp.get('entry_mid'), opp.get('entry_mid')]
    if to == 'READY':
        return (f"👌 READY — {p} {d}\n\n"
                f"Entry zone active: {z[0]} - {z[1]}\n"
                f"SL: {opp.get('stop_loss')} | TP1: {opp.get('tp1')} | TP2: {opp.get('tp2')}\n"
                f"Score: {opp.get('score')}/100")
    if to == 'TRIGGERED':
        return (f"🎯 TRIGGERED — {p} {d}\n\n"
                f"Entry zone {z[0]} - {z[1]} touched\n"
                f"SL: {opp.get('stop_loss')} | TP1: {opp.get('tp1')} | TP2: {opp.get('tp2')} | TP3: {opp.get('tp3')}\n"
                f"Score: {opp.get('score')}/100")
    if to in ('TP1_HIT', 'TP2_HIT', 'TP3_HIT'):
        entry = opp.get('entry_mid', 0)
        sl = opp.get('stop_loss', 0)
        R = abs(entry - sl) or 1
        level = {'TP1_HIT': opp.get('tp1'), 'TP2_HIT': opp.get('tp2'), 'TP3_HIT': opp.get('tp3')}[to]
        gain = abs(level - entry) / R if R else 0
        return (f"✅ {to.replace('_', ' ')} — {p} {d}\n\n"
                f"≈ +{gain:.1f}R | Next: TP2 {opp.get('tp2')} · TP3 {opp.get('tp3')}")
    if to == 'STOPPED':
        return f"🛑 STOPPED — {p} {d}\n\nStop-loss hit: {opp.get('stop_loss')}"
    if to == 'EXPIRED':
        return f"⏳ EXPIRED — {p} {d}\n\nSetup never triggered within the expiry window."
    if to == 'INVALIDATED':
        return f"❌ INVALIDATED — {p} {d}\n\nInvalidation level {opp.get('invalidation_level')} broken — idea abandoned."
    return f"{p} {d}: {frm} -> {to}"


def run_indicator_analysis(ops, cfg, verbose=True):
    """Run all 6 indicator modules + unified on every symbol with an active opportunity.
    Also scans the top universe for strong signals even without an existing opportunity.
    Saves JSON files for each indicator tab."""
    from .binance_client import klines as bc_klines
    from .indicators import klines_to_df as to_df, enrich

    stp = cfg.get('supertrend', {'period': 10, 'multiplier': 3.0})
    st_kw = dict(st_period=stp['period'], st_mult=stp['multiplier'])

    # Collect symbols to analyze: active opportunities + top universe
    sym_set = set()
    for op in ops:
        sym_set.add(op['symbol'])

    # Also scan top symbols by volume for fresh signals
    try:
        from .binance_client import ticker_24h
        tickers = ticker_24h()
        uni = cfg.get('universe', {})
        top = sorted(tickers, key=lambda t: -float(t.get('quoteVolume', 0)))
        for t in top[:50]:
            sym = t['symbol']
            if sym.endswith('USDT') and float(t.get('quoteVolume', 0)) >= 5e6:
                sym_set.add(sym)
    except Exception:
        pass

    if verbose:
        print(f"[indicators] Analyzing {len(sym_set)} symbols across 6 indicator modules")

    # Fetch klines for all symbols
    tf_data = {}
    from .scanner import _get_klines, _fetch_many
    syms = list(sym_set)

    # Fetch 4h and 1h for each symbol
    for sym, tf_name, k in _fetch_many(lambda s: _get_klines(s, '4h', 400), syms).values():
        if k is not None:
            tf_data.setdefault(sym, {})['4h'] = to_df(k)
    for sym, tf_name, k in _fetch_many(lambda s: _get_klines(s, '1h', 400), syms).values():
        if k is not None:
            tf_data.setdefault(sym, {})['1h'] = to_df(k)
    for sym, tf_name, k in _fetch_many(lambda s: _get_klines(s, '1d', 400), syms).values():
        if k is not None:
            tf_data.setdefault(sym, {})['1d'] = to_df(k)
    for sym, tf_name, k in _fetch_many(lambda s: _get_klines(s, '15m', 500), syms).values():
        if k is not None:
            tf_data.setdefault(sym, {})['15m'] = to_df(k)

    # Enrich all dataframes
    for sym in tf_data:
        for tf_name in list(tf_data[sym].keys()):
            try:
                tf_data[sym][tf_name] = enrich(tf_data[sym][tf_name], **st_kw)
            except Exception:
                del tf_data[sym][tf_name]

    # Run each indicator on every symbol
    smc_signals = []
    confluence_signals = []
    momentum_signals = []
    liquidity_signals = []
    adaptive_signals = []
    volume_signals = []
    unified_signals = []

    for sym, frames in tf_data.items():
        if len(frames) < 2:
            continue
        try:
            # Get current price from the last candle
            tf_4h = frames.get('4h') or frames.get('1h')
            if tf_4h is None or len(tf_4h) < 10:
                continue
            current_price = float(tf_4h['c'].iloc[-1])
            meta = next((op for op in ops if op['symbol'] == sym), None)

            # 1. SMC
            smc = analyze_smc_multi(frames)
            if smc['overall_bias'] != 'NEUTRAL' and smc['overall_confidence'] >= 45:
                smc_signals.append({
                    'symbol': sym,
                    'pair': sym.replace('USDT', '/USDT'),
                    'bias': smc['overall_bias'],
                    'confidence': smc['overall_confidence'],
                    'current_price': current_price,
                    'signals': smc['top_signals'][:5],
                    'per_timeframe': {k: {'bias': v['bias'], 'confidence': v['confidence']}
                                     for k, v in smc.get('per_timeframe', {}).items()},
                })

            # 2. Confluence
            conf = analyze_confluence_multi(frames)
            if conf['overall_bias'] != 'NEUTRAL' and conf['overall_score'] >= 45:
                confluence_signals.append({
                    'symbol': sym,
                    'pair': sym.replace('USDT', '/USDT'),
                    'bias': conf['overall_bias'],
                    'score': conf['overall_score'],
                    'current_price': current_price,
                    'per_timeframe': {k: {'bias': v['bias'], 'score': v['score']}
                                     for k, v in conf.get('per_timeframe', {}).items()},
                })

            # 3. Momentum
            mom = analyze_momentum_multi(frames)
            if mom['overall_bias'] != 'NEUTRAL' and mom['overall_score'] >= 40:
                momentum_signals.append({
                    'symbol': sym,
                    'pair': sym.replace('USDT', '/USDT'),
                    'bias': mom['overall_bias'],
                    'score': mom['overall_score'],
                    'current_price': current_price,
                    'top_signals': mom.get('top_signals', [])[:3],
                    'per_timeframe': {k: {'bias': v['bias'], 'score': v['score']}
                                     for k, v in mom.get('per_timeframe', {}).items()},
                })

            # 4. Liquidity
            liq = analyze_liquidity_multi(frames)
            if liq['overall_bias'] != 'NEUTRAL' and liq['overall_confidence'] >= 40:
                liquidity_signals.append({
                    'symbol': sym,
                    'pair': sym.replace('USDT', '/USDT'),
                    'bias': liq['overall_bias'],
                    'confidence': liq['overall_confidence'],
                    'current_price': current_price,
                    'top_signals': liq.get('top_signals', [])[:3],
                    'per_timeframe': {k: {'bias': v['bias'], 'confidence': v['confidence']}
                                     for k, v in liq.get('per_timeframe', {}).items()},
                })

            # 5. Adaptive
            ada = analyze_adaptive_multi(frames)
            if ada['overall_bias'] != 'NEUTRAL' and ada['overall_score'] >= 45:
                adaptive_signals.append({
                    'symbol': sym,
                    'pair': sym.replace('USDT', '/USDT'),
                    'bias': ada['overall_bias'],
                    'score': ada['overall_score'],
                    'current_price': current_price,
                    'per_timeframe': {k: {'bias': v['bias'], 'score': v['score']}
                                     for k, v in ada.get('per_timeframe', {}).items()},
                })

            # 6. Volume
            vol = analyze_volume_multi(frames)
            if vol['overall_bias'] != 'NEUTRAL' and vol['overall_score'] >= 45:
                volume_signals.append({
                    'symbol': sym,
                    'pair': sym.replace('USDT', '/USDT'),
                    'bias': vol['overall_bias'],
                    'score': vol['overall_score'],
                    'current_price': current_price,
                    'per_timeframe': {k: {'bias': v['bias'], 'score': v['score']}
                                     for k, v in vol.get('per_timeframe', {}).items()},
                })

            # 7. Unified
            uni_result = analyze_unified(frames)
            if uni_result['overall_bias'] != 'NEUTRAL' and uni_result['confidence'] >= 55:
                unified_signals.append({
                    'symbol': sym,
                    'pair': sym.replace('USDT', '/USDT'),
                    'bias': uni_result['overall_bias'],
                    'confidence': uni_result['confidence'],
                    'current_price': current_price,
                    'breakdown': uni_result['breakdown'],
                    'top_signals': uni_result['top_signals'][:5],
                })

        except Exception as e:
            if verbose:
                print(f"  [indicator] {sym}: {type(e).__name__}: {e}")
            continue

    # Sort by strength/confidence
    smc_signals.sort(key=lambda s: s.get('confidence', 0), reverse=True)
    confluence_signals.sort(key=lambda s: s.get('score', 0), reverse=True)
    momentum_signals.sort(key=lambda s: s.get('score', 0), reverse=True)
    liquidity_signals.sort(key=lambda s: s.get('confidence', 0), reverse=True)
    adaptive_signals.sort(key=lambda s: s.get('score', 0), reverse=True)
    volume_signals.sort(key=lambda s: s.get('score', 0), reverse=True)
    unified_signals.sort(key=lambda s: s.get('confidence', 0), reverse=True)

    # Save JSON files
    now = iso()
    save_json(data_path('signals_smc.json'), {
        'updated_at': now, 'count': len(smc_signals), 'signals': smc_signals[:30]
    })
    save_json(data_path('signals_confluence.json'), {
        'updated_at': now, 'count': len(confluence_signals), 'signals': confluence_signals[:30]
    })
    save_json(data_path('signals_momentum.json'), {
        'updated_at': now, 'count': len(momentum_signals), 'signals': momentum_signals[:30]
    })
    save_json(data_path('signals_liquidity.json'), {
        'updated_at': now, 'count': len(liquidity_signals), 'signals': liquidity_signals[:30]
    })
    save_json(data_path('signals_adaptive.json'), {
        'updated_at': now, 'count': len(adaptive_signals), 'signals': adaptive_signals[:30]
    })
    save_json(data_path('signals_volume.json'), {
        'updated_at': now, 'count': len(volume_signals), 'signals': volume_signals[:30]
    })
    save_json(data_path('signals_unified.json'), {
        'updated_at': now, 'count': len(unified_signals), 'signals': unified_signals[:30]
    })

    if verbose:
        print(f"[indicators] Results: SMC={len(smc_signals)} Confluence={len(confluence_signals)} "
              f"Momentum={len(momentum_signals)} Liquidity={len(liquidity_signals)} "
              f"Adaptive={len(adaptive_signals)} Volume={len(volume_signals)} "
              f"Unified={len(unified_signals)}")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cfg = load_config()
    verbose = '--quiet' not in argv
    ops, market, events = scan(cfg, verbose=verbose)

    tg = cfg.get('telegram', {})
    sent = 0
    if tg.get('enabled'):
        notify = tg.get('notify', {})
        min_alert = tg.get('min_score_alert', 85)
        # 1) brand-new high-score setups (fresh opportunities from this cycle)
        for op in events.get('new', []):
            if (op.get('score', 0) >= min_alert and notify.get('new_setup', True)
                    and op.get('status') in ('READY', 'WAITING_CONFIRMATION')):
                if send_telegram(alert_text(op), cfg):
                    sent += 1
        # 2) lifecycle transitions detected by the tracker this cycle
        for tr in events.get('transitions', []):
            key = _notify_key(tr['to'])
            if key and notify.get(key, False):
                if send_telegram(lifecycle_text(tr['opp'], tr['from'], tr['to']), cfg):
                    sent += 1
    if verbose:
        print(f"Analysis complete at {iso()} | market: {market['status']} | "
              f"opportunities: {len(ops)} | alerts sent: {sent}")
        for i, op in enumerate(ops[:cfg['max_opportunities']], 1):
            print(f"  #{i} {op['pair']:12s} {op['direction']:5s} score={op['score']:3d} "
                  f"status={op['status']:20s} entry={op['entry_zone']} sl={op['stop_loss']} "
                  f"tp1={op['tp1']} tp2={op['tp2']} rr2={op['rr_tp2']}")
    wa_sent = _send_whatsapp_alerts(events, cfg, ops)
    if verbose and wa_sent:
        print(f"WhatsApp alerts sent: {wa_sent}")

    # Run indicator analysis for the 6 tabs + unified
    try:
        run_indicator_analysis(ops, cfg, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"[indicators] Error: {type(e).__name__}: {e}")

    return 0


def _send_whatsapp_alerts(events, cfg, ops):
    """Send WhatsApp for (1) new confirmed opportunities and (2) new
    SuperTrend entry signals. Both are deduplicated against the persisted
    state file so each distinct signal/opportunity alerts exactly once.

    Sending requires WHATSAPP_TOKEN env var + whatsapp.enabled == True.
    Returns the number of accepted messages.

    Writes data/whatsapp_delivery.json each cycle with a diagnostic record so
    delivery can be verified from the committed data (token set? how many
    signals were new vs actually delivered?).
    """
    from .storage import iso
    wa = cfg.get("whatsapp", {})
    token_set = bool(os.environ.get("WHATSAPP_TOKEN", "").strip())
    rep = {
        "ts": iso(),
        "enabled": bool(wa.get("enabled")),
        "token_set": token_set,
        "endpoint": wa.get("endpoint"),
        "to": wa.get("to"),
        "new_st_candidates": 0,
        "new_opp_candidates": 0,
        "delivered": 0,
        "failures": [],
    }
    if not wa.get("enabled") or not token_set:
        rep["reason"] = ("disabled" if not wa.get("enabled")
                         else "WHATSAPP_TOKEN not set")
        save_json(data_path("whatsapp_delivery.json"), rep)
        return 0
    notify = wa.get("notify", {})
    sent = 0

    if notify.get("new_opportunity", True):
        new_opps = whatsapp.filter_new_opportunities(
            events.get("new", []), wa.get("min_score_alert", 84))
        rep["new_opp_candidates"] = len(new_opps)
        for op in new_opps:
            ok, err = whatsapp.send_whatsapp_diag(
                whatsapp.opportunity_text(op), cfg)
            if ok:
                sent += 1
            else:
                rep.setdefault("failures", []).append(
                    "opp {}: {}".format(op.get("symbol"), err))

    if notify.get("new_st_signal", True):
        st_board = load_json(data_path("st_signals.json"), {})
        max_fresh = wa.get("max_signal_fresh_hours")
        max_age = wa.get("max_st_signal_age_bars", 1)
        cands = whatsapp.filter_new_st_signals(st_board, max_fresh, max_age_bars=max_age)
        rep["new_st_candidates"] = len(cands)
        sent_syms = []
        for s in cands:
            s = dict(s)
            if max_fresh is not None:
                s["aged"] = int(s.get("bars_held") or 0) > int(max_fresh)
            ok, err = whatsapp.send_whatsapp_diag(
                whatsapp.st_signal_text(s), cfg)
            if ok:
                sent += 1
                sent_syms.append(s.get("symbol"))
            else:
                rep.setdefault("failures", []).append(
                    "st {}: {}".format(s.get("symbol"), err))
        if sent_syms:
            whatsapp.mark_st_sent(sent_syms)

    rep["delivered"] = sent
    save_json(data_path("whatsapp_delivery.json"), rep)
    return sent


if __name__ == '__main__':
    sys.exit(main())
