# -*- coding: utf-8 -*-
"""
Deterministic natural-language explanations (English + Arabic).
Templates are filled with REAL computed values only — nothing is invented.
"""
from .indicators import fmt_price


def _p(x):
    return fmt_price(x)


def reason_entry(tf, plan, symbol):
    t4, t1, td = tf['4h'], tf['1h'], tf['1d']
    d = plan['direction']
    z = plan['entry_zone']
    if plan['setup_type'] == 'PULLBACK':
        en = (f"{symbol} is in a daily uptrend (close above EMA20/50, RSI {td['rsi']:.0f}, MACD positive). "
              f"The 4H trend is intact above EMA50, and the 1H RSI cooled to {t1['rsi']:.0f}. "
              f"Entry zone {_p(z[0])}-{_p(z[1])} sits on the confluence of {', '.join(plan['confluences']) or '4H EMA20'}.")
        ar = (f"{symbol} في اتجاه صاعد يومي (إغلاق فوق EMA20/50 وRSI عند {td['rsi']:.0f} وMACD موجب). "
              f"الاتجاه على 4 ساعات سليم فوق EMA50، ومؤشر RSI على الساعة برد إلى {t1['rsi']:.0f}. "
              f"منطقة الدخول {_p(z[0])}-{_p(z[1])} تقع على التقاء {', '.join(plan['confluences']) or 'EMA20 على 4 ساعات'}.")
    elif plan['setup_type'] == 'VWAP_HOLD':
        en = (f"{symbol} is in a strong uptrend and holding the session VWAP ({_p(t4['vwap'])}) well above the 4H EMA20. "
              f"Daily RSI {td['rsi']:.0f}, 1H RSI cooled to {t1['rsi']:.0f}. Entry zone {_p(z[0])}-{_p(z[1])} is a "
              f"momentum-continuation buy at VWAP support with a stop under the recent 1H swing low.")
        ar = (f"{symbol} في اتجاه صاعد قوي ويتماسك فوق VWAP الجلسة ({_p(t4['vwap'])}) بوضوح فوق EMA20 على 4 ساعات. "
              f"RSI اليومي {td['rsi']:.0f} وRSI الساعة برد إلى {t1['rsi']:.0f}. منطقة الدخول {_p(z[0])}-{_p(z[1])} "
              f"شراء استمرار زخم عند دعم VWAP مع وقف تحت آخر قاع على الساعة.")
    elif plan['setup_type'] == 'BREAKOUT_RETEST':
        en = (f"{symbol} broke above the 4H resistance {_p(plan['invalidation_level'] + tf['4h']['atr'])} on strong volume, "
              f"then pulled back into the retest zone {_p(z[0])}-{_p(z[1])}. "
              f"Buying the retest of a confirmed breakout offers a well-defined stop below the breakout level.")
        ar = (f"{symbol} اخترق مقاومة 4 ساعات {_p(plan['invalidation_level'] + tf['4h']['atr'])} بحجم قوي، ثم تراجع إلى منطقة إعادة الاختبار {_p(z[0])}-{_p(z[1])}. "
              f"الشراء عند إعادة اختبار اختراق مؤكد يمنح وقفًا واضحًا تحت مستوى الاختراق.")
    elif plan['setup_type'] == 'BREAKDOWN_RETEST':
        en = (f"{symbol} broke below the 4H support {_p(plan['invalidation_level'] - tf['4h']['atr'])} on strong volume, "
              f"then rallied back into the retest zone {_p(z[0])}-{_p(z[1])}. Shorting the retest gives a stop above the breakdown level.")
        ar = (f"{symbol} كسر دعم 4 ساعات {_p(plan['invalidation_level'] - tf['4h']['atr'])} بحجم قوي، ثم ارتد إلى منطقة إعادة الاختبار {_p(z[0])}-{_p(z[1])}. "
              f"البيع عند إعادة اختبار كسر مؤكد يمنح وقفًا فوق مستوى الكسر.")
    else:
        en = (f"{symbol} is in a downtrend on the daily and 4H charts. Price rallied back to the 4H EMA20 rejection zone "
              f"{_p(z[0])}-{_p(z[1])} with 1H RSI at {t1['rsi']:.0f} — a trend-continuation short entry.")
        ar = (f"{symbol} في اتجاه هابط على اليومي و4 ساعات. عاد السعر إلى منطقة رفض EMA20 على 4 ساعات "
              f"{_p(z[0])}-{_p(z[1])} مع RSI على الساعة عند {t1['rsi']:.0f} — نقطة بيع لاستمرار الاتجاه.")
    return {'en': en, 'ar': ar}


def reason_sl(plan, tf):
    atr = tf['4h']['atr']
    d = abs(plan['entry_mid'] - plan['stop_loss']) / atr
    en = (f"Stop placed at {_p(plan['stop_loss'])} — {d:.1f}x the 4H ATR ({atr:.4f}) from entry, "
          f"below the recent structural {'low' if plan['direction']=='LONG' else 'high'} "
          f"and under the {', '.join(plan['confluences']) or 'key level'}. "
          f"The margin absorbs normal market noise so the stop is not hit by random swings.")
    ar = (f"الوقف عند {_p(plan['stop_loss'])} — مسافة {d:.1f} ضعف ATR على 4 ساعات ({atr:.4f}) من الدخول، "
          f"تحت آخر {'قاع' if plan['direction']=='LONG' else 'قمة'} هيكلي وتحت {', '.join(plan['confluences']) or 'المستوى الرئيسي'}. "
          f"الهامش يمتص ضجيج السوق الطبيعي حتى لا يُضرب الوقف بتذبذب عشوائي.")
    return {'en': en, 'ar': ar}


def reason_tp(plan, tf):
    entry = plan['entry_mid']
    R = abs(entry - plan['stop_loss'])
    en = (f"Targets are multiples of the trade risk R={_p(R)}: TP1 = 1.5R at {_p(plan['tp1'])}, "
          f"TP2 = 2.5R at {_p(plan['tp2'])}, TP3 = 4R at {_p(plan['tp3'])}, "
          f"each snapped to nearby structure (swing highs/lows and round numbers).")
    ar = (f"الأهداف مضاعفات من مخاطرة الصفقة R={_p(R)}: TP1 عند 1.5R = {_p(plan['tp1'])}، "
          f"TP2 عند 2.5R = {_p(plan['tp2'])}، TP3 عند 4R = {_p(plan['tp3'])}، "
          f"وكل هدف مثبت على أقرب مستوى هيكلي (قمم/قيعان وأرقام مستديرة).")
    return {'en': en, 'ar': ar}


def reason_invalidation(plan):
    lvl = plan['invalidation_level']
    if plan['direction'] == 'LONG':
        en = (f"The idea is invalidated if a 4H candle closes below {_p(lvl)} — that breaks the "
              f"higher-low structure / breakout level and the setup must be abandoned.")
        ar = (f"تُلغى الفكرة إذا أغلقت شمعة 4 ساعات تحت {_p(lvl)} — هذا يكسر هيكل القيعان الصاعدة / مستوى الاختراق ويجب التخلي عن الصفقة.")
    else:
        en = (f"The idea is invalidated if a 4H candle closes above {_p(lvl)} — the breakdown/trend "
              f"structure fails and the short must be abandoned.")
        ar = (f"تُلغى الفكرة إذا أغلقت شمعة 4 ساعات فوق {_p(lvl)} — يفشل هيكل الكسر/الاتجاه ويجب التخلي عن البيع.")
    return {'en': en, 'ar': ar}


def volume_note(tf, ticker):
    vr = tf['4h']['vol_ratio3']
    qv = (ticker.get('quoteVol') or 0) / 1e6
    if vr >= 1.5:
        en = (f"Volume is supportive: recent 4H candles average {vr:.1f}x their 20-bar norm, "
              f"with 24h quote volume ≈ ${qv:.1f}M.")
        ar = (f"الحجم داعم: متوسط آخر شموع 4 ساعات {vr:.1f} ضعف متوسط 20 شمعة، وحجم 24 ساعة ≈ {qv:.1f} مليون USDT.")
    else:
        en = (f"Volume is average: recent 4H candles at {vr:.1f}x their norm (24h quote volume ≈ ${qv:.1f}M). "
              f"Not a strong volume confirmation.")
        ar = (f"الحجم متوسط: آخر شموع 4 ساعات عند {vr:.1f} ضعف المتوسط (حجم 24 ساعة ≈ {qv:.1f} مليون USDT). بدون تأكيد حجم قوي.")
    return {'en': en, 'ar': ar}


def momentum_note(tf, direction):
    t4, t1 = tf['4h'], tf['1h']
    en = (f"Momentum check — 4H: RSI {t4['rsi']:.0f}, MACD histogram {'positive' if t4['macd_h']>=0 else 'negative'} "
          f"({'rising' if t4['macd_h'] > t4['macd_h_prev'] else 'falling'}); 1H: RSI {t1['rsi']:.0f}, "
          f"MACD histogram {'positive' if t1['macd_h']>=0 else 'negative'}.")
    ar = (f"فحص الزخم — 4 ساعات: RSI {t4['rsi']:.0f}، MACD {'موجب' if t4['macd_h']>=0 else 'سالب'} "
          f"({'صاعد' if t4['macd_h'] > t4['macd_h_prev'] else 'هابط'}); ساعة: RSI {t1['rsi']:.0f}، MACD {'موجب' if t1['macd_h']>=0 else 'سالب'}.")
    return {'en': en, 'ar': ar}
