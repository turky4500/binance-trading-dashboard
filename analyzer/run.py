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

from .scanner import scan
from .storage import iso, load_json, save_json, data_path
from . import whatsapp

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
