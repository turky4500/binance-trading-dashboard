# -*- coding: utf-8 -*-
"""
WhatsApp alert delivery for the dashboard.

Sends messages through the wats-saas HTTP API. The API token is NEVER
hard-coded or committed: it is read from the WHATSAPP_TOKEN environment
variable (configured as a GitHub Secret in CI), exactly like the Telegram
alerts. All errors are swallowed so a notification failure never breaks the
analysis run.
"""
import json
import os
import urllib.request

from .storage import load_json, save_json, data_path

ENDPOINT_DEFAULT = "https://wats-saas.duckdns.org/api/v1/send"
STATE_FILE = "whatsapp_state.json"


def _load_state():
    return load_json(data_path(STATE_FILE), {})


def _save_state(state):
    save_json(data_path(STATE_FILE), state)


def filter_new_st_signals(st_board):
    """Return SuperTrend signals that have not been notified before.

    Only daily BUY signals that are newly present (and fresh enough to be a
    real entry, per whatsapp.notify.new_st_signal) are returned. The caller
    decides whether to actually send; the state file is committed with the
    data each CI cycle so seen-dates persist across runs.
    """
    state = _load_state()
    seen = set(state.get("st_notified", []))
    sigs = (st_board or {}).get("signals") or []
    new = []
    for s in sigs:
        sym = s.get("symbol")
        if sym and sym not in seen:
            new.append(s)
    state["st_notified"] = sorted({s.get("symbol") for s in sigs} | seen)
    _save_state(state)
    return new


def filter_new_opportunities(new_ops, min_score=84):
    """Return READY/WAITING opportunities that have not been notified before.

    The input is the brand-new opportunities list from the scanner
    (events['new']). We notify each distinct symbol+direction once.
    """
    state = _load_state()
    seen = set(state.get("opp_notified", []))
    new = []
    for op in new_ops or []:
        key = "{}|{}".format(op.get("symbol"), op.get("direction"))
        if op.get("status") not in ("READY", "WAITING_CONFIRMATION"):
            continue
        if op.get("score", 0) < min_score:
            continue
        if key not in seen:
            new.append(op)
            seen.add(key)
    state["opp_notified"] = sorted(seen)
    _save_state(state)
    return new


def reset_state(which="all"):
    """Helper to clear notification history (used in tests / manual)."""
    state = _load_state()
    if which in ("all", "st"):
        state["st_notified"] = []
    if which in ("all", "opp"):
        state["opp_notified"] = []
    _save_state(state)


def send_whatsapp(text, cfg, to=None):
    """Send a plain-text WhatsApp message. Returns True on accepted delivery.

    Requires WHATSAPP_TOKEN env var and whatsapp.enabled == True. Recipient,
    endpoint and the message come from config/args. Never raises.
    """
    token = os.environ.get("WHATSAPP_TOKEN", "").strip()
    wa = cfg.get("whatsapp", {})
    if not token or not wa.get("enabled"):
        return False
    recipient = to or wa.get("to")
    endpoint = wa.get("endpoint") or ENDPOINT_DEFAULT
    if not recipient:
        return False
    try:
        payload = json.dumps({"to": recipient, "message": text}).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=payload, method="POST",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            })
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def st_signal_text(sig):
    """Format a new daily SuperTrend entry signal for WhatsApp."""
    pair = sig.get("pair") or (sig.get("symbol") or "").replace("USDT", "/USDT")
    change = sig.get("change_pct")
    change_txt = ("+" if change is not None and change >= 0 else "") + \
        f"{change:.2f}%" if change is not None else "—"
    return (
        f"📈 إشارة سوبر ترند جديدة\n\n"
        f"{pair}\n"
        f"الاتجاه (ساعة / 1H): شراء (BUY)\n"
        f"سعر بداية الإشارة: {sig.get('price_at_signal')}\n"
        f"السعر الحالي: {sig.get('current_price')} ({change_txt})\n"
        f"R.S.I: {sig.get('rsi') if sig.get('rsi') is not None else '—'}\n\n"
        f"فرصة دخول جديدة على فريم الساعة."
    )


def opportunity_text(op):
    """Format a newly-confirmed READY opportunity for WhatsApp."""
    pair = op.get("pair") or (op.get("symbol") or "").replace("USDT", "/USDT")
    d = op.get("direction", "")
    z = op.get("entry_zone") or [op.get("entry_mid"), op.get("entry_mid")]
    return (
        f"✅ فرصة مؤكدة جديدة\n\n"
        f"{pair} {d}\n"
        f"الإعداد: {op.get('setup_label')}\n"
        f"منطقة الدخول: {z[0]} - {z[1]}\n"
        f"وقف الخسارة: {op.get('stop_loss')}\n"
        f"الأهداف: TP1 {op.get('tp1')} · TP2 {op.get('tp2')} · TP3 {op.get('tp3')}\n"
        f"الدرجة: {op.get('score')}/100 ({op.get('grade')})"
    )
