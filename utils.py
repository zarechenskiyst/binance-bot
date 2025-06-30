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
