# -*- coding: utf-8 -*-
"""
Unified Signal indicator — combines all 6 indicator modules into one
weighted voting system. Each indicator votes BULLISH/BEARISH/NEUTRAL
with a confidence weight. The final signal is the weighted consensus.
"""
from .indicators_smc import analyze_smc_multi
from .indicators_confluence import analyze_confluence_multi
from .indicators_momentum import analyze_momentum_multi
from .indicators_liquidity import analyze_liquidity_multi
from .indicators_adaptive import analyze_adaptive_multi
from .indicators_volume import analyze_volume_multi


# Weight of each indicator in the final vote (total = 100)
INDICATOR_WEIGHTS = {
    'smc': 25,         # Smart Money — strongest institutional signals
    'confluence': 20,  # Trend Confluence — multi-indicator alignment
    'momentum': 15,    # Momentum Pro — divergence & extremes
    'liquidity': 15,   # Liquidity Zones — where stops cluster
    'adaptive': 15,    # AI Adaptive — ML-based classification
    'volume': 10,      # Volume Profile — institutional levels
}


def analyze_unified(tf_dict):
    """Run all 6 indicators and combine into one unified signal.
    tf_dict: {timeframe: DataFrame} with OHLCV + enriched indicators.
    Returns a dict with overall signal and per-indicator breakdown."""
    # Run all indicators
    smc = analyze_smc_multi(tf_dict)
    confluence = analyze_confluence_multi(tf_dict)
    momentum = analyze_momentum_multi(tf_dict)
    liquidity = analyze_liquidity_multi(tf_dict)
    adaptive = analyze_adaptive_multi(tf_dict)
    volume = analyze_volume_multi(tf_dict)

    indicators = {
        'smc': {'name': 'Smart Money (SMC)', 'result': smc, 'weight': INDICATOR_WEIGHTS['smc']},
        'confluence': {'name': 'Trend Confluence', 'result': confluence, 'weight': INDICATOR_WEIGHTS['confluence']},
        'momentum': {'name': 'Momentum Pro', 'result': momentum, 'weight': INDICATOR_WEIGHTS['momentum']},
        'liquidity': {'name': 'Liquidity Zones', 'result': liquidity, 'weight': INDICATOR_WEIGHTS['liquidity']},
        'adaptive': {'name': 'AI Adaptive', 'result': adaptive, 'weight': INDICATOR_WEIGHTS['adaptive']},
        'volume': {'name': 'Volume Profile', 'result': volume, 'weight': INDICATOR_WEIGHTS['volume']},
    }

    # Weighted voting
    total_weight = 0
    bullish_weight = 0
    bearish_weight = 0

    for key, ind in indicators.items():
        bias = ind['result'].get('overall_bias', 'NEUTRAL')
        w = ind['weight']
        total_weight += w
        if bias == 'BULLISH':
            bullish_weight += w
        elif bias == 'BEARISH':
            bearish_weight += w

    # Calculate confidence (0-100)
    if total_weight > 0:
        if bullish_weight > bearish_weight:
            confidence = int((bullish_weight / total_weight) * 100)
            overall_bias = 'BULLISH'
        elif bearish_weight > bullish_weight:
            confidence = int((bearish_weight / total_weight) * 100)
            overall_bias = 'BEARISH'
        else:
            confidence = 50
            overall_bias = 'NEUTRAL'
    else:
        confidence = 0
        overall_bias = 'NEUTRAL'

    # Build indicator breakdown for display
    breakdown = []
    for key, ind in indicators.items():
        bias = ind['result'].get('overall_bias', 'NEUTRAL')
        score = ind['result'].get('overall_score', 0)
        breakdown.append({
            'key': key,
            'name': ind['name'],
            'bias': bias,
            'score': score,
            'weight': ind['weight'],
            'weighted_score': round(score * ind['weight'] / 100, 1),
        })

    # Top signals from all indicators
    all_top = []
    for key, ind in indicators.items():
        for sig in ind['result'].get('top_signals', []):
            sig['source'] = ind['name']
            all_top.append(sig)
    all_top.sort(key=lambda s: s.get('strength', 0), reverse=True)

    return {
        'overall_bias': overall_bias,
        'confidence': confidence,
        'bullish_weight': bullish_weight,
        'bearish_weight': bearish_weight,
        'total_weight': total_weight,
        'breakdown': breakdown,
        'top_signals': all_top[:15],
        'per_indicator': {k: v['result'] for k, v in indicators.items()},
    }


def score_symbol(tf_dict):
    """Quick score for a single symbol across all indicators.
    Returns a number 0-100 and the dominant bias."""
    result = analyze_unified(tf_dict)

    # Weighted average of individual scores
    total_score = 0
    total_weight = 0
    for item in result['breakdown']:
        total_score += item['weighted_score']
        total_weight += item['weight']

    avg_score = int(total_score) if total_weight > 0 else 50

    return {
        'score': min(100, avg_score),
        'bias': result['overall_bias'],
        'confidence': result['confidence'],
        'breakdown': result['breakdown'],
    }
