# -*- coding: utf-8 -*-
"""
Lifecycle tracker for published opportunities (read-only, no orders anywhere).

State machine (LONG example; SHORT mirrored):
  WAITING_CONFIRMATION -> READY (price entered entry zone / confirmation met)
  WAITING_CONFIRMATION -> INVALIDATED (close beyond invalidation level)
  WAITING_CONFIRMATION -> EXPIRED (older than expiry_hours, never triggered)
  READY                -> TRIGGERED (price touched the entry zone)
  READY                -> INVALIDATED / EXPIRED
  TRIGGERED            -> TP1_HIT -> TP2_HIT -> TP3_HIT (progressive)
  TRIGGERED            -> STOPPED (candle low <= SL; checked first = conservative)

After a setup triggers, targets are evaluated ONLY on candles from the touch
candle onward — price action before the entry fill never counts.
"""
import json
from datetime import datetime, timezone

from .indicators import klines_to_df

TERMINAL = {'STOPPED', 'TP3_HIT', 'EXPIRED', 'INVALIDATED'}
OPEN_AFTER_TRIGGER = {'TP1_HIT', 'TP2_HIT'}


def _parse(ts):
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S+00:00"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _ev(opp, at, frm, to, note=None):
    opp.setdefault('events', [])
    ev = {'at': at, 'from': frm, 'to': to}
    if note:
        ev['note'] = note
    opp['events'].append(ev)


def _close_record(opp, closed_at):
    rec = {
        'id': opp['id'], 'symbol': opp['symbol'], 'pair': opp.get('pair', opp['symbol']),
        'direction': opp['direction'], 'setup_type': opp.get('setup_type'),
        'setup_label': opp.get('setup_label'),
        'entry_mid': opp.get('entry_mid'), 'stop_loss': opp.get('stop_loss'),
        'tp1': opp.get('tp1'), 'tp2': opp.get('tp2'), 'tp3': opp.get('tp3'),
        'rr_tp1': opp.get('rr_tp1'), 'rr_tp2': opp.get('rr_tp2'),
        'score': opp.get('score'), 'created_at': opp.get('created_at'),
        'closed_at': closed_at, 'final_status': opp['status'],
    }
    if opp['status'] in ('TP1_HIT', 'TP2_HIT', 'TP3_HIT'):
        rec['result'] = 'WIN'
    elif opp['status'] == 'STOPPED':
        rec['result'] = 'LOSS'
    else:
        rec['result'] = opp['status']
    c = _parse(opp.get('created_at') or '')
    if c:
        rec['hold_hours'] = round((_parse(closed_at) - c).total_seconds() / 3600, 1)
    return rec


def track(opportunities, klines_15m, expiry_hours, now_iso):
    """Update statuses in-place. Returns list of closed records."""
    now = _parse(now_iso)
    if now is None:
        now = datetime.now(timezone.utc)
    closed = []
    for opp in opportunities:
        if opp.get('status') in TERMINAL:
            continue
        sym = opp['symbol']
        st = opp['status']
        created = _parse(opp.get('created_at') or '')
        if created is None:
            continue
        age_h = (now - created).total_seconds() / 3600

        # ---- expiry: setups that never triggered within expiry_hours
        if st in ('WAITING_CONFIRMATION', 'READY') and age_h > expiry_hours:
            _ev(opp, now_iso, st, 'EXPIRED')
            opp['status'] = 'EXPIRED'
            closed.append(_close_record(opp, now_iso))
            continue

        k = klines_15m.get(sym)
        if not k:
            continue
        try:
            df = klines_to_df(k)
        except Exception:
            continue
        recent = df[df['t'] > created]
        if len(recent) == 0:
            continue

        d = opp['direction']
        zone_lo, zone_hi = opp['entry_zone']
        sl = opp['stop_loss']
        inv = opp.get('invalidation_level')

        # find the first candle touching the entry zone (after creation)
        if d == 'LONG':
            touched = recent.index[recent['l'] <= zone_hi]
        else:
            touched = recent.index[recent['h'] >= zone_lo]
        has_touch = len(touched) > 0

        cl = float(recent['c'].iloc[-1])

        if st == 'WAITING_CONFIRMATION':
            if d == 'LONG':
                if inv is not None and cl < inv:
                    _ev(opp, now_iso, st, 'INVALIDATED'); opp['status'] = 'INVALIDATED'
                    closed.append(_close_record(opp, now_iso)); continue
                if has_touch:
                    _ev(opp, now_iso, st, 'READY'); opp['status'] = 'READY'
            else:
                if inv is not None and cl > inv:
                    _ev(opp, now_iso, st, 'INVALIDATED'); opp['status'] = 'INVALIDATED'
                    closed.append(_close_record(opp, now_iso)); continue
                if has_touch:
                    _ev(opp, now_iso, st, 'READY'); opp['status'] = 'READY'
            continue

        if st == 'READY':
            if d == 'LONG':
                if inv is not None and cl < inv:
                    _ev(opp, now_iso, st, 'INVALIDATED'); opp['status'] = 'INVALIDATED'
                    closed.append(_close_record(opp, now_iso)); continue
            else:
                if inv is not None and cl > inv:
                    _ev(opp, now_iso, st, 'INVALIDATED'); opp['status'] = 'INVALIDATED'
                    closed.append(_close_record(opp, now_iso)); continue
            if has_touch:
                _ev(opp, now_iso, st, 'TRIGGERED', note=f"entry zone {zone_lo}–{zone_hi} touched")
                opp['status'] = 'TRIGGERED'
                opp['triggered_at'] = str(recent.loc[touched[0], 't'])[:19].replace(' ', 'T') + 'Z'
            else:
                continue

        # ---- progressive TP / SL evaluation from the touch candle onward
        if opp['status'] in ('TRIGGERED', 'TP1_HIT', 'TP2_HIT'):
            if 'triggered_at' in opp and opp['triggered_at']:
                t_at = _parse(opp['triggered_at'])
                if t_at is not None:
                    win = recent[recent['t'] >= t_at]
                else:
                    win = recent.iloc[touched[0]:] if has_touch else recent
            else:
                win = recent.iloc[touched[0]:] if has_touch else recent
            if len(win) == 0:
                continue
            lo = float(win['l'].min())
            hi = float(win['h'].max())
            cur = opp['status']
            if d == 'LONG':
                if lo <= sl:  # stop checked first — conservative
                    _ev(opp, now_iso, cur, 'STOPPED'); opp['status'] = 'STOPPED'
                    closed.append(_close_record(opp, now_iso))
                elif hi >= opp['tp3']:
                    _ev(opp, now_iso, cur, 'TP3_HIT'); opp['status'] = 'TP3_HIT'
                    closed.append(_close_record(opp, now_iso))
                elif hi >= opp['tp2']:
                    _ev(opp, now_iso, cur, 'TP2_HIT'); opp['status'] = 'TP2_HIT'
                elif hi >= opp['tp1']:
                    _ev(opp, now_iso, cur, 'TP1_HIT'); opp['status'] = 'TP1_HIT'
            else:
                if hi >= sl:
                    _ev(opp, now_iso, cur, 'STOPPED'); opp['status'] = 'STOPPED'
                    closed.append(_close_record(opp, now_iso))
                elif lo <= opp['tp3']:
                    _ev(opp, now_iso, cur, 'TP3_HIT'); opp['status'] = 'TP3_HIT'
                    closed.append(_close_record(opp, now_iso))
                elif lo <= opp['tp2']:
                    _ev(opp, now_iso, cur, 'TP2_HIT'); opp['status'] = 'TP2_HIT'
                elif lo <= opp['tp1']:
                    _ev(opp, now_iso, cur, 'TP1_HIT'); opp['status'] = 'TP1_HIT'
    return closed


def performance_stats(history):
    """Deterministic stats over closed setups. Historical data is NOT a promise."""
    total = len(history)
    wins = sum(1 for h in history if h.get('result') == 'WIN')
    losses = sum(1 for h in history if h.get('result') == 'LOSS')
    decided = wins + losses
    stopped = losses
    expired = sum(1 for h in history if h.get('final_status') == 'EXPIRED')
    invalidated = sum(1 for h in history if h.get('final_status') == 'INVALIDATED')
    tp1 = sum(1 for h in history if h.get('final_status') in ('TP1_HIT', 'TP2_HIT', 'TP3_HIT'))
    tp2 = sum(1 for h in history if h.get('final_status') in ('TP2_HIT', 'TP3_HIT'))
    tp3 = sum(1 for h in history if h.get('final_status') == 'TP3_HIT')
    scores = [h.get('score') or 0 for h in history]
    rrs = [h.get('rr_tp2') or 0 for h in history if (h.get('rr_tp2') or 0) > 0]
    return {
        'total': total,
        'successful': tp1,
        'stopped': stopped,
        'expired': expired,
        'invalidated': invalidated,
        'win_rate': round(100 * wins / decided, 1) if decided else None,
        'tp1_hit_rate': round(100 * tp1 / decided, 1) if decided else None,
        'tp2_hit_rate': round(100 * tp2 / decided, 1) if decided else None,
        'tp3_hit_rate': round(100 * tp3 / decided, 1) if decided else None,
        'avg_score': round(sum(scores) / len(scores), 1) if scores else None,
        'avg_rr_tp2': round(sum(rrs) / len(rrs), 2) if rrs else None,
        'avg_hold_hours': round(sum(h.get('hold_hours') or 0 for h in history) / total, 1) if total else None,
    }
