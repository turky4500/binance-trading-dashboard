# -*- coding: utf-8 -*-
"""Deterministic multi-timeframe SuperTrend opportunity agent.

The agent reuses candles already fetched by the Python pipeline.  Each symbol
is evaluated independently on 15m, 1h and 4h, with confirmation from higher
timeframes.  It emits strict JSON for the static dashboard and never contacts a
secret-bearing service or places an order.
"""
from collections import Counter

from .indicators import enrich, swings
from .scoring import grade
from .signal import tf_state

SCHEMA_VERSION = "scalp-supertrend-1.1"

TIMEFRAME_CHAINS = {
    # execution frame -> confirmation frame(s)
    '15m': ('15m', '1h', '4h'),
    '1h': ('1h', '4h', '1d'),
    '4h': ('4h', '1d'),
}
TIMEFRAME_AR = {'15m': '15 دقيقة', '1h': 'ساعة', '4h': '4 ساعات', '1d': 'يومي'}

AGENT_SCORE_LABELS = {
    'trend_alignment': 'Trend & SuperTrend Alignment',
    'price_action': 'Price Action Quality',
    'entry_quality': 'EMA200 Extension / Entry Quality',
    'risk_reward': 'Risk / Reward',
    'momentum': 'Momentum',
    'volume': 'Volume',
    'liquidity': 'Liquidity',
}

REJECTION_TEXT = {
    "MISSING_DATA": ("بيانات الشموع أو المؤشرات غير مكتملة.", "Candle or indicator data is incomplete."),
    "MARKET_BREADTH_GATE": ("اتساع السوق دون الحد المطلوب لنشر فرصة جديدة.", "Market breadth is below the new-signal gate."),
    "EMA200_NOT_CONFIRMED": ("السعر غير ثابت فوق EMA200 على إطار التنفيذ وإطار التأكيد.", "Price is not confirmed above EMA200 on the execution and confirmation frames."),
    "EMA200_OVEREXTENDED": ("السعر مبتعد أكثر من المسموح عن EMA200.", "Price is overextended from EMA200."),
    "LIVE_PRICE_CHASE": ("السعر اللحظي اندفع بعيدًا عن آخر شمعة مغلقة؛ لا تطارد الحركة.", "Live price ran too far beyond the last closed candle; do not chase it."),
    "HTF_TREND_CONFLICT": ("اتجاه الإطار الأعلى يتعارض مع صفقة LONG.", "The higher-timeframe trend conflicts with a LONG setup."),
    "SUPERTREND_BEARISH": ("SuperTrend ليس صاعدًا على إطار التنفيذ وأطر التأكيد المطلوبة.", "SuperTrend is not bullish on the execution and required confirmation frames."),
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
    "timeframes": ["15m", "1h", "4h"],
    "min_score": 82,
    "max_signals": 12,
    "max_signals_per_timeframe": 4,
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
    out['timeframes'] = [tf for tf in out.get('timeframes', []) if tf in TIMEFRAME_CHAINS]
    if not out['timeframes']:
        out['timeframes'] = list(DEFAULTS['timeframes'])
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


def _prepare_frames(frames, settings):
    """Calculate each timeframe once, even though it can feed several scans."""
    stp = settings.get('supertrend', {'period': 10, 'multiplier': 3.0})
    enriched = {name: _closed_enriched(frames.get(name), stp) for name in ('15m', '1h', '4h', '1d')}
    states = {}
    for name, df in enriched.items():
        if df is not None:
            states[name] = tf_state(df, k=2 if name in ('15m', '1h') else 3)
    return enriched, states


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
    """Prefer a structural level inside the target band; otherwise use its midpoint."""
    lo = entry * (1.0 + lo_pct / 100.0)
    hi = entry * (1.0 + hi_pct / 100.0)
    raw = entry * (1.0 + pct / 100.0)
    valid = [float(v) for v in levels if v and lo <= float(v) <= hi]
    return min(valid, key=lambda v: abs(v - raw)) if valid else raw


def _rejection(symbol, code, primary_timeframe=None):
    ar, en = REJECTION_TEXT[code]
    result = {"symbol": symbol, "codes": [code], "reason_ar": ar, "reason_en": en}
    if primary_timeframe:
        result['primary_timeframe'] = primary_timeframe
    return result


def _agent_score(states, ticker, extension, trigger, rr1, primary, confirmation):
    """100-point score calibrated to the selected execution timeframe."""
    # Alignment is mandatory before scoring, so a fully aligned chain receives
    # the 25 trend points. Remaining components rank valid candidates.
    trend = 25
    price_action = 20 if trigger == 'BREAKOUT_HOLD' else 18
    entry_quality = 15 if extension <= 2.0 else (12 if extension <= 3.5 else 8)
    risk_reward = 15 if rr1 >= 2.0 else (13 if rr1 >= 1.75 else 11)

    momentum = 0
    momentum += 4 if 45 <= states[primary]['rsi'] <= 72 else 0
    momentum += 3 if 45 <= states[confirmation]['rsi'] <= 72 else 0
    momentum += 1.5 if states[primary]['macd_h'] > 0 else 0
    momentum += 1.5 if states[confirmation]['macd_h'] > 0 else 0

    vr = states[primary]['vol_ratio3']
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


def evaluate_candidate(symbol, ticker, frames, settings, now_iso,
                       primary_timeframe='15m', prepared=None):
    """Evaluate one symbol on one execution timeframe.

    ``primary_timeframe`` is one of 15m/1h/4h.  Each scan uses the next higher
    frame(s) from ``TIMEFRAME_CHAINS`` for trend confirmation.
    """
    cfg = _cfg(settings)
    if primary_timeframe not in TIMEFRAME_CHAINS:
        return None, _rejection(symbol, 'MISSING_DATA', primary_timeframe), None
    chain = TIMEFRAME_CHAINS[primary_timeframe]
    primary, confirmation = chain[0], chain[1]
    context = chain[2] if len(chain) > 2 else None
    enriched, states = prepared if prepared is not None else _prepare_frames(frames, settings)
    if not all(enriched.get(tf) is not None and tf in states for tf in chain):
        return None, _rejection(symbol, 'MISSING_DATA', primary), None

    p_df = enriched[primary]
    p_state = states[primary]
    current = float(ticker.get('last') or p_state['close'])
    ema200 = p_state['ema200']
    extension = (current - ema200) / ema200 * 100 if ema200 > 0 else 999.0
    live_chase_atr = ((current - p_state['close']) / p_state['atr'] if p_state['atr'] > 0 else 999.0)
    diagnostics = {
        'primary_timeframe': primary,
        'confirmation_timeframes': list(chain[1:]),
        'ema200_distance_pct': round(extension, 3),
        'live_chase_atr': round(live_chase_atr, 3),
        'supertrend_last_3': ['UP' if int(v) == 1 else 'DOWN' for v in p_df['st_dir'].iloc[-3:]],
        'upper_wick_rejection': _upper_wick_rejection(p_df, cfg),
        'lower_highs_detected': _lower_highs(p_df),
    }

    # EMA200 must hold on execution + immediate confirmation.  An optional
    # third context frame must also remain bullish to avoid countertrend trades.
    if not (current > states[primary]['ema200'] and
            states[confirmation]['close'] > states[confirmation]['ema200']):
        return None, _rejection(symbol, 'EMA200_NOT_CONFIRMED', primary), diagnostics
    if extension > float(cfg['max_ema200_extension_pct']):
        return None, _rejection(symbol, 'EMA200_OVEREXTENDED', primary), diagnostics
    if live_chase_atr > float(cfg['max_live_chase_atr']):
        return None, _rejection(symbol, 'LIVE_PRICE_CHASE', primary), diagnostics
    if context and not (states[context]['close'] > states[context]['ema200'] and states[context]['st_dir'] == 1):
        return None, _rejection(symbol, 'HTF_TREND_CONFLICT', primary), diagnostics
    if not (all(states[tf]['st_dir'] == 1 for tf in chain) and current > states[primary]['st_line']):
        return None, _rejection(symbol, 'SUPERTREND_BEARISH', primary), diagnostics
    if _flip_count(p_df['st_dir'].iloc[-3:]) > 1:
        return None, _rejection(symbol, 'SUPERTREND_CHOP', primary), diagnostics
    if diagnostics['upper_wick_rejection']:
        return None, _rejection(symbol, 'UPPER_WICK_REJECTION', primary), diagnostics
    if diagnostics['lower_highs_detected']:
        return None, _rejection(symbol, 'LOWER_HIGHS', primary), diagnostics

    trigger_ok, trigger = _entry_trigger(p_df, cfg)
    diagnostics['entry_trigger'] = trigger
    if not trigger_ok:
        return None, _rejection(symbol, 'BREAKOUT_NOT_HOLDING', primary), diagnostics

    recent = p_df.tail(8)
    bullish = recent[recent['c'] > recent['o']].tail(2)
    if len(bullish) < 2:
        return None, _rejection(symbol, 'MISSING_DATA', primary), diagnostics
    structural_low = float(bullish['l'].min())
    support_anchor = max(float(states[primary]['st_line']), structural_low)
    stop = support_anchor - float(cfg['stop_buffer_atr']) * float(states[primary]['atr'])
    if not 0 < stop < current:
        return None, _rejection(symbol, 'INVALID_LEVEL_ORDER', primary), diagnostics
    stop_pct = (current - stop) / current * 100
    if stop_pct > float(cfg['max_stop_distance_pct']):
        return None, _rejection(symbol, 'STOP_TOO_WIDE', primary), diagnostics

    tp1 = current * (1.0 + float(cfg['tp1_pct']) / 100.0)
    tp2_levels = ([p for p, _ in states[primary]['sw_highs'][-8:]] +
                  [states[primary]['hi20'], states[confirmation]['last_high']])
    tp3_levels = ([p for p, _ in states[confirmation]['sw_highs'][-8:]] +
                  [states[confirmation]['hi50']])
    if context:
        tp3_levels.append(states[context]['last_high'])
    tp2 = _pick_target(current, float(cfg['tp2_pct']), tp2_levels, 2.0, 2.5)
    tp3 = _pick_target(current, float(cfg['tp3_pct']), tp3_levels, 3.5, 5.0)
    risk = current - stop
    rr1, rr2, rr3 = (tp1 - current) / risk, (tp2 - current) / risk, (tp3 - current) / risk
    if rr1 < float(cfg['min_rr_tp1']):
        return None, _rejection(symbol, 'RR_BELOW_MINIMUM', primary), diagnostics
    if not stop < current < tp1 < tp2 < tp3:
        return None, _rejection(symbol, 'INVALID_LEVEL_ORDER', primary), diagnostics

    score, parts = _agent_score(states, ticker, extension, trigger, rr1, primary, confirmation)
    if score < int(cfg['min_score']):
        rejection = _rejection(symbol, 'SCORE_BELOW_MINIMUM', primary)
        rejection['score'] = score
        return None, rejection, diagnostics

    half = float(cfg['entry_zone_half_width_pct']) / 100.0
    entry_zone = [current * (1.0 - half), current * (1.0 + half)]
    pair = symbol[:-4] + '/USDT' if symbol.endswith('USDT') else symbol
    trigger_ar = 'ثبات أعلى الاختراق' if trigger == 'BREAKOUT_HOLD' else 'ارتداد مؤكد من خط SuperTrend'
    trigger_en = 'held breakout' if trigger == 'BREAKOUT_HOLD' else 'confirmed SuperTrend bounce'
    chain_en = ', '.join(chain)
    chain_ar = ' و'.join(TIMEFRAME_AR[tf] for tf in chain)
    tf_tag = primary.upper()
    supertrend_status = {tf: 'UP' for tf in chain}
    ema200_status = {tf: 'ABOVE' for tf in chain}

    signal = {
        'opportunity_id': f"{symbol}_LONG_SUPERTREND_{tf_tag}_{now_iso[:16].replace(':', '')}",
        'symbol': symbol, 'pair': pair, 'direction': 'LONG',
        'setup_type': 'SCALP_SUPERTREND',
        'setup_label': f"SuperTrend opportunity ({primary})",
        'status': 'READY', 'decision': 'FAVORABLE',
        'score': score, 'grade': grade(score),
        'current_price': _round_price(current),
        'entry_mid': _round_price(current),
        'entry_zone': [_round_price(entry_zone[0]), _round_price(entry_zone[1])],
        'stop_loss': _round_price(stop),
        'tp1': _round_price(tp1), 'tp2': _round_price(tp2), 'tp3': _round_price(tp3),
        'rr_tp1': round(rr1, 2), 'rr_tp2': round(rr2, 2), 'rr_tp3': round(rr3, 2),
        'sl_distance_pct': round(stop_pct, 2),
        'profit_pct_tp1': round((tp1 - current) / current * 100, 2),
        'profit_pct_tp2': round((tp2 - current) / current * 100, 2),
        'profit_pct_tp3': round((tp3 - current) / current * 100, 2),
        'primary_timeframe': primary,
        'confirmation_timeframes': list(chain[1:]),
        'timeframes': list(chain),
        'supertrend_status': supertrend_status,
        'ema200_status': ema200_status,
        'score_breakdown': parts, 'score_breakdown_labels': AGENT_SCORE_LABELS,
        'reason': {
            'ar': f"فرصة على إطار {TIMEFRAME_AR[primary]}: SuperTrend صاعد على {chain_ar}، والسعر فوق EMA200 مع {trigger_ar} دون مطاردة سعرية.",
            'en': f"{primary} opportunity: SuperTrend is bullish on {chain_en}; price is above EMA200 with a {trigger_en} and no excessive extension.",
        },
        'sl_reason': {
            'ar': f"الوقف أسفل أقرب دعم صالح على إطار {TIMEFRAME_AR[primary]} من خط SuperTrend وقيعان آخر شمعتين صاعدتين مع هامش ATR صغير.",
            'en': f"The stop sits below the nearest valid {primary} SuperTrend/two-bullish-candle support with a small ATR buffer.",
        },
        'tp_reason': {
            'ar': 'الأهداف ضمن نطاقات الاستراتيجية، مع تفضيل مقاومة هيكلية عندما تقع داخل النطاق المحدد.',
            'en': 'Targets use the configured bands, preferring a structural resistance when it falls inside the band.',
        },
        'risk_notes': {
            'ar': [f"إشارة على إطار {TIMEFRAME_AR[primary]}؛ ألغِ الفكرة عند كسر الوقف.", 'الدرجة ليست احتمالًا مضمونًا للربح.'],
            'en': [f"{primary} signal; invalidate it if the stop breaks.", 'The score is not a guaranteed probability of profit.'],
        },
        'invalidation_level': _round_price(stop),
        'invalidation_reason': {
            'ar': f"تُلغى الفكرة عند إغلاق شمعة {TIMEFRAME_AR[primary]} أسفل {_round_price(stop)}.",
            'en': f"The idea is invalidated by a {primary} close below {_round_price(stop)}.",
        },
        'confirmation': {
            'ar': f"شروط إطار {TIMEFRAME_AR[primary]} وتأكيداته الأعلى مكتملة والسعر داخل منطقة الدخول وقت المسح.",
            'en': f"The {primary} conditions and higher-frame confirmations pass; price is inside the entry zone at scan time.",
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
    return {'ar': f"لا توجد فرصة مطابقة حاليًا على أطر 15 دقيقة والساعة و4 ساعات. السبب الأبرز: {ar}",
            'en': f"No qualifying 15m, 1h or 4h opportunity now. Main reason: {en}"}


def _limit_signals(signals, cfg):
    """Keep top signals while reserving room for every requested timeframe."""
    ordered = sorted(signals, key=lambda s: (-s['score'], -s['rr_tp1']))
    per_limit = int(cfg['max_signals_per_timeframe'])
    total_limit = int(cfg['max_signals'])
    counts = Counter()
    selected = []
    for signal in ordered:
        tf = signal['primary_timeframe']
        if counts[tf] >= per_limit or len(selected) >= total_limit:
            continue
        selected.append(signal)
        counts[tf] += 1
    return selected


def run_quant_agent(candidates, intraday, daily, market, settings, now_iso):
    """Evaluate every candidate independently on 15m, 1h and 4h."""
    cfg = _cfg(settings)
    scan_timeframes = list(cfg['timeframes'])
    signals, rejections, errors = [], [], []
    candidate_rows = list(candidates)
    gate_min = float(settings.get('market_filter', {}).get('min_breadth_pct', 0))
    gated = bool(market.get('new_setups_gated')) or (
        settings.get('market_filter', {}).get('enabled') and
        float(market.get('breadth_pct_above_ema50') or 0) < gate_min
    )
    total_evaluations = len(candidate_rows) * len(scan_timeframes)

    if not cfg.get('enabled', True):
        status = 'ok'
        no_reason = {'ar': 'الوكيل الكمي معطل من الإعدادات.', 'en': 'The quantitative agent is disabled in settings.'}
    elif gated:
        for symbol, _ in candidate_rows:
            for primary in scan_timeframes:
                rejections.append(_rejection(symbol, 'MARKET_BREADTH_GATE', primary))
        status = 'ok'
        no_reason = _no_opportunity(rejections, market)
    else:
        for symbol, ticker in candidate_rows:
            raw = intraday.get(symbol) or {}
            frames = {'15m': raw.get('15m'), '1h': raw.get('1h'),
                      '4h': raw.get('4h'), '1d': daily.get(symbol)}
            try:
                prepared = _prepare_frames(frames, settings)
            except Exception as exc:
                errors.append(f"{symbol}: prepare: {type(exc).__name__}: {exc}")
                for primary in scan_timeframes:
                    rejections.append(_rejection(symbol, 'MISSING_DATA', primary))
                continue
            for primary in scan_timeframes:
                try:
                    signal, rejection, _ = evaluate_candidate(
                        symbol, ticker, frames, settings, now_iso,
                        primary_timeframe=primary, prepared=prepared)
                    if signal:
                        signals.append(signal)
                    elif rejection:
                        rejections.append(rejection)
                except Exception as exc:  # one symbol/timeframe must never break the cycle
                    errors.append(f"{symbol} {primary}: {type(exc).__name__}: {exc}")
        signals = _limit_signals(signals, cfg)
        status = 'degraded' if errors else 'ok'
        no_reason = None if signals else _no_opportunity(rejections, market)

    by_timeframe = {tf: sum(1 for s in signals if s.get('primary_timeframe') == tf)
                    for tf in scan_timeframes}
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
        'timeframes_scanned': scan_timeframes,
        'symbols_scanned': len(candidate_rows),
        'total_scanned': total_evaluations,
        'opportunities_found': len(signals),
        'opportunities_by_timeframe': by_timeframe,
        'signals': signals,
        'rejections': rejections,
        'no_opportunity_reason': no_reason,
        'config': cfg,
        'errors': errors[-20:],
    }
