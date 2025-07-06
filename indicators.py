import pandas as pd

# 📌 RSI
def compute_rsi(series, period=None, params=None):
    if period is None:
        if params and 'rsi_period' in params:
            period = params['rsi_period']
        else:
            period = 14
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
