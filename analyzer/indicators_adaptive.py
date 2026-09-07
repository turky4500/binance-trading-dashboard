# -*- coding: utf-8 -*-
"""
AI Adaptive indicator — inspired by Quantzee & Lorentzian Classification.
Uses KNN-based classification, adaptive EMAs, and multi-timeframe confirmation.
"""
import numpy as np
import pandas as pd
from .indicators import ema, rsi, macd, atr, supertrend


def _adaptive_ema(df, period=20):
    """Adaptive EMA that adjusts its smoothing based on volatility."""
    c = df['c']
    # Calculate efficiency ratio (ER)
    change = (c - c.shift(period)).abs()
    volatility = c.diff().abs().rolling(period).sum()
    er = change / volatility.replace(0, np.nan)
    er = er.fillna(0.5)

    # Adaptive smoothing constant
    fast_sc = 2 / (2 + 1)
    slow_sc = 2 / (30 + 1)
    sc = er * (fast_sc - slow_sc) + slow_sc

    # Compute adaptive EMA
    result = pd.Series(index=c.index, dtype=float)
    result.iloc[0] = c.iloc[0]
    for i in range(1, len(c)):
        if pd.isna(sc.iloc[i]):
            result.iloc[i] = result.iloc[i - 1]
        else:
            result.iloc[i] = c.iloc[i] * sc.iloc[i] + result.iloc[i - 1] * (1 - sc.iloc[i])
    return result


def _knn_classify(features, labels, new_point, k=5):
    """K-Nearest Neighbors classifier.
    features: array of feature vectors
    labels: array of labels (1=long, -1=short, 0=neutral)
    new_point: feature vector to classify
    k: number of neighbors
    Returns: classification (1, -1, 0) and confidence (0-100)."""
    if len(features) < k:
        return 0, 30

    # Euclidean distance
    distances = np.sqrt(np.sum((features - new_point) ** 2, axis=1))
    nearest_indices = np.argsort(distances)[:k]
    nearest_labels = labels[nearest_indices]
    nearest_distances = distances[nearest_indices]

    # Weighted voting (closer neighbors have more weight)
    weights = 1 / (nearest_distances + 1e-10)
    weighted_sum = np.sum(nearest_labels * weights)
    total_weight = np.sum(weights)

    if total_weight <= 0:
        return 0, 20

    score = weighted_sum / total_weight
    confidence = min(100, int(abs(score) * 100))

    if score > 0.3:
        return 1, confidence
    elif score < -0.3:
        return -1, confidence
    else:
        return 0, confidence


def _build_features(df, idx):
    """Build a feature vector for KNN classification at a given index.
    Features: [RSI, MACD_hist, EMA_position, ATR%, Volume_ratio, Price_change]."""
    n = len(df)
    if idx < 5 or idx >= n:
        return None

    try:
        c = df['c'].values
        rsi_val = float(df['rsi'].iloc[idx]) if 'rsi' in df else 50.0
        macd_h = float(df['macd_h'].iloc[idx]) if 'macd_h' in df else 0.0
        ema20 = float(df['ema20'].iloc[idx]) if 'ema20' in df else c[idx]
        ema50 = float(df['ema50'].iloc[idx]) if 'ema50' in df else c[idx]
        atr_val = float(df['atr'].iloc[idx]) if 'atr' in df else 1.0
        vol_ratio = float(df['vol_ratio'].iloc[idx]) if 'vol_ratio' in df else 1.0

        # Derived features
        ema_position = (c[idx] - ema20) / (ema20 + 1e-10)  # relative to EMA20
        atr_pct = atr_val / (c[idx] + 1e-10) * 100
        price_change = (c[idx] - c[idx - 1]) / (c[idx - 1] + 1e-10) * 100

        return np.array([rsi_val / 100, macd_h * 1000, ema_position, atr_pct, vol_ratio / 3, price_change])
    except Exception:
        return None


def _build_labels(df, forward_bars=5):
    """Build training labels based on future price action.
    1 = price went up significantly, -1 = price went down, 0 = neutral."""
    n = len(df)
    labels = np.zeros(n)
    c = df['c'].values
    atr_val = float(df['atr'].iloc[-1]) if 'atr' in df else 1.0

    for i in range(n - forward_bars):
        future_return = (c[i + forward_bars] - c[i]) / (c[i] + 1e-10)
        if future_return > 0.005:  # > 0.5% up
            labels[i] = 1
        elif future_return < -0.005:  # > 0.5% down
            labels[i] = -1
        else:
            labels[i] = 0

    return labels


def analyze_adaptive(df, timeframe='4h'):
    """Full AI adaptive analysis for a single timeframe."""
    n = len(df)
    if n < 200:
        return {'timeframe': timeframe, 'score': 0, 'bias': 'NEUTRAL', 'confidence': 0}

    # 1. Adaptive EMA
    ada_ema = _adaptive_ema(df, 20)
    ada_ema_val = float(ada_ema.iloc[-1])
    ada_ema_prev = float(ada_ema.iloc[-2])
    close = float(df['c'].iloc[-1])

    # 2. KNN Classification
    features = []
    labels_list = []
    for i in range(200, n):
        feat = _build_features(df, i)
        if feat is not None:
            features.append(feat)

    if len(features) < 50:
        return {'timeframe': timeframe, 'score': 0, 'bias': 'NEUTRAL', 'confidence': 20}

    features = np.array(features)
    labels_arr = _build_labels(df)

    # Use last 200 points for training, current point for prediction
    train_features = features[-200:-1]
    train_labels = labels_arr[-200:-1]
    current_feat = features[-1] if len(features) > 0 else None

    knn_class, knn_confidence = 0, 20
    if current_feat is not None:
        knn_class, knn_confidence = _knn_classify(train_features, train_labels, current_feat, k=5)

    # 3. Multi-confirmation score
    score = 0
    rsi_val = float(df['rsi'].iloc[-1])
    macd_h = float(df['macd_h'].iloc[-1])
    macd_h_prev = float(df['macd_h'].iloc[-2]) if n > 1 else 0
    st_line, st_dir = supertrend(df)
    st_direction = int(st_dir.iloc[-1])

    # Adaptive EMA direction: +15
    if close > ada_ema_val and ada_ema_val > ada_ema_prev:
        score += 15
    elif close < ada_ema_val and ada_ema_val < ada_ema_prev:
        score += 0
    else:
        score += 7

    # KNN vote: +25
    if knn_class == 1:
        score += 25
    elif knn_class == -1:
        score += 0
    else:
        score += 12

    # SuperTrend alignment: +15
    if st_direction == 1:
        score += 15
    elif st_direction == -1:
        score += 0
    else:
        score += 7

    # MACD: +15
    if macd_h > 0 and macd_h > macd_h_prev:
        score += 15
    elif macd_h < 0 and macd_h < macd_h_prev:
        score += 0
    elif macd_h > 0:
        score += 8
    else:
        score += 3

    # RSI: +10
    if 40 <= rsi_val <= 65:
        score += 10
    elif 65 < rsi_val <= 80:
        score += 5
    elif rsi_val > 80:
        score += 2
    elif 25 <= rsi_val < 40:
        score += 5
    else:
        score += 2

    # EMA alignment: +10
    ema20 = float(df['ema20'].iloc[-1])
    ema50 = float(df['ema50'].iloc[-1])
    ema200 = float(df['ema200'].iloc[-1])
    if close > ema20 > ema50 > ema200:
        score += 10
    elif close < ema20 < ema50 < ema200:
        score += 0
    else:
        score += 5

    # Volume: +10
    vol_ratio = float(df['vol_ratio'].iloc[-1])
    if vol_ratio >= 1.5:
        score += 10
    elif vol_ratio >= 1.0:
        score += 5
    else:
        score += 2

    # Determine bias
    if score >= 60:
        bias = 'BULLISH'
    elif score <= 40:
        bias = 'BEARISH'
    else:
        bias = 'NEUTRAL'

    return {
        'timeframe': timeframe,
        'score': min(100, score),
        'bias': bias,
        'confidence': knn_confidence,
        'adaptive_ema': round(ada_ema_val, 8),
        'knn_class': knn_class,
        'knn_confidence': knn_confidence,
        'knn_vote': 'LONG' if knn_class == 1 else ('SHORT' if knn_class == -1 else 'NEUTRAL'),
    }


def analyze_adaptive_multi(tf_dict):
    """Analyze adaptive signals across multiple timeframes."""
    results = {}
    total_score = 0
    count = 0

    for tf_name, df in tf_dict.items():
        r = analyze_adaptive(df, tf_name)
        results[tf_name] = r
        total_score += r['score']
        count += 1

    avg_score = total_score // count if count else 0

    # Weighted bias
    weights = {'15m': 1, '1h': 2, '4h': 3, '1d': 4}
    weighted_long = 0
    weighted_short = 0
    total_weight = 0
    for tf_name, r in results.items():
        w = weights.get(tf_name, 1)
        total_weight += w
        if r['bias'] == 'BULLISH':
            weighted_long += w
        elif r['bias'] == 'BEARISH':
            weighted_short += w

    if weighted_long > weighted_short:
        overall_bias = 'BULLISH'
    elif weighted_short > weighted_long:
        overall_bias = 'BEARISH'
    else:
        overall_bias = 'NEUTRAL'

    return {
        'overall_bias': overall_bias,
        'overall_score': avg_score,
        'per_timeframe': results,
    }
