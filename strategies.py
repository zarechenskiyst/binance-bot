import pandas as pd
import numpy as np
from indicators import compute_rsi

# 📌 EMA + RSI
def ema_rsi_strategy(df):
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['RSI'] = compute_rsi(df['close'])
    latest = df.iloc[-1]
    if latest['close'] > latest['EMA20'] and latest['RSI'] < 70:
        return 'BUY'
    elif latest['close'] < latest['EMA20'] and latest['RSI'] > 30:
        return 'SELL'
    return None

# 📌 Bollinger + RSI
def bollinger_rsi_strategy(df):
    df['MA20'] = df['close'].rolling(window=20).mean()
    df['STD'] = df['close'].rolling(window=20).std()
    df['Upper'] = df['MA20'] + 2 * df['STD']
    df['Lower'] = df['MA20'] - 2 * df['STD']
    df['RSI'] = compute_rsi(df['close'])
    latest = df.iloc[-1]
    if latest['close'] < latest['Lower'] and latest['RSI'] < 30:
        return 'BUY'
    elif latest['close'] > latest['Upper'] and latest['RSI'] > 70:
        return 'SELL'
    return None

# 📌 MACD + EMA
def macd_ema_strategy(df):
    df['EMA12'] = df['close'].ewm(span=12).mean()
    df['EMA26'] = df['close'].ewm(span=26).mean()
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['Signal'] = df['MACD'].ewm(span=9).mean()
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    if prev['MACD'] < prev['Signal'] and latest['MACD'] > latest['Signal']:
        return 'BUY'
    elif prev['MACD'] > prev['Signal'] and latest['MACD'] < latest['Signal']:
        return 'SELL'
    return None

# 📌 VWAP + RSI
def vwap_rsi_strategy(df):
    q = df['volume']
    p = df['close']
    df['VWAP'] = (p * q).cumsum() / q.cumsum()
    df['RSI'] = compute_rsi(df['close'])
    latest = df.iloc[-1]
    if latest['close'] > latest['VWAP'] and latest['RSI'] < 70:
        return 'BUY'
    elif latest['close'] < latest['VWAP'] and latest['RSI'] > 30:
        return 'SELL'
    return None

# 📌 Stochastic RSI helper
def stochastic_rsi(close, period=14, smoothK=3, smoothD=3):
    rsi = compute_rsi(close, period)
    min_rsi = rsi.rolling(window=period).min()
    max_rsi = rsi.rolling(window=period).max()
    stoch_rsi = (rsi - min_rsi) / (max_rsi - min_rsi)
    k = stoch_rsi.rolling(window=smoothK).mean()
    d = k.rolling(window=smoothD).mean()
    return k, d

# 📌 MACD + Stochastic RSI
def macd_stochastic_strategy(df):
    df['EMA12'] = df['close'].ewm(span=12).mean()
    df['EMA26'] = df['close'].ewm(span=26).mean()
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['Signal'] = df['MACD'].ewm(span=9).mean()
    df['StochK'], df['StochD'] = stochastic_rsi(df['close'])
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    if (
        prev['MACD'] < prev['Signal'] and latest['MACD'] > latest['Signal']
        and prev['StochK'] < prev['StochD'] and latest['StochK'] > latest['StochD']
    ):
        return 'BUY'
    elif (
        prev['MACD'] > prev['Signal'] and latest['MACD'] < latest['Signal']
        and prev['StochK'] > prev['StochD'] and latest['StochK'] < latest['StochD']
    ):
        return 'SELL'
    return None

# 📌 Bollinger + Volume Spike
def bollinger_volume_strategy(df, volume_threshold=1.5):
    df['MA20'] = df['close'].rolling(window=20).mean()
    df['STD'] = df['close'].rolling(window=20).std()
    df['Upper'] = df['MA20'] + 2 * df['STD']
    df['Lower'] = df['MA20'] - 2 * df['STD']
    df['Volume_MA20'] = df['volume'].rolling(window=20).mean()
    latest = df.iloc[-1]
    volume_spike = latest['volume'] > volume_threshold * latest['Volume_MA20']
    if latest['close'] > latest['Upper'] and volume_spike:
        return 'SELL'
    elif latest['close'] < latest['Lower'] and volume_spike:
        return 'BUY'
    return None

# 📌 EMA50 / EMA200 crossover
def ema_crossover_strategy(df):
    df['EMA50'] = df['close'].ewm(span=50).mean()
    df['EMA200'] = df['close'].ewm(span=200).mean()
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    if prev['EMA50'] < prev['EMA200'] and latest['EMA50'] > latest['EMA200']:
        return 'BUY'
    elif prev['EMA50'] > prev['EMA200'] and latest['EMA50'] < latest['EMA200']:
        return 'SELL'
    return None
