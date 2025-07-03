import pandas as pd

# 📌 RSI
def compute_rsi(series, period=None):
    if period is None:
        from main import strategy_params
        period = strategy_params['rsi_period']
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
