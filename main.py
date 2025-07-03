from binance.client import Client
import pandas as pd
import numpy as np
import time
import requests
import os
from zoneinfo import ZoneInfo
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
START_DEPOSIT = 100.0
TRADE_PERCENT = 15
current_deposit = START_DEPOSIT

# В начале файла
strategy_params = {'ema_period': 20, 'rsi_period': 14}  # при желании — сюда же можно добавить macd_fast=12, macd_slow=26 и т.д.
trade_log_all = [] # для хранения полной истории
consecutive-losses = 0
pause_until = None

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

def confidence_multiplier(buy_count, sell_count):
    count = max(buy_count, sell_count)
    if count >= 4:
        return 1.2  # высокая уверенность
    elif count == 3:
        return 1.1
    elif count == 2:
        return 1.0
    else:
        return 0.9  # слабый сигнал

def estimate_volatility(df):
    """Оценивает волатильность как среднее тело свечей / цену"""
    df['body'] = abs(df['close'] - df['open'])
    avg_body = df['body'].rolling(window=20).mean().iloc[-1]
    avg_price = df['close'].rolling(window=20).mean().iloc[-1]
    return avg_body / avg_price
        
def is_trading_time():
    now = datetime.now(ZoneInfo("Europe/Kyiv")).time()
    return now >= datetime.strptime("06:00", "%H:%M").time() and now <= datetime.strptime("22:00", "%H:%M").time()
    
def is_volume_sufficient(df, min_volume_ratio=0.5):
    """Проверяет, превышает ли последний объём средний хотя бы на min_volume_ratio"""
    df['volume'] = df['volume'].astype(float)
    avg_volume = df['volume'].rolling(window=20).mean().iloc[-2]
    current_volume = df['volume'].iloc[-1]
    return current_volume >= avg_volume * min_volume_ratio
    
def get_klines(symbol):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=lookback)
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['open'] = df['open'].astype(float)
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

def get_symbol_winrate(symbol, min_trades=5):
    """Возвращает winrate символа, если достаточно сделок"""
    trades = [t for t in trade_log if t['symbol'] == symbol and t['result'] in ('win', 'loss')]
    total = len(trades)
    if total < min_trades:
        return None  # недостаточно данных
    wins = sum(1 for t in trades if t['result'] == 'win')
    return wins / total
    
def calculate_adaptive_timeout(df):
    """Адаптивный тайм-аут на основе волатильности"""
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)

    volatility = (df['high'] - df['low']) / df['close'] * 100
    avg_volatility = volatility.rolling(window=20).mean().iloc[-1]

    if avg_volatility > 3:
        return 30  # высокая волатильность — держим коротко
    elif avg_volatility > 1.5:
        return 60
    else:
        return 90  # низкая волатильность — дольше держим

def format_quantity(qty):
    # Преобразуем float в строку с обычной десятичной записью, не используя e-формат
    return format(qty, 'f').rstrip('0').rstrip('.') or '0'

def execute_trade(symbol, signal, confidence = 1.0, timeout = 60):
    global current_deposit
    if symbol in open_positions:
        return  # уже есть открытая позиция

    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])

        base_percent = TRADE_PERCENT

        extra_percent= 0
        # Получаем winrate для символа
        winrate = get_symbol_winrate(symbol)

        if winrate is not None:
            if winrate >= 0.7:
                extra_percent = 2  # +5% к ставке
            elif winrate <= 0.5:
                extra_percent = -2  # -5% (сниженная ставка)
        
        adjusted_percent = min(base_percent + extra_percent + (confidence - 2) * 2, 30)
        trade_amount = current_deposit * adjusted_percent / 100

        if not can_trade(client, symbol, trade_amount):
            return

        qty = get_trade_quantity(symbol, trade_amount, price)
        qty_str=format_quantity(qty)

        exchange_info = client.get_symbol_info(symbol)                

        # Проверка баланса USDT перед покупкой
        if signal == 'BUY':
            balance_info = client.get_asset_balance(asset='USDT')
            free_usdt = float(balance_info['free']) if balance_info else 0.0
            if free_usdt < trade_amount:
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
            'time': datetime.now(),
            'timeout': timeout
        }

        trade_log.append({
            'symbol': symbol,
            'direction': signal,
            'amount': trade_amount,
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
            max_minutes = pos.get('timeout', 60) # Дефолт 60 мин
            
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
                        if t['result'] == 'loss':
                            consecutive_losses += 1
                        else:
                            consecutive_losses = 0

                        # Если дошли до порога — ставим паузу
                        if consecutive_losses >= LOSS_PAUSE_THRESHOLD:
                        pause_until = datetime.now() + timedelta(minutes=PAUSE_DURATION_MIN)
                        print(f"⏸️ Ставим паузу до {pause_until.strftime('%H:%M')}, из-за {consecutive_losses} убыточных сделок подряд.")
                        break
                

                symbols_to_close.append(symbol)

                

        except Exception as e:
            error_message = str(e)
            if "502 Bad Gateway" in error_message:
                print(f"⚠️ Binance временно недоступен при закрытии {symbol} — ошибка 502.")
                continue  # просто пропускаем и пробуем позже
            else:
                print(f"❌ Ошибка при закрытии {symbol}: {e}")
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
    global trade_log,  current_deposit
    if not trade_log:
        send_telegram_message("📊 Пока нет сделок.")
        return

    closed_trades = [t for t in trade_log if t['result'] is not None]
    open_trades= [t for t in trade_log if t['result'] is None]
    
    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if t['result'] == 'win')
    losses = sum(1 for t in closed_trades if t['result'] == 'loss')
    total_amount = sum(t['amount'] for t in closed_trades)
    total_profit = sum(t.get('profit', 0) for t in closed_trades)
    open_trades = len(open_positions)

    message = (
        "📈 *Статистика за 3 часа*\n\n"
        f"Всего сделок: {total}\n"
        f"📦 Баланс: ${current_deposit:.2f}\n"
        f"✅ Прибыльных: {wins}\n"
        f"❌ Убыточных: {losses}\n"
        f"🟡 Открытых: {open_trades}\n"
        f"💸 Поставлено: ${total_amount:.2f}\n"
        f"💰 Прибыль: ${total_profit:.2f}"
    )
    send_telegram_message(message)

    trade_log = [t for t in trade_log if t['result'] is None]

    trade_log_all.extend(closed_trades)
    optimize_parameters()

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
    if not is_trading_time():
        print("⏳ Вне торгового времени. Пауза.")
        time.sleep(60 * 5)
        continue

    print(f"\n🕒 Проверка сигналов... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for symbol in symbols:
        try:
            df = get_klines(symbol)
            if df is None or df.empty:
                continue

             if pause_until and datetime.now() < pause_until:
                print(f"⏸ Торговля на паузе до {pause_until.strftime('%H:%M')}")
                time.sleep(60 * 5)
                continue

            adaptive_timeout = calculate_adaptive_timeout(df)
            
            strategies = [
                ema_rsi_strategy,
                bollinger_rsi_strategy,
                macd_ema_strategy,
                vwap_rsi_strategy,
                macd_stochastic_strategy,
                bollinger_volume_strategy,
                ema_crossover_strategy
            ]

            signals = []
            for strat in strategies:
                result = strat(df)
                if result:
                    print(f" 📊 {symbol}: {strat.__name__} дал сигнал {result}")
                    signals.append(result)

            # Подтверждение от минимум 2 стратегий
            buy_count = signals.count('BUY')
            sell_count = signals.count('SELL')

            final_signal = None
            confidence = 0
            
            if buy_count >= 2 and sell_count == 0:
                final_signal = 'BUY'
            elif sell_count >= 2 and buy_count == 0:
                final_signal = 'SELL'

            if final_signal:
                # Коэффициенты уверенности
                conf_mult = confidence_multiplier(buy_count, sell_count)

                # Волатильность
                volatility = estimate_volatility(df)
    
                # Модифицируем timeout
                new_timeout = int(adaptive_timeout * (1 + volatility))  # адаптивное время удержания
        

                # Передаём коэффициент уверенности в execute_trade
                execute_trade(symbol, final_signal, confidence=conf_mult, timeout = min(new_timeout, 240))

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
