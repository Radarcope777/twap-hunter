# BOT B — FULL ACTUAL TRADING HISTORY & PARAMETER ANALYSIS

Работай строго READ-ONLY.

Никаких изменений кода, БД, настроек или работающего бота.

---

# 1. ЦЕЛЬ

Нас интересует ТОЛЬКО этот бот:

```
Bot B

id = b_384a1650
strategy = twap_lockin
asset = BTC
```

Bot A:

```
b_0d55c464
```

ПОЛНОСТЬЮ ИСКЛЮЧИТЬ ИЗ АНАЛИЗА.

Не сравнивать Bot B с Bot A.

Не использовать сделки Bot A ни в одной статистике.

Задача:

> Выгрузить и полностью проанализировать всю фактически совершённую торговую историю Bot B и на основании реальных production trades определить, есть ли основания менять его текущие параметры.

Особенно нас интересуют:

```
entry_window_s
min_margin_bps
max_entry_cents
```

Сначала анализ.

Никаких изменений параметров в этой задаче.

---

# 2. ТЕКУЩИЙ BOT B

Ожидаемая конфигурация:

```
id = b_384a1650
strategy = twap_lockin

entry_window_s = 60
min_margin_bps = 2.5
max_entry_cents = 90
order_usd = 100
```

Но source of truth:

```
live API
bots table
research_settings_versions
```

Сначала проверь текущие фактические параметры.

Если они отличаются от указанных выше — сообщи это.

Не изменяй их.

Также покажи:

```
start balance
current live balance
realized P&L
current open position, если есть
bot status
total persisted trades
```

Пользователь ожидает, что бот сейчас находится примерно в:

```
+$860
```

Не принимай эту цифру как факт.

Проверь фактический текущий результат по данным/API и сообщи точное значение.

---

# 3. СТРОГИЕ ОГРАНИЧЕНИЯ

НЕ:

- менять код;
- создавать аналитический модуль;
- менять БД;
- менять настройки;
- рестартовать backend;
- останавливать collector;
- запускать replay;
- запускать Phase 2B.2;
- создавать synthetic trades;
- моделировать сделки, которых Bot B не совершал;
- использовать hypothetical ask_size;
- оптимизировать counterfactual entries;
- анализировать Bot A.

Разрешены только:

```
read-only SQL
read-only API
одноразовые read-only Python/PowerShell команды
```

для получения статистики.

---

# 4. ОПРЕДЕЛИ ПОЛНУЮ ТОРГОВУЮ ИСТОРИЮ BOT B

Сначала найди корректную границу текущего торгового периода Bot B.

Нужно определить:

```
последний RESET
start balance
первая BUY после RESET
последняя завершённая сделка
```

Не смешивай предыдущие тестовые периоды, если Bot B когда-либо reset'ился.

Основная выборка:

> ВСЕ фактические сделки текущего Bot B после последнего RESET.

Назови её:

```
CURRENT BOT B HISTORY
```

Дополнительно внутри неё выдели:

```
PHASE 2A PERIOD
```

начиная с:

```
1791459962473
```

Но основное решение по настройкам должно учитывать всю достоверную текущую историю.

---

# 5. ПРОВЕРКА ЦЕЛОСТНОСТИ

Перед анализом проверь trade history.

Посчитай:

```
BUY rows
SELL rows
SETTLE rows
ADJUST rows

completed positions
open positions

orphan exits
BUY without exit
multiple exits
duplicate trades
missing price
missing shares
missing P&L
```

Проверь:

```
start balance + realized P&L
```

против:

```
current balance
```

Объясни расхождение, если оно есть.

Не продолжай молча с повреждённой историей.

---

# 6. СОБЕРИ ОДНУ СТРОКУ НА КАЖДУЮ ФАКТИЧЕСКУЮ СДЕЛКУ

Для каждой production BUY Bot B:

```
window_id
side

entry timestamp
entry price
entry shares
entry notional
entry fee

projected_margin_bps at entry
abs(projected_margin_bps)

remaining_ms at entry

exit timestamp
exit type
exit price

realized P&L
WIN / LOSS

official/local market outcome
```

Используй только достоверные значения.

Для Phase 2A можно привязать research snapshot/event к фактическому BUY.

Если для старой сделки margin или remaining time достоверно восстановить нельзя:

```
NULL / unavailable
```

Не придумывай значение.

Не используй будущие данные для восстановления entry signal.

---

# 7. ЭКСПОРТ ФАКТИЧЕСКОЙ ИСТОРИИ

Создай:

```
research_output/bot_b_actual_trade_history.csv
```

Одна строка = одна фактическая позиция.

Минимальные колонки:

```
window_id
side
entry_ts
entry_price_cents
shares
entry_notional
entry_fee
entry_margin_bps
abs_entry_margin_bps
remaining_ms
exit_ts
exit_type
exit_price_cents
realized_pnl
result
market_outcome
```

Это должен быть экспорт реальных production trades.

Никаких replay trades.

---

# 8. ОБЩАЯ СТАТИСТИКА

Для CURRENT BOT B HISTORY:

```
completed trades
open trades

wins
losses
win rate

net realized P&L
gross profit
gross loss
profit factor

average P&L
median P&L

average win
median win
largest win

average loss
median loss
largest loss

best trade
worst trade

max consecutive wins
max consecutive losses
```

Также:

```
total entry notional
average entry notional
median entry notional
min/max entry notional
```

---

# 9. EQUITY / DRAWDOWN

По хронологии фактических сделок рассчитай:

```
cumulative realized P&L
equity after each completed trade
running peak
drawdown
```

Сообщи:

```
maximum drawdown $
maximum drawdown %
longest recovery period in trades
current distance from equity peak
```

Не строить отдельный модуль.

Просто расчёт для отчёта.

---

# 10. EXIT TYPES

Bot B не имеет cut.

Разбей сделки на:

```
PROFIT SELL
SETTLEMENT WIN
SETTLEMENT LOSS
other, если существует
```

Для каждой группы:

```
N
% trades
net P&L
avg P&L
median P&L
avg entry price
avg abs margin
avg remaining time
```

Особое внимание:

```
SETTLEMENT LOSS
```

Покажи каждую такую сделку отдельно.

---

# 11. ENTRY PRICE DISTRIBUTION

Buckets:

```
<50
50–59
60–69
70–74
75–79
80–84
85–87
88–89
90
>90
```

Для каждого:

```
N
W
L
WR

net P&L
gross profit
gross loss
profit factor

avg P&L
median P&L

avg shares
avg entry notional

avg pnl per share
median pnl per share
```

Главный вопрос:

> Как меняется качество реальных сделок при росте цены входа?

---

# 12. ОСОБО ПРОАНАЛИЗИРУЙ ДОРОГИЕ ВХОДЫ

Отдельные группы:

```
entry >= 80
entry >= 85
entry >= 88
entry >= 89
entry = 90
```

Для каждой:

```
N
W/L
WR
PnL
PF
avg PnL
median PnL
settlement losses
```

Нужно понять:

> Является ли текущий cap 90¢ слишком высоким?

---

# 13. MAX ENTRY CAP — ACTUAL-TRADE FILTER

Это НЕ backtest.

Использовать только фактически совершённые сделки Bot B.

Проверить:

```
<=90
<=89
<=88
<=87
<=85
<=82
<=80
<=75
<=70
```

Для каждого cap:

```
retained N
removed N
% retained

W
L
WR

net actual P&L
profit factor
avg P&L
median P&L

avg pnl/share
median pnl/share

removed trades W/L
removed trades P&L
```

Также:

```
difference vs current <=90 sample
```

Главный вопрос:

> Если удалить реальные дорогие входы, становится ли фактическая история устойчиво лучше?

---

# 14. MARGIN BPS DISTRIBUTION

Использовать сделки, для которых entry margin достоверно известен.

Использовать:

```
abs(projected_margin_bps)
```

Buckets:

```
2.5–2.99
3.0–3.99
4.0–4.99
5.0–5.99
6.0–7.49
7.5–9.99
10–14.99
15+
```

Также показать, если существуют реальные сделки:

```
<2.5
```

и объяснить почему они появились при current threshold 2.5:

- historical settings?
- timing difference?
- snapshot approximation?
- другое?

Для каждого bucket:

```
N
W/L
WR

PnL
PF
avg PnL
median PnL

avg entry price
avg remaining time

avg pnl/share
median pnl/share
```

---

# 15. MIN MARGIN — ACTUAL-TRADE FILTER

Только более строгие thresholds:

```
>=2.5
>=3.0
>=3.5
>=4.0
>=5.0
>=6.0
>=7.5
>=10
>=15
```

Для каждого:

```
retained N
removed N
% retained

W/L
WR

net actual P&L
PF
avg P&L
median P&L

avg pnl/share
median pnl/share

removed W/L
removed P&L
```

Главный вопрос:

> Удаляет ли повышение min_margin преимущественно плохие сделки или вместе с ними удаляет основную прибыль?

---

# 16. PRICE × MARGIN

Для сделок с достоверным margin построить таблицу:

```
entry price × abs(entry margin)
```

Price groups:

```
<70
70–79
80–84
85–87
88–89
90
```

Margin groups:

```
2.5–2.99
3–3.99
4–4.99
5–7.49
7.5–9.99
10+
```

В каждой ячейке:

```
N
W/L
WR
PnL
avg PnL
```

Не делать сильных выводов по маленьким N.

---

# 17. COMBINED FILTER ANALYSIS

Только реальные production trades.

Grid:

```
max_entry_cents:
90
89
88
87
85
82
80
75

min_margin_bps:
2.5
3.0
3.5
4.0
5.0
6.0
7.5
10
```

Условие:

```
entry_price <= cap
AND
abs(entry_margin_bps) >= threshold
```

Для каждой комбинации:

```
N
W
L
WR
PnL
PF
avg PnL
median PnL
avg pnl/share
```

Это ACTUAL-TRADE FILTER ANALYSIS.

Никаких новых входов.

---

# 18. SAMPLE SIZE

Каждую выборку классифицировать:

```
N < 5      = TINY
N 5–9      = VERY SMALL
N 10–19    = SMALL
N 20–49    = MODERATE
N >=50     = USEFUL
```

Не рекомендовать параметр только потому, что:

```
N=3
3 wins
100% WR
```

---

# 19. ENTRY TIME ANALYSIS

Если `remaining_ms` достоверно доступен:

```
60+
55–60
50–54
45–49
40–44
30–39
20–29
10–19
```

Для каждого:

```
N
W/L
WR
PnL
PF
avg PnL
median PnL
avg entry price
avg abs margin
```

Ответить:

> Есть ли признаки, что вход около T-60 хуже или лучше более поздних входов?

---

# 20. SIDE ANALYSIS

Отдельно:

```
UP
DOWN
```

Для каждого:

```
N
W/L
WR
PnL
PF
avg PnL
median PnL

avg entry price
avg margin
avg remaining
```

Проверь, не создаётся ли общая прибыль преимущественно одной стороной.

---

# 21. LOSSES — ОСОБО ВАЖНО

Покажи таблицу ВСЕХ фактических проигрышных сделок Bot B.

Для каждой:

```
window_id
date/time
side

entry price
shares
entry notional

entry margin bps
remaining time

exit type
exit price
realized P&L

market outcome
```

Для каждой проигрышной сделки коротко ответь:

```
Would lower max_entry have filtered it?
At which cap?

Would higher min_margin have filtered it?
At which threshold?
```

---

# 22. WINNERS REMOVED BY FILTERS

Это обязательно.

Для каждого интересного более строгого cap/threshold покажи:

```
losses removed
loss dollars removed

wins removed
profit dollars removed
```

Нам нельзя выбрать фильтр только потому, что он удаляет два проигрыша, если одновременно он удаляет $500 прибыли.

---

# 23. LEAVE-ONE-LOSS-OUT CHECK

Если проигрышных сделок мало, результаты могут полностью зависеть от одной сделки.

Поэтому:

Для каждого settlement loss повтори основные price/margin выводы:

```
с этой сделкой
без этой сделки
```

Нужно понять:

> Рекомендация по параметрам устойчива или полностью определяется одним loss?

---

# 24. CHRONOLOGICAL STABILITY

Раздели CURRENT BOT B HISTORY хронологически:

```
first 50%
second 50%
```

Также, если N позволяет:

```
first third
middle third
last third
```

Для каждого периода:

```
N
W/L
WR
PnL
PF
avg entry
avg margin
```

Проверь, одинаково ли ведут себя предлагаемые более строгие filters в разных частях истории.

---

# 25. PHASE 2A VS FULL CURRENT HISTORY

Сравни:

```
CURRENT BOT B HISTORY
```

и:

```
PHASE 2A PERIOD
```

по:

```
N
WR
PnL
PF
entry price distribution
margin distribution
loss characteristics
```

Если вывод по параметру совпадает:

```
CONSISTENT
```

Если расходится:

```
MIXED
```

---

# 26. НЕ ИСПОЛЬЗУЙ ТОЛЬКО ДОЛЛАРОВЫЙ P&L

Production position sizes различаются.

Поэтому каждый важный вывод проверяй одновременно по:

```
actual P&L
W/L
WR
PF
avg P&L
median P&L
avg PnL/share
median PnL/share
```

Если actual P&L говорит одно, а equal-share view другое — сообщи это.

Не скрывай расхождение.

---

# 27. НЕ ПЕРЕОПТИМИЗИРУЙ

Не выбирай комбинацию просто по максимальному historical P&L.

Нам нужно определить:

> Есть ли очевидно слабая зона текущих разрешённых входов?

И:

> Можно ли убрать её простым повышением margin или снижением cap без уничтожения значимой части хороших сделок?

Ищи устойчивый эффект, а не максимальную цифру.

---

# 28. ЧТО МОЖНО И НЕЛЬЗЯ ВЫВЕСТИ

Этот анализ МОЖЕТ дать основания:

```
max_entry 90 -> LOWER
```

или:

```
min_margin 2.5 -> RAISE
```

потому что мы можем удалить часть фактически совершённых сделок.

Этот анализ НЕ МОЖЕТ доказать:

```
max_entry >90 будет лучше
```

или:

```
min_margin <2.5 будет лучше
```

потому что Bot B такие дополнительные сделки не совершал.

Для этого потребовался бы counterfactual replay.

---

# 29. ГЛАВНЫЕ РЕШЕНИЯ

После всего анализа отдельно ответить:

## max_entry_cents = 90

Выбрать:

```
KEEP 90
LOWER
INSUFFICIENT DATA
```

Если LOWER:

```
recommended cap =
```

и дать конкретные фактические основания.

---

## min_margin_bps = 2.5

Выбрать:

```
KEEP 2.5
RAISE
INSUFFICIENT DATA
```

Если RAISE:

```
recommended threshold =
```

и дать конкретные основания.

---

## entry_window_s = 60

Только если entry-time data достаточно:

```
KEEP 60
SHORTEN
INSUFFICIENT DATA
```

Не увеличивать >60 на основании этого анализа, потому что дополнительные сделки отсутствуют в production history.

---

# 30. ОТЧЁТ

Создай:

```
docs/BOT_B_ACTUAL_TRADING_HISTORY_ANALYSIS.md
```

И экспорт:

```
research_output/bot_b_actual_trade_history.csv
```

Больше ничего не создавать.

---

# 31. ФИНАЛЬНАЯ СВОДКА

В конце отчёта обязательно:

```
BOT B ACTUAL TRADING HISTORY ANALYSIS

Bot:
b_384a1650
twap_lockin

Current settings:
entry_window_s =
min_margin_bps =
max_entry_cents =
order_usd =

Current period:
start =
end =
completed trades =

Start balance =
Current balance =
Net realized P&L =
Win rate =
Profit factor =
Maximum drawdown =

Profit exits =
Settlement wins =
Settlement losses =
```

Затем:

```
MAX ENTRY ANALYSIS

Current: 90c
Verdict: KEEP / LOWER / INSUFFICIENT DATA
Recommended value:
Evidence:
Trades removed:
Wins removed:
Losses removed:
PnL removed:
```

Затем:

```
MIN MARGIN ANALYSIS

Current: 2.5 bps
Verdict: KEEP / RAISE / INSUFFICIENT DATA
Recommended value:
Evidence:
Trades removed:
Wins removed:
Losses removed:
PnL removed:
```

Затем:

```
ENTRY WINDOW ANALYSIS

Current: 60s
Verdict: KEEP / SHORTEN / INSUFFICIENT DATA
Recommended value:
Evidence:
```

И финально:

```
Strongest profitable region:
Weakest region:
Most dangerous loss pattern:

Are profits broadly distributed or dominated by a few trades?
Are losses concentrated in a specific price range?
Are losses concentrated in a specific margin range?
Are losses concentrated near T-60?

Would lowering max_entry_cents be justified now? YES/NO
Would raising min_margin_bps be justified now? YES/NO
Would shortening entry_window_s be justified now? YES/NO
```

Самый последний блок:

```
IF I HAD TO SET BOT B TODAY BASED ONLY ON ITS ACTUAL PRODUCTION HISTORY:

entry_window_s = ?
min_margin_bps = ?
max_entry_cents = ?

Confidence: LOW / MEDIUM / HIGH

Reason:
```

Не менять эти настройки.

Только дать рекомендацию.

STOP.