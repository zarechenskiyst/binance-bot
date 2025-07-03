from binance.client import Client

# Кэш для minNotional
min_notional_cache = {}

def can_trade(client: Client, symbol: str, trade_amount: float) -> bool:
    """
    Проверяет, можно ли торговать по символу, исходя из минимального notional (minNotional).
    """
    if symbol not in min_notional_cache:
        try:
            info = client.get_symbol_info(symbol)
            for f in info['filters']:
                if f['filterType'] == 'MIN_NOTIONAL':
                    min_notional_cache[symbol] = float(f['minNotional'])
                    break
        except Exception as e:
            print(f"⚠️ Ошибка получения minNotional для {symbol}: {e}")
            return False

    min_required = min_notional_cache.get(symbol, 10)

    if trade_amount < min_required:
        print(f"⚠️ Пропущена сделка {symbol}: minNotional {min_required} > ставка {trade_amount:.2f}")
        return False

    return True

def optimize_parameters(window=50, min_winrate=0.5):
    """
    Берём последние `window` закрытых сделок и пересчитываем winrate.
    Если winrate ниже `min_winrate`, пробуем слегка поменять параметры.
    """
    # Берём последние завершённые сделки
    closed = [t for t in trade_log_all if t['result'] in ('win','loss')]
    recent = closed[-window:]
    if len(recent) < window:
        return  # ещё мало данных

    wins = sum(1 for t in recent if t['result']=='win')
    wr = wins / window

    # Если падение winrate — меняем ema_period +\- 2
    if wr < min_winrate:
        # Пример: если сейчас 20, то пробуем 22, иначе 18
        strategy_params['ema_period'] += 2
        if strategy_params['ema_period'] > 50:
            strategy_params['ema_period'] = 20  # возвращаем к базовому
        # Аналогично можно менять RSI
        strategy_params['rsi_period'] = max(8, strategy_params['rsi_period'] - 2)
        print(f"🔧 Оптимизация: winrate={wr:.2f}, новые параметры: EMA={strategy_params['ema_period']}, RSI={strategy_params['rsi_period']}")

