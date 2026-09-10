# -*- coding: utf-8 -*-
"""Signal performance tracking — records each ST/AI signal and checks TP/SL hit."""
import time
from datetime import datetime, timezone, timedelta

from .storage import load_json, save_json, data_path


TRACKING_FILE = "signal_tracking.json"
RESULTS_FILE = "results.json"


def _load_tracking():
    return load_json(data_path(TRACKING_FILE), {"st": [], "ai": []})


def _save_tracking(data):
    save_json(data_path(TRACKING_FILE), data)


def record_signal(sig, indicator):
    """Record a new signal for tracking. indicator = 'st' or 'ai'."""
    data = _load_tracking()
    key = indicator
    if key not in data:
        data[key] = []
    entry = {
        "symbol": sig.get("symbol"),
        "signal_at": sig.get("signal_at"),
        "entry": sig.get("current_price") or sig.get("price_at_signal"),
        "sl": sig.get("stop_loss"),
        "tp1": sig.get("tp1"),
        "tp2": sig.get("tp2"),
        "tp3": sig.get("tp3"),
        "confidence": sig.get("confidence"),
        "status": "pending",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    data[key].append(entry)
    _save_tracking(data)
    return entry


def check_signals(frames):
    """Check all pending signals against current price data. Updates status."""
    data = _load_tracking()
    changed = False
    for key in ("st", "ai"):
        for rec in data.get(key, []):
            if rec.get("status") != "pending":
                continue
            sym = rec.get("symbol")
            df = frames.get(sym)
            if df is None or len(df) < 1:
                continue
            cur = float(df['c'].iloc[-1])
            hi = float(df['h'].iloc[-1])
            lo = float(df['l'].iloc[-1])
            sl = rec.get("sl")
            tp1 = rec.get("tp1")
            tp2 = rec.get("tp2")
            tp3 = rec.get("tp3")
            if not sl:
                continue
            # TP takes priority over SL (same as original project)
            if tp3 and hi >= float(tp3):
                rec["status"] = "TP3"
                rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
                rec["result_price"] = float(tp3)
                changed = True
            elif tp2 and hi >= float(tp2):
                rec["status"] = "TP2"
                rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
                rec["result_price"] = float(tp2)
                changed = True
            elif tp1 and hi >= float(tp1):
                rec["status"] = "TP1"
                rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
                rec["result_price"] = float(tp1)
                changed = True
            elif cur <= float(sl):
                rec["status"] = "SL"
                rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
                rec["result_price"] = float(sl)
                changed = True
    if changed:
        _save_tracking(data)
    return data


def compute_results(data):
    """Compute stats per indicator and combined, include raw records."""
    stats = {}
    for key in ("st", "ai"):
        sigs = data.get(key, [])
        total = len(sigs)
        pending = sum(1 for s in sigs if s.get("status") == "pending")
        tp1 = sum(1 for s in sigs if s.get("status") in ("TP1", "TP2", "TP3"))
        tp2 = sum(1 for s in sigs if s.get("status") in ("TP2", "TP3"))
        tp3 = sum(1 for s in sigs if s.get("status") == "TP3")
        sl = sum(1 for s in sigs if s.get("status") == "SL")
        decided = tp1 + sl
        stats[key] = {
            "total": total,
            "pending": pending,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "sl": sl,
            "win_rate": round(100 * tp1 / decided, 1) if decided else None,
            "tp2_rate": round(100 * tp2 / decided, 1) if decided else None,
            "tp3_rate": round(100 * tp3 / decided, 1) if decided else None,
        }
    # Raw records for frontend table
    stats["st_records"] = [{"indicator": "st", **s} for s in data.get("st", [])]
    stats["ai_records"] = [{"indicator": "ai", **s} for s in data.get("ai", [])]
    # Combined
    all_sigs = data.get("st", []) + data.get("ai", [])
    total_all = len(all_sigs)
    pending_all = sum(1 for s in all_sigs if s.get("status") == "pending")
    tp1_all = sum(1 for s in all_sigs if s.get("status") in ("TP1", "TP2", "TP3"))
    tp2_all = sum(1 for s in all_sigs if s.get("status") in ("TP2", "TP3"))
    tp3_all = sum(1 for s in all_sigs if s.get("status") == "TP3")
    sl_all = sum(1 for s in all_sigs if s.get("status") == "SL")
    decided_all = tp1_all + sl_all
    stats["combined"] = {
        "total": total_all,
        "pending": pending_all,
        "tp1": tp1_all,
        "tp2": tp2_all,
        "tp3": tp3_all,
        "sl": sl_all,
        "win_rate": round(100 * tp1_all / decided_all, 1) if decided_all else None,
        "tp2_rate": round(100 * tp2_all / decided_all, 1) if decided_all else None,
        "tp3_rate": round(100 * tp3_all / decided_all, 1) if decided_all else None,
    }
    stats["updated_at"] = datetime.now(timezone.utc).isoformat()
    return stats
