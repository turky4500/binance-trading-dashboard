# -*- coding: utf-8 -*-
"""Deterministic SuperTrend scalp agent.

The module is deliberately not an LLM.  It receives the same Binance candles
already fetched by the pipeline, evaluates mechanical filters, and emits a
strict JSON-serialisable payload for the static dashboard.  Prices and levels
are computed only from market data; no browser or secret-bearing API is
involved.
"""
from collections import Counter

from .indicators import enrich, swings
from .scoring import grade
from .signal import tf_state

AGENT_SCORE_LABELS = {
    'trend_alignment': 'Trend & SuperTrend Alignment',
    'price_action': 'Price Action Quality',
    'entry_quality': 'EMA200 Extension / Entry Quality',
    'risk_reward': 'Risk / Reward',
    'momentum': 'Momentum',
    'volume': 'Volume',
    'liquidity': 'Liquidity',
}

SCHEMA_VERSION = "scalp-supertrend-1.0"

REJECTION_TEXT = {
    "MISSING_DATA": ("بيانات الشموع أو المؤشرات غير مكتملة.", "Candle or indicator data is incomplete."),
    "MARKET_BREADTH_GATE": ("اتساع السوق دون الحد المطلوب لنشر فرصة جديدة.", "Market breadth is below the new-signal gate."),
    "EMA200_NOT_CONFIRMED": ("السعر غير ثابت فوق EMA200 على أطر التنفيذ والتأكيد.", "Price is not confirmed above EMA200 on the execution and confirmation frames."),
    "EMA200_OVEREXTENDED": ("السعر مبتعد أكثر من المسموح عن EMA200.", "Price is overextended from EMA200."),
    "LIVE_PRICE_CHASE": ("السعر اللحظي اندفع بعيدًا عن آخر شمعة مغلقة؛ لا تطارد الحركة.", "Live price ran too far beyond the last closed candle; do not chase it."),
    "HTF_TREND_CONFLICT": ("اتجاه 4 ساعات يتعارض مع صفقة LONG.", "The 4-hour trend conflicts with a LONG setup."),
    "SUPERTREND_BEARISH": ("SuperTrend ليس صاعدًا على جميع أطر التأكيد المطلوبة.", "SuperTrend is not bullish on every required confirmation frame."),
    "SUPERTREND_CHOP": ("SuperTrend بدّل اتجاهه مرارًا خلال آخر ثلاث شموع.", "SuperTrend flipped repeatedly during the last three closed candles."),
    "UPPER_WICK_REJECTION": ("ظهر ذيل علوي طويل قرب مقاومة.", "A long upper rejection wick appeared near resistance."),
    "LOWER_HIGHS": ("آخر القمم الهيكلية هابطة.", "Recent structural highs are descending."),
    "BREAKOUT_NOT_HOLDING": ("لا يوجد ثبات اختراق أو ارتداد مؤكد من SuperTrend.", "There is no held breakout or confirmed SuperTrend bounce."),
    "INVALID_LEVEL_ORDER": ("ترتيب الدخول والوقف والأهداف غير صالح.", "Entry, stop and target ordering is invalid."),
    "STOP_TOO_WIDE": ("مسافة وقف الخسارة أكبر من الحد المسموح.", "Stop-loss distance exceeds the configured maximum."),
    "RR_BELOW_MINIMUM": ("العائد إلى المخاطرة للهدف الأول أقل من الحد الأدنى.", "TP1 risk/reward is below the configured minimum."),
    "SCORE_BELOW_MINIMUM": ("درجة الجودة أقل من حد النشر.", "The deterministic quality score is below the publication threshold."),
}

DEFAULTS = {
    "enabled": True,
    "min_score": 82,
    "max_signals": 8,
    "max_ema200_extension_pct": 5.0,
    "max_live_chase_atr": 0.75,
    "max_upper_wick_body_ratio": 1.5,
    "resistance_proximity_pct": 0.30,
    "max_stop_distance_pct": 3.0,
    "min_rr_tp1": 1.5,
    "entry_zone_half_width_pct": 0.15,
    "stop_buffer_atr": 0.10,
    "supertrend_bounce_atr": 0.25,
    "tp1_pct": 1.20,
    "tp2_pct": 2.25,
    "tp3_pct": 4.25,
}


def _cfg(settings):
    out = dict(DEFAULTS)
    out.update(settings.get("quant_agent") or {})
    return out


def _round_price(value):
    value = float(value)
    if value >= 1000:
        return round(value, 2)
    if value >= 100:
        return round(value, 3)
    if value >= 1:
        return round(value, 5)
    return round(value, 8)


def _closed_enriched(df, stp):
    """Drop the still-forming Binance candle, then calculate indicators."""
    if df is None or len(df) < 202:
        return None
    raw = df[['t', 'o', 'h', 'l', 'c', 'v']].iloc[:-1].copy().reset_index(drop=True)
    if len(raw) < 200:
        return None
    return enrich(raw, st_period=stp['period'], st_mult=stp['multiplier'])


def _flip_count(values):
    vals = [int(v) for v in values]
    return sum(1 for a, b in zip(vals, vals[1:]) if a != b)


def _upper_wick_rejection(df, cfg):
    """Long upper wick in the last three bars while testing prior resistance."""
    if len(df) < 30:
        return False
    resistance = float(df['h'].iloc[-23:-3].max())
    proximity = float(cfg['resistance_proximity_pct']) / 100.0
    limit = float(cfg['max_upper_wick_body_ratio'])
    for _, row in df.iloc[-3:].iterrows():
        body = abs(float(row['c']) - float(row['o']))
        candle_range = max(float(row['h']) - float(row['l']), 1e-12)
        body = max(body, candle_range * 0.05)
        upper = float(row['h']) - max(float(row['o']), float(row['c']))
        at_resistance = float(row['h']) >= resistance * (1.0 - proximity)
        if at_resistance and upper / body > limit:
            return True
    return False


def _lower_highs(df):
    hs, _ = swings(df.tail(100).reset_index(drop=True), k=2)
    vals = [float(x[1]) for x in hs[-3:]]
    return len(vals) == 3 and vals[0] > vals[1] > vals[2]


def _entry_trigger(df, cfg):
    """Return (passed, trigger name) for held breakout or SuperTrend bounce."""
    if len(df) < 30:
        return False, None
    prior_resistance = float(df['h'].iloc[-24:-4].max())
    breakout = bool((df['c'].iloc[-3:] > prior_resistance).all())
    bounce = False
    bounce_atr = float(cfg['supertrend_bounce_atr'])
    for _, row in df.iloc[-3:].iterrows():
        near = float(row['l']) <= float(row['st_line']) + bounce_atr * float(row['atr'])
        held = float(row['c']) > float(row['st_line']) and int(row['st_dir']) == 1
        bullish = float(row['c']) > float(row['o'])
        if near and held and bullish:
            bounce = True
            break
    if breakout:
        return True, "BREAKOUT_HOLD"
    if bounce:
        return True, "SUPERTREND_BOUNCE"
    return False, None


def _pick_target(entry, pct, levels, lo_pct, hi_pct):
    """Prefer a structural level inside the target band; otherwise use the band midpoint."""
    lo = entry * (1.0 + lo_pct / 100.0)
    hi = entry * (1.0 + hi_pct / 100.0)
    raw = entry * (1.0 + pct / 100.0)
    valid = [float(v) for v in levels if v and lo <= float(v) <= hi]
    return min(valid, key=lambda v: abs(v - raw)) if valid else raw


def _rejection(symbol, code):
    ar, en = REJECTION_TEXT[code]
    return {"symbol": symbol, "codes": [code], "reason_ar": ar, "reason_en": en}


def _agent_score(state, ticker, extension, trigger, rr1):
    """100-point score calibrated to the scalp filters (not the 4H swing score)."""
    # Mandatory filters already guarantee the core alignment; this score ranks
    # the quality inside that valid set instead of scoring unrelated 4H setups.
    trend = 0
    trend += sum(4 for tf in ('15m', '1h', '4h') if state[tf]['st_dir'] == 1)
    trend += sum(3 for tf in ('15m', '1h', '4h') if state[tf]['above200'])
    trend += 4 if state['1d']['above200'] else 0

    price_action = 20 if trigger == 'BREAKOUT_HOLD' else 18
    entry_quality = 15 if extension <= 2.0 else (12 if extension <= 3.5 else 8)
    risk_reward = 15 if rr1 >= 2.0 else (13 if rr1 >= 1.75 else 11)

    momentum = 0
    momentum += 4 if 45 <= state['15m']['rsi'] <= 72 else 0
    momentum += 3 if 45 <= state['1h']['rsi'] <= 72 else 0
    momentum += 1.5 if state['15m']['macd_h'] > 0 else 0
    momentum += 1.5 if state['1h']['macd_h'] > 0 else 0

    vr = state['15m']['vol_ratio3']
    volume = 10 if vr >= 1.5 else (8 if vr >= 1.2 else (6 if vr >= 1.0 else 3))
    spread = float(ticker.get('spread') or 99)
    trades = int(ticker.get('trades') or 0)
    liquidity = (3 if spread <= 0.03 else (2 if spread <= 0.10 else 1))
    liquidity += 2 if trades >= 50000 else (1 if trades >= 10000 else 0)

    parts = {
        'trend_alignment': round(trend, 1),
        'price_action': round(price_action, 1),
        'entry_quality': round(entry_quality, 1),
        'risk_reward': round(risk_reward, 1),
        'momentum': round(momentum, 1),
        'volume': round(volume, 1),
        'liquidity': round(liquidity, 1),
    }
    return round(sum(parts.values())), parts


def evaluate_candidate(symbol, ticker, frames, settings, now_iso):
    """Evaluate one symbol. Returns (signal, rejection, diagnostics)."""
    cfg = _cfg(settings)
    stp = settings.get('supertrend', {'period': 10, 'multiplier': 3.0})
    enriched = {name: _closed_enriched(df, stp) for name, df in frames.items()}
    if not all(enriched.get(tf) is not None for tf in ('15m', '1h', '4h', '1d')):
        return None, _rejection(symbol, 'MISSING_DATA'), None

    state = {
        '15m': tf_state(enriched['15m'], k=2),
        '1h': tf_state(enriched['1h'], k=2),
        '4h': tf_state(enriched['4h'], k=3),
        '1d': tf_state(enriched['1d'], k=3),
    }
    current = float(ticker.get('last') or state['15m']['close'])
    e15, e1, e4 = state['15m']['ema200'], state['1h']['ema200'], state['4h']['ema200']
    extension = (current - e15) / e15 * 100 if e15 > 0 else 999.0
    live_chase_atr = ((current - state['15m']['close']) / state['15m']['atr']
                      if state['15m']['atr'] > 0 else 999.0)
    diagnostics = {
        'ema200_distance_pct': round(extension, 3),
        'live_chase_atr': round(live_chase_atr, 3),
        'supertrend_last_3': ['UP' if int(v) == 1 else 'DOWN' for v in enriched['15m']['st_dir'].iloc[-3:]],
        'upper_wick_rejection': _upper_wick_rejection(enriched['15m'], cfg),
        'lower_highs_detected': _lower_highs(enriched['15m']),
    }

    if not (current > e15 and state['1h']['close'] > e1):
        return None, _rejection(symbol, 'EMA200_NOT_CONFIRMED'), diagnostics
    if extension > float(cfg['max_ema200_extension_pct']):
        return None, _rejection(symbol, 'EMA200_OVEREXTENDED'), diagnostics
    if live_chase_atr > float(cfg['max_live_chase_atr']):
        return None, _rejection(symbol, 'LIVE_PRICE_CHASE'), diagnostics
    if not (state['4h']['close'] > e4 and state['4h']['st_dir'] == 1):
        return None, _rejection(symbol, 'HTF_TREND_CONFLICT'), diagnostics
    if not (state['15m']['st_dir'] == state['1h']['st_dir'] == state['4h']['st_dir'] == 1
            and current > state['15m']['st_line']):
        return None, _rejection(symbol, 'SUPERTREND_BEARISH'), diagnostics
    if _flip_count(enriched['15m']['st_dir'].iloc[-3:]) > 1:
        return None, _rejection(symbol, 'SUPERTREND_CHOP'), diagnostics
    if diagnostics['upper_wick_rejection']:
        return None, _rejection(symbol, 'UPPER_WICK_REJECTION'), diagnostics
    if diagnostics['lower_highs_detected']:
        return None, _rejection(symbol, 'LOWER_HIGHS'), diagnostics

    trigger_ok, trigger = _entry_trigger(enriched['15m'], cfg)
    diagnostics['entry_trigger'] = trigger
    if not trigger_ok:
        return None, _rejection(symbol, 'BREAKOUT_NOT_HOLDING'), diagnostics

    # Protective stop: the tighter valid support of SuperTrend or the last two
    # bullish-candle lows, with a small ATR buffer below it.
    recent = enriched['15m'].tail(8)
    bullish = recent[recent['c'] > recent['o']].tail(2)
    if len(bullish) < 2:
        return None, _rejection(symbol, 'MISSING_DATA'), diagnostics
    structural_low = float(bullish['l'].min())
    support_anchor = max(float(state['15m']['st_line']), structural_low)
    stop = support_anchor - float(cfg['stop_buffer_atr']) * float(state['15m']['atr'])
    if not 0 < stop < current:
        return None, _rejection(symbol, 'INVALID_LEVEL_ORDER'), diagnostics
    stop_pct = (current - stop) / current * 100
    if stop_pct > float(cfg['max_stop_distance_pct']):
        return None, _rejection(symbol, 'STOP_TOO_WIDE'), diagnostics

    tp1 = current * (1.0 + float(cfg['tp1_pct']) / 100.0)
    tp2_levels = [p for p, _ in state['15m']['sw_highs'][-8:]] + [state['15m']['hi20'], state['1h']['last_high']]
    tp3_levels = [p for p, _ in state['1h']['sw_highs'][-8:]] + [state['1h']['hi50'], state['4h']['last_high']]
    tp2 = _pick_target(current, float(cfg['tp2_pct']), tp2_levels, 2.0, 2.5)
    tp3 = _pick_target(current, float(cfg['tp3_pct']), tp3_levels, 3.5, 5.0)
    risk = current - stop
    rr1, rr2, rr3 = (tp1 - current) / risk, (tp2 - current) / risk, (tp3 - current) / risk
    if rr1 < float(cfg['min_rr_tp1']):
        return None, _rejection(symbol, 'RR_BELOW_MINIMUM'), diagnostics
    if not stop < current < tp1 < tp2 < tp3:
        return None, _rejection(symbol, 'INVALID_LEVEL_ORDER'), diagnostics

    half = float(cfg['entry_zone_half_width_pct']) / 100.0
    plan = {
        'direction': 'LONG', 'setup_type': 'SCALP_SUPERTREND',
        'setup_label': 'SuperTrend scalp continuation (15m)',
        'status': 'READY', 'entry_mid': current,
        'entry_zone': [current * (1.0 - half), current * (1.0 + half)],
        'stop_loss': stop, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
        'invalidation_level': stop,
        'primary_timeframe': '15m',
        'confluences': ['15m SuperTrend', '1H SuperTrend', '15m EMA200', trigger],
    }
    score, parts = _agent_score(state, ticker, extension, trigger, rr1)
    if score < int(cfg['min_score']):
        rej = _rejection(symbol, 'SCORE_BELOW_MINIMUM')
        rej['score'] = score
        return None, rej, diagnostics

    pair = symbol[:-4] + '/USDT' if symbol.endswith('USDT') else symbol
    trigger_ar = 'ثبات أعلى الاختراق' if trigger == 'BREAKOUT_HOLD' else 'ارتداد مؤكد من خط SuperTrend'
    trigger_en = 'held breakout' if trigger == 'BREAKOUT_HOLD' else 'confirmed SuperTrend bounce'
    signal = {
        'opportunity_id': f"{symbol}_LONG_SCALP_SUPERTREND_{now_iso[:16].replace(':', '')}",
        'symbol': symbol, 'pair': pair, 'direction': 'LONG',
        'setup_type': 'SCALP_SUPERTREND',
        'setup_label': 'SuperTrend scalp continuation (15m)',
        'status': 'READY', 'decision': 'FAVORABLE',
        'score': score, 'grade': grade(score),
        'current_price': _round_price(current),
        'entry_mid': _round_price(current),
        'entry_zone': [_round_price(plan['entry_zone'][0]), _round_price(plan['entry_zone'][1])],
        'stop_loss': _round_price(stop),
        'tp1': _round_price(tp1), 'tp2': _round_price(tp2), 'tp3': _round_price(tp3),
        'rr_tp1': round(rr1, 2), 'rr_tp2': round(rr2, 2), 'rr_tp3': round(rr3, 2),
        'sl_distance_pct': round(stop_pct, 2),
        'profit_pct_tp1': round((tp1 - current) / current * 100, 2),
        'profit_pct_tp2': round((tp2 - current) / current * 100, 2),
        'profit_pct_tp3': round((tp3 - current) / current * 100, 2),
        'primary_timeframe': '15m', 'timeframes': ['15m', '1h', '4h', '1d'],
        'supertrend_status': {'15m': 'UP', '1h': 'UP', '4h': 'UP'},
        'ema200_status': {'15m': 'ABOVE', '1h': 'ABOVE', '4h': 'ABOVE'},
        'score_breakdown': parts, 'score_breakdown_labels': AGENT_SCORE_LABELS,
        'reason': {
            'ar': f"SuperTrend صاعد على 15د و1س و4س، والسعر فوق EMA200 مع {trigger_ar} دون مطاردة امتداد سعري.",
            'en': f"SuperTrend is bullish on 15m, 1h and 4h; price is above EMA200 with a {trigger_en} and no excessive extension.",
        },
        'sl_reason': {
            'ar': 'الوقف أسفل أقرب دعم صالح من خط SuperTrend وقيعان آخر شمعتين صاعدتين مع هامش ATR صغير.',
            'en': 'The stop sits below the nearest valid SuperTrend/two-bullish-candle support with a small ATR buffer.',
        },
        'tp_reason': {
            'ar': 'الأهداف سريعة ضمن نطاقات الاستراتيجية، مع تفضيل مقاومة هيكلية عندما تقع داخل النطاق المحدد.',
            'en': 'Targets use the configured scalp bands, preferring a structural resistance when it falls inside the band.',
        },
        'risk_notes': {
            'ar': ['إشارة مضاربة قصيرة الأجل؛ ألغِ الفكرة عند كسر الوقف.', 'الدرجة ليست احتمالًا مضمونًا للربح.'],
            'en': ['Short-term scalp signal; invalidate it if the stop breaks.', 'The score is not a guaranteed probability of profit.'],
        },
        'invalidation_level': _round_price(stop),
        'invalidation_reason': {
            'ar': f"تُلغى الفكرة عند إغلاق شمعة 15 دقيقة أسفل {_round_price(stop)}.",
            'en': f"The idea is invalidated by a 15-minute close below {_round_price(stop)}.",
        },
        'confirmation': {
            'ar': 'الشروط الميكانيكية مكتملة والسعر داخل منطقة الدخول وقت المسح.',
            'en': 'Mechanical conditions pass and price is inside the entry zone at scan time.',
        },
        'execution_note': {
            'ar': 'الفرصة مستوفية للشروط حاليًا؛ التزم بالوقف ولا تطارد السعر إذا غادر منطقة الدخول.',
            'en': 'Conditions currently pass; respect the stop and do not chase price outside the entry zone.',
        },
        'diagnostics': diagnostics,
        'change_24h': round(float(ticker.get('chg24') or 0), 3),
        'quote_volume_24h': round(float(ticker.get('quoteVol') or 0), 2),
        'spread_pct': round(float(ticker.get('spread') or 0), 5),
        'data_timestamp': now_iso,
    }
    return signal, None, diagnostics


def _no_opportunity(rejections, market):
    if market.get('new_setups_gated'):
        return {
            'ar': 'لا توجد إشارات جديدة لأن اتساع السوق دون الحد الوقائي.',
            'en': 'No new signals: market breadth is below the protective gate.',
        }
    counts = Counter(code for r in rejections for code in r.get('codes', []))
    if not counts:
        return {'ar': 'لا توجد مرشحات مكتملة البيانات في هذه الدورة.', 'en': 'No complete candidates were available in this cycle.'}
    code, _ = counts.most_common(1)[0]
    ar, en = REJECTION_TEXT.get(code, REJECTION_TEXT['MISSING_DATA'])
    return {'ar': f"لا توجد فرصة مطابقة حاليًا. السبب الأبرز: {ar}",
            'en': f"No qualifying opportunity now. Main reason: {en}"}


def run_quant_agent(candidates, intraday, daily, market, settings, now_iso):
    """Evaluate pipeline candidates and return the strict dashboard document."""
    cfg = _cfg(settings)
    signals, rejections, errors = [], [], []
    candidate_rows = list(candidates)
    gate_min = float(settings.get('market_filter', {}).get('min_breadth_pct', 0))
    gated = bool(market.get('new_setups_gated')) or (
        settings.get('market_filter', {}).get('enabled') and
        float(market.get('breadth_pct_above_ema50') or 0) < gate_min
    )

    if not cfg.get('enabled', True):
        status = 'ok'
        no_reason = {'ar': 'الوكيل الكمي معطل من الإعدادات.', 'en': 'The quantitative agent is disabled in settings.'}
    elif gated:
        for symbol, _ in candidate_rows:
            rejections.append(_rejection(symbol, 'MARKET_BREADTH_GATE'))
        status = 'ok'
        no_reason = _no_opportunity(rejections, market)
    else:
        for symbol, ticker in candidate_rows:
            try:
                raw = intraday.get(symbol) or {}
                frames = {'15m': raw.get('15m'), '1h': raw.get('1h'), '4h': raw.get('4h'), '1d': daily.get(symbol)}
                signal, rejection, _ = evaluate_candidate(symbol, ticker, frames, settings, now_iso)
                if signal:
                    signals.append(signal)
                elif rejection:
                    rejections.append(rejection)
            except Exception as exc:  # one bad symbol must never break the cycle
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
        signals.sort(key=lambda s: (-s['score'], -s['rr_tp1']))
        signals = signals[:int(cfg['max_signals'])]
        status = 'degraded' if errors else 'ok'
        no_reason = None if signals else _no_opportunity(rejections, market)

    return {
        'schema_version': SCHEMA_VERSION,
        'scan_timestamp': now_iso,
        'source_data_timestamp': now_iso,
        'status': status,
        'market': {
            'status': market.get('status', 'NEUTRAL'),
            'breadth_pct_above_ema50': market.get('breadth_pct_above_ema50'),
            'new_setups_gated': gated,
        },
        'total_scanned': len(candidate_rows),
        'opportunities_found': len(signals),
        'signals': signals,
        'rejections': rejections,
        'no_opportunity_reason': no_reason,
        'config': cfg,
        'errors': errors[-20:],
    }
