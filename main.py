from binance.client import Client
import pandas as pd
import numpy as np
import time
import requests
import os
from datetime import datetime, timedelta

# Telegram конфигурация
TELEGRAM_TOKEN = os.getenv("TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

# Статистика торговли
trade_log = []

# Время следующей отправки отчета
next_report_time = datetime.now() + timedelta(hours=3)

open_positions = {}  # Пример: {'BTCUSDT': {'side': 'BUY', 'entry_price': 30000.0, 'qty': 0.00033}}
TRADE_AMOUNT = 10    # Сумма сделки в USDT

# 🔑 API ключи с Binance Testnet
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binance.vision/api'

# 🔄 Торгуемые пары
symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'AVAXUSDT', 'PEPEUSDT']
interval = Client.KLINE_INTERVAL_5MINUTE
lookback = 100

def get_klines(symbol):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=lookback)
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df['close'] = df['close'].astype(float)
    return df

# 📈 Индикаторы

def ema_rsi_strategy(df):
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['RSI'] = compute_rsi(df['close'])
    latest = df.iloc[-1]
    if latest['close'] > latest['EMA20'] and latest['RSI'] < 70:
        return 'BUY'
    elif latest['close'] < latest['EMA20'] and latest['RSI'] > 30:
        return 'SELL'
    return None

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

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def execute_trade(symbol, signal):
    if symbol in open_positions:
        return  # уже есть открытая позиция

    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])
        qty = round(TRADE_AMOUNT / price, 5)

        if signal == 'BUY':
            client.order_limit_buy(symbol=symbol, quantity=qty, price=str(round(price, 2)))
        elif signal == 'SELL':
            client.order_limit_sell(symbol=symbol, quantity=qty, price=str(round(price, 2)))

        print(f"✅ {signal} ордер отправлен для {symbol} по {price}")

        open_positions[symbol] = {
            'side': signal,
            'entry_price': price,
            'qty': qty,
            'time': datetime.now()
        }

        trade_log.append({
            'symbol': symbol,
            'direction': signal,
            'amount': TRADE_AMOUNT,
            'entry_price': price,
            'timestamp': datetime.now(),
            'result': None,
            'profit': 0.0
        })

    except Exception as e:
        print(f"❌ Ошибка при торговле {symbol}: {e}")

def check_exit_conditions():
    symbols_to_close = []

    for symbol, pos in open_positions.items():
        try:
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            entry = pos['entry_price']
            side = pos['side']
            qty = pos['qty']

            change = (current_price - entry) / entry * 100
            if side == 'SELL':
                change = -change  # для коротких сделок переворачиваем знак

            if change >= 1.5 or change <= -1.0:
                close_side = 'SELL' if side == 'BUY' else 'BUY'
                if close_side == 'BUY':
                    client.order_market_buy(symbol=symbol, quantity=qty)
                else:
                    client.order_market_sell(symbol=symbol, quantity=qty)

                profit_usdt = round(TRADE_AMOUNT * change / 100, 2)
                result = 'win' if profit_usdt > 0 else 'loss'

                print(f"📤 Закрыта позиция по {symbol} — {result.upper()} ({change:.2f}%)")

                # Обновление лога
                for t in reversed(trade_log):
                    if t['symbol'] == symbol and t['result'] is None:
                        t['result'] = result
                        t['profit'] = profit_usdt
                        break

                symbols_to_close.append(symbol)

        except Exception as e:
            print(f"❌ Ошибка при закрытии {symbol}: {e}")

    for s in symbols_to_close:
        open_positions.pop(s)

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"❌ Ошибка при отправке Telegram: {e}")

def send_statistics():
    if not trade_log:
        send_telegram_message("📊 Пока нет сделок.")
        return

    total = len(trade_log)
    wins = sum(1 for t in trade_log if t['result'] == 'win')
    losses = sum(1 for t in trade_log if t['result'] == 'loss')
    total_amount = sum(t['amount'] for t in trade_log)
    total_profit = sum(t.get('profit', 0) for t in trade_log)
    open_trades = sum(1 for tin trade_log if t['result'] is None)

    message = (
        "📈 *Статистика за 3 часа*\n\n"
        f"Всего сделок: {total}\n"
        f"✅ Прибыльных: {wins}\n"
        f"❌ Убыточных: {losses}\n"
        f"🟡 Открытых: {open_trades}\n"
        f"💸 Поставлено: ${total_amount:.2f}\n"
        f"💰 Прибыль: ${total_profit:.2f}"
    )
    send_telegram_message(message)


# 🧠 Главный цикл
while True:
    print(f"\n🕒 Проверка сигналов... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for symbol in symbols:
        try:
            df = get_klines(symbol)
            if df is None or df.empty:
                continue

            ema_rsi = ema_rsi_strategy(df)
            boll_rsi = bollinger_rsi_strategy(df)
            macd = macd_ema_strategy(df)

          # Выбираем, какая стратегия дала сигнал
            signal = ema_rsi or boll_rsi or macd

            if signal:
                execute_trade(symbol, signal)

            print(f"📊 {symbol}:")
            print(f"  EMA + RSI: {ema_rsi}")
            print(f"  Bollinger + RSI: {boll_rsi}")
            print(f"  MACD + EMA: {macd}")

        except Exception as e:
            print(f"⚠️ Ошибка при обработке {symbol}: {e}")

  # Проверка времени отчета
    if datetime.now() >= next_report_time:
        send_statistics()
        next_report_time = datetime.now() + timedelta(hours=3)

    check_exit_conditions()
    
    time.sleep(60 * 5)  # ждем 5 минут до следующей проверки
