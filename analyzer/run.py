# -*- coding: utf-8 -*-
"""
Entry point: python -m analyzer.run

Runs one full analysis cycle and saves results under /data.
Optionally sends Telegram alerts (only if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
are configured via environment variables — never hard-coded, never logged).
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from .scanner import scan
from .storage import load_json, data_path, iso

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, 'config', 'settings.json')


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


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cfg = load_config()
    verbose = '--quiet' not in argv
    ops, market = scan(cfg, verbose=verbose)

    # Telegram alerts for brand-new READY setups above the alert threshold
    min_alert = cfg.get('telegram', {}).get('min_score_alert', 85)
    prev = load_json(data_path('opportunities.json'), [])
    prev_ids = {o['id'] for o in prev}
    sent = 0
    for op in ops:
        if op['score'] >= min_alert and op['status'] in ('READY', 'WAITING_CONFIRMATION') and op['id'] not in prev_ids:
            if send_telegram(alert_text(op), cfg):
                sent += 1
    if verbose:
        print(f"Analysis complete at {iso()} | market: {market['status']} | "
              f"opportunities: {len(ops)} | alerts sent: {sent}")
        for i, op in enumerate(ops[:cfg['max_opportunities']], 1):
            print(f"  #{i} {op['pair']:12s} {op['direction']:5s} score={op['score']:3d} "
                  f"status={op['status']:20s} entry={op['entry_zone']} sl={op['stop_loss']} "
                  f"tp1={op['tp1']} tp2={op['tp2']} rr2={op['rr_tp2']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
