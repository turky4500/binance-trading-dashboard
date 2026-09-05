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
from datetime import datetime, timedelta, timezone

from .storage import load_json, save_json, data_path

ENDPOINT_DEFAULT = "https://wats-saas.duckdns.org/api/v1/send"
STATE_FILE = "whatsapp_state.json"

# The owner's timezone for human-readable alert times (UTC+3, no DST).
RIYADH_TZ = timezone(timedelta(hours=3), name="Asia/Riyadh")


def _load_state():
    return load_json(data_path(STATE_FILE), {})


def _save_state(state):
    save_json(data_path(STATE_FILE), state)


def filter_new_st_signals(st_board, max_fresh_hours=None):
    """Return SuperTrend signals that have not been successfully sent yet.

    Does NOT modify state — the caller is responsible for marking signals
    as sent (via mark_st_sent) only AFTER a successful delivery. This avoids
    the race condition where concurrent CI runs mark signals as "seen" without
    actually sending them.
    """
    state = _load_state()
    seen = set(state.get("st_notified", []))
    sigs = (st_board or {}).get("signals") or []
    return [s for s in sigs if s.get("symbol") and s["symbol"] not in seen]


def mark_st_sent(symbols):
    """Mark symbols as successfully sent in st_notified. Called by the caller
    after each successful send, NOT by filter_new_st_signals."""
    state = _load_state()
    seen = set(state.get("st_notified", []))
    seen.update(symbols)
    state["st_notified"] = sorted(seen)
    _save_state(state)


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


def send_whatsapp_diag(text, cfg, to=None):
    """Like send_whatsapp but returns (success: bool, error: str|None)."""
    token = os.environ.get("WHATSAPP_TOKEN", "").strip()
    wa = cfg.get("whatsapp", {})
    if not token:
        return False, "WHATSAPP_TOKEN not set"
    if not wa.get("enabled"):
        return False, "whatsapp disabled in config"
    recipient = to or wa.get("to")
    endpoint = wa.get("endpoint") or ENDPOINT_DEFAULT
    if not recipient:
        return False, "no recipient"
    try:
        payload = json.dumps({"to": recipient, "message": text}).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=payload, method="POST",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            })
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = 200 <= r.status < 300
            return ok, None if ok else "HTTP {}".format(r.status)
    except Exception as e:
        return False, str(e)[:200]


def _fmt_signal_time(iso_str):
    """'2026-09-03T14:00:00+00:00' -> '2026-09-03 17:00' in Riyadh time (UTC+3).
    Falls back to the raw string if parsing fails."""
    try:
        dt = datetime.fromisoformat(str(iso_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(RIYADH_TZ).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(iso_str) if iso_str else '—'


def st_signal_text(sig):
    """Format a new SuperTrend entry signal (1H board) for WhatsApp.

    Always includes the exact signal start time (Riyadh) and the signal's
    current age, so late alerts carry enough context for the owner to decide.
    """
    pair = sig.get("pair") or (sig.get("symbol") or "").replace("USDT", "/USDT")
    change = sig.get("change_pct")
    change_txt = ("+" if change is not None and change >= 0 else "") + \
        f"{change:.2f}%" if change is not None else "—"
    bars = sig.get("bars_held")
    lines = [
        "📈 إشارة سوبر ترند جديدة",
        "",
        f"{pair}",
        "الاتجاه (ساعة / 1H): شراء (BUY)",
        f"وقت بداية الإشارة: {_fmt_signal_time(sig.get('signal_at'))} (بتوقيت الرياض)",
    ]
    if bars is not None:
        lines.append(f"عمر الإشارة الآن: {bars} ساعة")
    lines += [
        f"سعر بداية الإشارة: {sig.get('price_at_signal')}",
        f"السعر الحالي: {sig.get('current_price')} ({change_txt})",
        f"R.S.I: {sig.get('rsi') if sig.get('rsi') is not None else '—'}",
        "",
    ]
    if sig.get("aged"):
        lines.append(
            "⚠️ تنبيه متأخر: ظهرت هذه الإشارة على اللوحة بعد أكثر من "
            "24 ساعة من بدايتها، فالسعر قد يكون تحرك بالفعل — قرار الدخول "
            "لك بعد التحقق من السعر والاتجاه."
        )
    else:
        lines.append("فرصة دخول جديدة على فريم الساعة.")
    return "\n".join(lines)


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
