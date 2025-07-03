import pickle, datetime

# Загрузим историю (trade_log_all) из файла, если вы его сохраняете
with open('trade_log_all.pkl', 'rb') as f:
    trade_log_all = pickle.load(f)

# Вбрасываем 5 убыточных сделок подряд
for _ in range(5):
    trade_log_all.append({
        'symbol':'BTCUSDT', 'result':'loss', 'amount':1,
        'profit':-1, 'timestamp':datetime.datetime.now()
    })

# Сохраняем обратно
with open('trade_log_all.pkl', 'wb') as f:
    pickle.dump(trade_log_all, f)

print("Injected 5 losses, бот на следующем цикле увидит подряд убытки и поставит паузу.")
