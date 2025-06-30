from binance.client import Client
import pandas as pd
import numpy as np
import time
import requests
import os
from utils import can_trade
from datetime import datetime, timedelta
from strategies import (
    ema_rsi_strategy,
    bollinger_rsi_strategy,
    macd_ema_strategy,
    vwap_rsi_strategy,
    macd_stochastic_strategy,
    bollinger_volume_strategy,
    ema_crossover_strategy
)

# Telegram конфигурация
TELEGRAM_TOKEN = os.getenv("TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

# Статистика торговли
trade_log = []

# Время следующей отправки отчета
next_report_time = datetime.now() + timedelta(hours=3)

symbol_precision_cache = {}

open_positions = {}  # Пример: {'BTCUSDT': {'side': 'BUY', 'entry_price': 30000.0, 'qty': 0.00033}}
TRADE_AMOUNT = 10    # Сумма сделки в USDT
START_DEPOSIT = 100.0
TRADE_PERCENT = 10
current_deposit = START_DEPOSIT

# 🔑 API ключи с Binance Testnet
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binance.vision/api'

# 🔄 Торгуемые пары
raw_symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'ADAUSDT', 'MATICUSDT', 'DOTUSDT', 'LINKUSDT', 'AVAXUSDT', 'XRPUSDT', 'PEPEUSDT']

# Получаем список всех доступных символов на Binance
exchange_info = client.get_exchange_info()
valid_binance_symbols = {s['symbol'] for s in exchange_info['symbols']}
symbols = [s for s in raw_symbols if s in valid_binance_symbols]

interval = Client.KLINE_INTERVAL_5MINUTE
lookback = 100

symbol_timeouts = {
    'BTCUSDT' : 120, 
    'ETHUSDT' : 120, 
    'BNBUSDT' : 100, 
    'SOLUSDT' : 90, 
    'ADAUSDT' : 90, 
    'MATICUSDT' : 90, 
    'DOTUSDT' : 90, 
    'LINKUSDT' : 90, 
    'AVAXUSDT' : 90, 
    'XRPUSDT' : 60, 
    'PEPEUSDT': 45
}

def get_klines(symbol):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=lookback)
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

def format_quantity(qty):
    # Преобразуем float в строку с обычной десятичной записью, не используя e-формат
    return format(qty, 'f').rstrip('0').rstrip('.') or '0'

def execute_trade(symbol, signal):
    global current_deposit
    if symbol in open_positions:
        return  # уже есть открытая позиция

    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])

        trade_amount = current_deposit * TRADE_PERCENT / 100

        if not can_trade(client, symbol, trade_amount):
            return

        qty = get_trade_quantity(symbol, trade_amount, price)
        qty_str=format_quantity(qty)

        exchange_info = client.get_symbol_info(symbol)                

        # Проверка баланса USDT перед покупкой
        if signal == 'BUY':
            balance_info = client.get_asset_balance(asset='USDT')
            free_usdt = float(balance_info['free']) if balance_info else 0.0
            if free_usdt < TRADE_AMOUNT:
                return  # Недостаточно USDT — пропускаем

        if signal == 'BUY':
            client.order_market_buy(symbol=symbol, quantity=qty_str)
        elif signal == 'SELL':
            # Проверка баланса монеты перед продажей
            base_asset = symbol.replace('USDT', '')
            balance_info = client.get_asset_balance(asset=base_asset)
            free_balance = float(balance_info['free']) if balance_info else 0.0
            if free_balance < qty:
                return  # Недостаточно монет — пропускаем
                
            client.order_market_sell(symbol=symbol, quantity=qty_str)

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
        error_message = f"❌ Ошибка при торговле {symbol}: {e}"
        print(f"{error_message}")
        send_telegram_error(error_message)

def get_trade_quantity(symbol, trade_amount, price):
    info = client.get_symbol_info(symbol)
    step = 0.00001  # fallback

    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            step = float(f['stepSize'])
            break

    raw_qty = trade_amount / price
    precision = int(round(-np.log10(step)))
    adjusted_qty = raw_qty - (raw_qty % step)
    qty = round(adjusted_qty, precision)
    return float(qty)

def check_exit_conditions():
    global current_deposit
    symbols_to_close = []

    for symbol, pos in open_positions.items():
        try:
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            entry = pos['entry_price']
            side = pos['side']
            qty = pos['qty']
            qty_str=format_quantity(qty)
            
            change = (current_price - entry) / entry * 100
            if side == 'SELL':
                change = -change  # для коротких сделок переворачиваем знак

            elapsed_minutes = (datetime.now() - pos['time']).total_seconds() / 60
            max_minutes = symbol_timeouts.get(symbol, 60) # Дефолт 60 мин
            
            if change >= 1.5 or change <= -1.0 or elapsed_minutes >= max_minutes:

                # Проверка перед закрытием позиции
                base_asset = symbol.replace('USDT', '')

                try:
                    balance_info = client.get_asset_balance(asset=base_asset)
                    free_balance = float(balance_info['free']) if balance_info else 0.0
                except Exception:
                    continue  # если не удалось получить баланс — пропускаем

                # Если не хватает монет — пропускаем
                if free_balance < qty:
                    continue
                
                close_side = 'SELL' if side == 'BUY' else 'BUY'
                if close_side == 'BUY':
                    client.order_market_buy(symbol=symbol, quantity=qty_str)
                else:
                    client.order_market_sell(symbol=symbol, quantity=qty_str)

                # Обновление лога
                for t in reversed(trade_log):
                    if t['symbol'] == symbol and t['result'] is None:
                        trade_amount = t['amount']
                        profit_usdt = round(trade_amount * change / 100, 2)
                        t['result'] = 'win' if profit_usdt > 0 else 'loss'
                        t['profit'] = profit_usdt
                        current_deposit += profit_usdt  # не забываем обновить текущий депозит
                        result = t['result']
                        print(f"📤 Закрыта позиция по {symbol} — {result.upper()} ({change:.2f}%)")
                        break
                

                symbols_to_close.append(symbol)

        except Exception as e:
            error_message = f"❌ Ошибка при закрытии {symbol}: {e}"
            print(f"{error_message}")
            send_telegram_error(error_message)

    for s in symbols_to_close:
        open_positions.pop(s)

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload)
    except Exception as e:
       error_message = f"❌ Ошибка при отправке Telegram: {e}"
       print(f"{error_message}")
       send_telegram_error(error_message)
        

def send_telegram_error(message):
    full_message = f"❌ Ошибка:\n{message}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': full_message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"[Telegram Error Fail] {e}")

def send_statistics():
    if not trade_log:
        send_telegram_message("📊 Пока нет сделок.")
        return

    total = len(trade_log)
    wins = sum(1 for t in trade_log if t['result'] == 'win')
    losses = sum(1 for t in trade_log if t['result'] == 'loss')
    total_amount = sum(t['amount'] for t in trade_log)
    total_profit = sum(t.get('profit', 0) for t in trade_log)
    open_trades = sum(1 for t in trade_log if t['result'] is None)

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

def round_step_size(symbol, qty):
    if symbol in symbol_precision_cache:
        precision = symbol_precision_cache[symbol]
    else:
        info = client.get_symbol_info(symbol)
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step = float(f['stepSize'])
                precision = int(round(-np.log10(step)))
                symbol_precision_cache[symbol] = precision
                break
        else:
            precision = 5  # запасной вариант

    return round(qty, precision)

# 🧠 Главный цикл
while True:
    print(f"\n🕒 Проверка сигналов... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for symbol in symbols:
        try:
            df = get_klines(symbol)
            if df is None or df.empty:
                continue

            strategies = [
                ema_rsi_strategy,
                bollinger_rsi_strategy,
                macd_ema_strategy,
                vwap_rsi_strategy,
                macd_stochastic_strategy,
                bollinger_volume_strategy,
                ema_crossover_strategy
            ]

            signal = None
            for strat in strategies:
                signal = strat(df)
                if signal:
                    print(f" 📊 {symbol}: {strat.__name__} дал сигнал {signal}")
                    break

            if signal:
                execute_trade(symbol, signal)

        except Exception as e:
            error_message = f"⚠️ Ошибка при обработке {symbol}: {e}"
            print(f"{error_message}")
            send_telegram_error(error_message)

  # Проверка времени отчета
    if datetime.now() >= next_report_time:
        send_statistics()
        next_report_time = datetime.now() + timedelta(hours=3)

    check_exit_conditions()
    
    time.sleep(60 * 5)  # ждем 5 минут до следующей проверки
