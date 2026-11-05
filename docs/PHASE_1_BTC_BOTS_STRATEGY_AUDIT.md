# PHASE 1 — Аудит двух BTC TWAP-ботов

Дата: 08.10.2026. Срез SQLite: **2026-10-08T08:46:16.108027+00:00** (UTC; Москва = UTC+3).
Репозиторий: D:/TEST/twap-hunter. Commit: f249b464e18ac3db21fc5231e5684b535e74f9b9.
Источник данных: D:/TEST/twap-hunter/data/twaphunter.db, включая WAL; соединение SQLite URI mode=ro, PRAGMA query_only=ON, одна read-транзакция.
Боты продолжали работать независимо от аудита. Цифры ниже зафиксированы на этом срезе, не являются обещанием текущего состояния после него.

**Scope:** A = b_0d55c464; B = b_384a1650. Другие боты не включены в расчёты. Таблица accounts и содержимое приватных ключей не читались.
Исходники, конфигурация, настройки, записи БД и статусы не менялись; ордера не отправлялись; боты не запускались и не останавливались.
Читался GET /api/bots на работающем localhost:9000, без login/POST; из ответа анализировались только два целевых ID. API подтвердил стратегии, параметры, статусы и отсутствие позиций.
Проверки чистых функций выполнялись через AST-извлечение отдельных функций в память с искусственными объектами, без импорта backend/storage и без создания менеджеров приложения.
Единственный создаваемый проектный файл — этот отчёт. Дополнительный Canvas не создавался: пользователь явно задал Markdown-артефакт.

## A. Executive Summary

1. **Оба целевых ID — paper-боты**, записи таблицы bots; в real_bots этих ID нет. Их BUY/SELL — симуляция, не реальные CLOB-ордера. Выводы нельзя автоматически переносить на реальные сделки или SDK V2.
2. A действительно twap_lockin_cut; B действительно twap_lockin. База и runtime API согласованы. Называть B cut-ботом неверно.
3. A: 479 закрытых сделок, 378W/101L, WR 78.91%, net по журналу **−$764.63**. B: 50, 37W/13L, WR 74.00%, net **−$219.09**.
4. Средний выигрыш A $11.95 против среднего проигрыша $52.29; B $15.08 против $59.78. Наблюдаемые WR ниже порогов безубыточности 81.40% и 79.85%.
5. Входы одинаковы по алгоритму, но существенно различаются параметрами: A 90s / 0.3bps / 95¢; B 60s / 2.5bps / 90¢. Оба лот $100 ДО комиссии.
6. Выход при bid ≥99¢ действует для ОБОИХ. Только A продаёт при подтверждённом развороте проекции. У A 89 cut-выходов, один из них прибыльный; 88 отрицательных cut дают средний убыток $50.41. Название Small Loss не соответствует гарантии исполнения.
7. У каждого 13 SETTLE LOSS. Все эти исходы совпадают с сохранённым outcome_official соответствующего BTC-раунда. Для A причину отсутствия своевременного cut восстановить невозможно: нет траекторий проекции, таймера и неудачных SELL. Для B удержание против перевернувшегося сигнала предусмотрено стратегией.
8. На раннем входе A (90s до закрытия, lookback 60s) прогноз является последней Chainlink-ценой, перенесённой вперёд. Ещё ни одна уже прошедшая секунда не входит в будущий финальный 60s TWAP. Это НЕ почти зафиксированный исход.
9. Есть HIGH-риски: устаревшие/непривязанные к раунду котировки; отсутствие проверок свежести oracle; PTB carry без проверки timestamp; отсутствие коррекции decisive-local settlements; повторная задержка после неудачного cut; неперсистентный one-entry lock; неатомарное сохранение сделки и состояния.
10. Корректного полного replay имеющихся данных недостаточно. Подбор production-настроек не выполнялся.

## B. Multi-Bot Architecture

```text
Binance ticks (BTC)       RTDS Chainlink / TWAP30 / TWAP60
         \                         /
          Pipeline BTC: rolling, PTB, clock, ring buffers
                           ↑
             Gamma + CLOB books + resolutions
                           |
                  Hub.pipelines["BTC"]
                           |
             BotManager._loop (sleep 0.25s + work)
                    /                         \
    bots["b_0d55c464"]                  bots["b_384a1650"]
    params/balance/position             params/balance/position
    _rt[id].adverse_since               _rt[id].entries
    _eval_twap_lockin_cut               _eval_twap_lockin
             |                                  |
       paper buy/sell                       paper buy/sell
             \                                  /
           SQLite trades WHERE bot_id = own id
           SQLite bots id → JSON per bot
           rounds (asset="BTC", window_id): SHARED market facts
```

Создание: frontend/js/bots.js → POST /api/bots → проверка asset/strategy → default_params + sanitize_params → BotManager.create.
UUID b_...; отдельные словари params, stats, position, trades и balance; новый бот stopped, при start=true затем set_running.
Выбор стратегии сохраняется в JSON bots.data.strategy. Отдельных stateful экземпляров Strategy нет: stateless-функции из _EVALUATORS вызываются с bot и _rt[bot_id].
Dispatch: twap_lockin → _eval_twap_lockin; twap_lockin_cut → _eval_twap_lockin_cut; winning_hold → _eval_winning_hold (последний исследован только как элемент общей архитектуры).
Обновление API меняет params/name, не strategy. Runtime читает свой словарь в памяти; произвольное внешнее редактирование SQLite автоматически не подхватывается.
Каждый бот имеет максимум одну position; история trade rows разделена bot_id. rounds не имеет bot_id, это нормально: привязка через BTC + window_id позиции/сделки, а не через имя/время суток.
Оба BTC-бота могут торговать одновременно и в разные стороны. Глобального торгового market lock между ними нет. Ask depth виртуально не расходуется между paper-ботами: оба могут считать заполненным один и тот же объём.
Прямого смешивания балансов, параметров, таймеров и позиций A/B в текущем пути не найдено. У каждого _fresh_rt создаёт новый вложенный entries.
Общие изменяемые объекты: Pipeline, rolling/history/quotes/PTB/round_results, Hub, Storage, HTTP-клиент и цикл менеджера. Ошибочный общий источник влияет на оба решения.
Один цикл последовательно обходит ботов; await при direct settlement HTTP может задержать проверку остальных; внешнее исключение прерывает текущий проход целиком. Поэтому логическая изоляция состояния НЕ равна изоляции задержек.
Hub retained[asset] хранит множество bot_id: один STOP не должен выключить feed другого running-бота.

Схема:
- bots(id PRIMARY KEY, data JSON TEXT, updated_ms): конфигурация, статус, деньги, открытая позиция, последние 300 записей, pending_official.
- trades(id, bot_id, t, window_id, round_label, action, side, price_cents, shares, pnl, reason, balance): полный оставшийся журнал ПОСЛЕ RESET.
- rounds(asset, window_id PRIMARY KEY pair, ptb_twap, ptb_price, final_twap, final_range_twap, outcome_local, outcome_official, ...): общая история раундов.
- _rt: только RAM; записи/таймер после перезапуска теряются.
- real_bots/accounts: отдельный слой, не включён в performance настоящего аудита.

## C. Verified Bot A Configuration


| Поле | Значение |
| --- | --- |
| Bot ID | b_0d55c464 |
| Name | BTC Lock In Small Loss |
| Asset | BTC |
| Persisted / runtime strategy | twap_lockin_cut / twap_lockin_cut |
| Status (DB/API) | running / running |
| Parameters | {"entry_window_s":90,"min_margin_bps":0.3,"max_entry_cents":95,"cut_margin_bps":0.5,"cut_confirm_s":2,"order_usd":100} |
| Start balance | 1000.00 |
| Cash / equity (нет позиции) | 235.31393171 |
| Balance P&L | -764.68606829 |
| Closed P&L from rounded log | -764.63 |
| History rows / BUY / closes | 964 / 479 / 479 |
| Embedded recent history | 300 |
| Open positions / pending official | 0 / 0 |
| Stats wins/losses/rounds | 378 / 101 / 18 |
| Fees persisted | 718.6175 |
| Persisted updated UTC | 2026-10-08T08:43:51.307367+00:00 |

rounds=18 означает число settlement/void-проходов, а не все 479 торговавшихся раундов. В статистике UI нельзя подменять этим completed trades.

## D. Verified Bot B Configuration


| Поле | Значение |
| --- | --- |
| Bot ID | b_384a1650 |
| Name | BTC TWAp LOCKIN |
| Asset | BTC |
| Persisted / runtime strategy | twap_lockin / twap_lockin |
| Status (DB/API) | running / running |
| Parameters | {"entry_window_s":60,"min_margin_bps":2.5,"max_entry_cents":90,"order_usd":100} |
| Start balance | 1000.00 |
| Cash / equity (нет позиции) | 780.93793626 |
| Balance P&L | -219.06206374 |
| Closed P&L from rounded log | -219.09 |
| History rows / BUY / closes | 106 / 50 / 50 |
| Embedded recent history | 106 |
| Open positions / pending official | 0 / 0 |
| Stats wins/losses/rounds | 37 / 13 / 16 |
| Fees persisted | 53.8374 |
| Persisted updated UTC | 2026-10-08T08:44:40.909636+00:00 |

Расхождение только с неформальным названием в обсуждении: B НЕ twap_lockin_cut. Скрытого применения cut к B не найдено.

## E. twap_lockin_cut Implementation

A вызывает _eval_twap_lockin_cut (bots.py:1258). Плоское состояние → один вход на текущий window → 10..90s осталось → валидный projected margin с модулем ≥0.3bps → сторона по знаку → общий buy c cap95¢ и $100.
При позиции: общий profit99 имеет приоритет; затем adverse margin строго за противоположным порогом ±0.5bps; после наблюдаемого подтверждения ≥2s попытка общего sell.
Это не стоп по цене токена, проценту просадки или долларовому убытку. Во время ожидания bid может упасть почти до нуля. После cut повторного входа в этом раунде не будет, пока жив _rt.

## F. twap_lockin Implementation

B вызывает _eval_twap_lockin (bots.py:1240). Плоское состояние → тот же набор условий, но 10..60s, ≥2.5bps, cap90¢, $100.
При открытой позиции evaluator НЕ использует reversed margin для выхода. Может продать при bid≥99; иначе держит до settlement. При ручном STOP общий менеджер также пытается продать, но в данном срезе SELL flatten отсутствуют.
Прибыльная позиция может превратиться в полный проигрыш без нарушения алгоритма.

## G. Strategy Behavioral Diff


| Механика | Bot A — cut | Bot B — lockin |
| --- | --- | --- |
| Проекция/формула входа | Общая | Общая |
| Entry window текущий / default | 90s / 90s | 60s / 55s |
| Min margin текущий / default | 0.3 / 1.5bps | 2.5 / 2.5bps |
| Max entry текущий / default | 95 / 88¢ | 90 / 88¢ |
| Сумма текущая / default | $100 / $1 | $100 / $1 |
| Одна position / один вход за раунд | Да, RAM lock | Да, RAM lock |
| bid≥99¢ take profit | Да, hard-coded | Да, hard-coded |
| Cut-loss | Противоположный margin >0.5bps | Нет |
| Cut confirmation | 2s, состояние _rt[id] | Нет |
| Ценовой SL / max-loss | Нет | Нет |
| Late cut before end | Нет отдельного запрета по remaining | Не применяется |
| После смены window | Только settlement старой позиции | То же |
| Settlement/fees/accounting | Общие | Общие |


Карта реализации (пути относительно D:/TEST/twap-hunter):

| Файл:строка | Функция / объект | A | B | Назначение / настройка |
| --- | --- | --- | --- | --- |
| backend/main.py:369 | create_bot / API | Да | Да | Создание, default_params, sanitize_params |
| frontend/js/bots.js:88 | paramRow / collectParams / save settings | Да | Да | Форма по STRATEGIES[].params |
| backend/engine/bots.py:333 | BotManager | Да | Да | Изоляция по id |
| backend/engine/bots.py:662 | _loop / _evaluate | Да | Да | 0.25s, dispatch и settlement |
| backend/engine/bots.py:1199 | _projected_margin_bps | Да | Да | Локальный rolling projection vs shared PTB |
| backend/engine/bots.py:1258 | _eval_twap_lockin_cut | Да | Нет | entry_window_s, min_margin_bps, cut_* |
| backend/engine/bots.py:1240 | _eval_twap_lockin | Нет | Да | entry_window_s, min_margin_bps |
| backend/engine/bots.py:720 | buy | Да | Да | order_usd, max_entry_cents, guards |
| backend/engine/bots.py:1229 | lock_profit_if_bid | Да | Да | LOCK_PROFIT_CENTS=99 |
| backend/engine/bots.py:984 | sell | Да | Да | Bid fill всей позиции |
| backend/engine/bots.py:1018 | _try_settle / _reconcile | Да | Да | Local/official/VOID/ADJUST |
| backend/engine/metrics.py:17 | rolling_twap / RollingTwap | Да | Да | time-weighted price hold |
| backend/engine/pipeline.py:218 | _run_rtds | Да | Да | Chainlink history, official TWAP60 |
| backend/engine/pipeline.py:615 | _roll_window / _set_ptb | Да | Да | Official carry и fallback |
| backend/feeds/polymarket.py:277 | _discover_once / books / parse_resolution | Да | Да | Market lookup, quote, outcome |
| backend/storage.py:114 | add_trade / trades_for | Да | Да | bot_id в SQL |


Путь: Gamma discovery → общая market/UP/DOWN token map → CLOB top of book; RTDS → rolling и official TWAP60 → PTB на границе → projected final → margin → entry boolean → side → PAPER BUY → profit99 / (только A: cut) → PAPER SELL либо SETTLE → cash/stats → _log → trades(bot_id).
В paper buy token_id вообще не используется/не сохраняется; только side и window_id. Поэтому token selection происходит косвенно через feed mapping, а соответствие токена и window не проверяется buy.

## H. PTB / TWAP / Projected Final

**PTB.** _roll_window берёт текущее self.twap60 (последнее полученное значение официального crypto_prices_twap_sixty) и фиксирует как official_twap60_carry нового окна. Один общий PTB для A/B.
_set_ptb не перезаписывает непустой PTB в текущем окне. Используется Python float без предварительного округления. UI window.ptb: BTC 4 десятичных; round storage: 5; отображаемая строка может показывать 2.
Граница = floor(poly_now/300000)*300000, clock loop номинально 100ms.
**Ограничение:** значение TWAP60 не выбирается по source timestamp относительно границы, не проверяется возраст, не очищается при разрыве. Возможно перенести старое либо уже пришедшее после границы значение. _last_twap60_ms есть, но на latch не используется.
Fallback: rolling.value_at(start) → chainlink_last; на cold start backfill → twap_at_start_backfill; early Chainlink ≤start+10s → first_reference; _ptb_recovery раз в5s после4s → recovered oracle coverage (~90% lookback) → среднее Binance 1s closes (максимум3 попытки), exchange_approx.
Стратегии блокируют только exchange_approx, остальные приближения принимают. Source не сохранён в rounds/trades. Если fallback уже установлен, поздний официальный TWAP не заменит его в этом окне.
**Совпадение с сайтом/референсом:** намерение official carry видно в коде; историческую точность официального PTB по каждому окну доказать нельзя — нет независимого снимка сайта, официального opening value или source timestamp latch. Совпадение outcome_official с локальным исходом не доказывает совпадения PTB.

**Live TWAP:** resolution_twap предпочитает self.twap60, иначе rolling.value. Этот ряд используют display/локальная фиксация результата.
**Projected final:** НЕ экстраполяция готового TWAP60. Используется отдельный локальный RollingTwap из Chainlink, value_at(window_end). Binance, pressure, volatility и momentum в entry/cut margin не входят.
Lookback L обычно60s, но _on_new_market может заменить по twapLookbackSeconds. Текущий lookback/history по каждой исторической сделке не сохранён.
Принятые samples строго возрастают по ts; повторные/старые пропускаются. Данные хранятся около lookback+5s с опорной точкой.

Пусть T — конец раунда, L — lookback в ms, пары (t_i,p_i). p_i действует до t_(i+1), последняя цена до T:
```text
start_i = max(t_i, T-L)
end_i   = min(t_(i+1), T)  # у последней точки end=T
w_i     = max(0, end_i-start_i)
projected_final = sum(p_i*w_i) / sum(w_i)
```
NaN/inf prices пропускаются. Если sum(w)=0 → None. Если покрытие неполное, деление идёт на имеющийся вес, а не обязательно на полный L. Нет минимального числа samples или контроля полного покрытия.
Для полного покрытия, свежей точки в t и remaining r=T−t:
- r≥L → весь будущий TWAP = последняя p(t);
- 0≤r<L → уже известный интеграл [T−L,t] + p(t)*r, делённые на L.
При r=90s и L=60s доля уже зафиксированных наблюдений равна0%. При r=55s — всего около8.3% (без учёта задержки).
Это условный price-hold forecast, не вероятность, не граница гарантированной победы.

## I. Projected Margin Definition

```text
m = 10000 * (rolling.value_at(window_end) - ptb) / ptb
valid iff ptb is not None, ptb > 0,
         ptb_source != "exchange_approx",
         projected is not None
abs(m) >= min_margin_bps → entry may proceed
m > 0 → UP, otherwise DOWN
```

Для положительных настроенных порогов: UP m≥threshold; DOWN m≤−threshold. Равенство допускается при входе.
A threshold0.3bps = 0.003%; B2.5bps = 0.025%. Это минимальное расстояние прогноза от PTB, не вероятность выигрыша и не ожидаемая доходность токена.
В сравнении используется float без округления. В reason записывается только m с одним десятичным знаком. Поэтому распределения по margin восстановлены с погрешностью порядка±0.05bps; timestamp left в reason округлён до секунды.
Исходный projected_final в журнале не сохраняется. Оценка PTB*(1+m_log/10000) приблизительна.

## J. Exact Entry Algorithms

Для A/B общий алгоритм; значения A/B приведены явно. Каждая неуспешная проверка возвращает без BUY; обычные отказы НЕ пишутся в trade history.

| Gate | Файл / функция | A / B | При отказе / лог |
| --- | --- | --- | --- |
| status == running | bots.py:662 _loop | running / running | Evaluator не вызывается; автоматического trade log нет |
| Pipeline существует для bot.asset | _loop | BTC / BTC | continue, нет записи |
| Нет позиции прошлого window | _evaluate | position=None / None | Ожидание settlement; нет entry |
| Нет открытой position вообще | evaluators + buy | 0 / 0 на срезе | Управление текущей позицией |
| entries[current_window] < 1 | evaluators, _rt[id] | Один успешный BUY | Без re-entry до нового round/RAM reset |
| 10000ms ≤ remaining ≤ entry_window_s*1000 | evaluators | 10..90s / 10..60s | Вне окна молча |
| PTB>0, не None, source != exchange_approx | _projected_margin_bps | Общий | None → молча |
| rolling.value_at(end) != None | _projected_margin_bps | Общий | Нет прогноза → молча |
| abs(m) ≥ min_margin_bps | evaluators | 0.3 / 2.5bps | Молча |
| remaining≥5000ms и age≥2000ms | buy | Hard-coded; основное окно строже | Молча |
| ask существует, 0<ask<100, ask≤max | buy | 95 / 90¢ | Молча |
| bid существует и bid>0 | buy | Hard-coded | Молча |
| ask−bid≤20¢ | buy | Hard-coded | Молча; отрицательный spread отдельно не запрещён |
| shares=min(order_usd/(ask/100),ask_size) ≥1 | buy | $100 / $100; size cap при наличии | Молча, добора остатка нет |
| cost+fee≤balance | buy | Индивидуальный cash | Молча; не уменьшает лот под остаток cash |


**Отсутствующие gates:** market != None, current slug/window equality, quote/source timestamp age, feed connected, минимальное покрытие oracle, min entry price, volatility/risk limit. Не подменять отсутствующие проверки желаемыми.
Успех: log BUY с reason `lock-in: projected final {m:+.1f} bps vs PTB, {remaining/1000:.0f}s left`.
Первый подходящий tick в допустимом окне исполняет вход; sleep0.25s + работа означает частоту не выше4Hz, не гарантированные 4Hz.
При отказе buy попытка повторяется на следующем проходе, пока условия позволяют; entries растёт только после успешной покупки. После продажи тот же раунд не переоткрывается в текущем процессе.
При Enter=60 допустим интервал T−60..T−10 включительно, **не T−60..T−0**. Пять секунд из buy не расширяют более строгий десятисекундный evaluator.
Buy/sell обычные синхронные методы, внутри них нет await: межботового переключения между проверкой и изменением их RAM-состояния не происходит. После restart lock entries не восстановлен из trades.

## K. Exact Exit Algorithms

**Profit для обоих:** перед dispatch менеджер вызывает lock_profit_if_bid; при той же window_id и bid≥99 продаёт. Повторная проверка внутри evaluator не меняет правило.
Порог hard-coded LOCK_PROFIT_CENTS=99; продаёт по фактическому bid, не принудительно по99. В текущей истории reason округляет bid до целого.
**sell:** та же window; bid != None и bid>0 → продаётся ВСЁ количество по этому bid, вычитается model fee. Bid size/depth, slippage, частичное исполнение и CLOB rejection не моделируются. Никакого настоящего limit/FOK/market order для этих ID нет.
Если bid отсутствует/нулевой или window уже другая — False, неудачная попытка НЕ логируется. Profit повторится на следующем проходе.
**A** дополнительно делает cut по L. **B** при смене знака просто держит; не имеет скрытого SL, late exit или switch.
После rollover старая позиция передаётся _try_settle, выход по новому стакану не выполняется.
Settlement ladder:
1. In-memory official result source=market.
2. DB outcome_official.
3. Если есть local result и |final−PTB|≥5bps → немедленно local decisive.
4. Иначе ждать90s после конца окна.
5. Если local result есть → provisional; сохраняется pending_official, поправки ADJUST в течение15мин.
6. Если local result нет → прямой Gamma lookup с интервалом10s; без результата ждать до10мин.
7. VOID возвращает cost+entry_fee, исход не считается выигрышем/проигрышем.
Шаг3 не создаёт pending_official: официальный конфликт позже не исправит кошелёк paper. Код Pipeline обновит rounds, но не уже завершённую позицию. В данном срезе такого конфликта не обнаружено.
Для шага5 ADJUST — дополнительная поправка той же сделки, не новая completed trade. В срезе ADJUST/VOID нет.

## L. Cut-Loss State Machine

```python
# A only; после общего profit99 и проверки того же window
m = projected_margin()
if m is None:
    return                  # timer НЕ очищается!
adverse = m < -0.5 if position.side == "UP" else m > +0.5
if not adverse:
    adverse_since = None
    return
if adverse_since is None:
    adverse_since = now
    return
if now - adverse_since >= 2000:
    sell_at_current_bid()   # bool result игнорируется
    adverse_since = None   # сброс даже если sell=False
```

При m=−0.5 для UP / +0.5 для DOWN cut НЕ запускается: строгое сравнение.
Таймер отдельный у A; у B он не используется этим evaluator. Сбрасывается на смене раунда, отсутствии позиции, наблюдаемом non-adverse и после попытки cut; теряется на restart.
Подтверждение непрерывно только по доступным наблюдениям. None не сбрасывает таймер, stale samples могут продолжать давать значение. Фиксированной минимальной скорости новых oracle ticks нет.
Никакого ограничения потери $/¢ не задано: во время2s token bid может рухнуть. При bid>0 paper запишет продажу всей позиции, даже если реального объёма нет. При bid=0 не продаст и снова будет подтверждать.
Отдельного cutoff T−N для cut нет; на смене window оно прекращается, начинается settlement.
При confirm=0 всё равно требуется следующий evaluation: первый adverse tick лишь ставит timer.

Безопасные проверки извлечённых функций, только синтетические данные:
- value_at при последней цене105 даёт105 как через90s, так и через1час без обновлений: stale не отсеивается.
- adverse ticks0,2000,2250,4000,4250ms, sell=False → попытки только2000 и4250ms.
- m=None между adverse наблюдениями сохраняет timer=0.
- confirm=0 → первая попытка на250ms, не на0.
Это доказательства поведения текущего кода, НЕ реконструкция конкретных исторических провалов.

## M. Settlement-Loss Investigation

Ниже **каждый** доступный SETTLE LOSS, отдельно по id. BUY/SETTLE сопоставлены по последовательности в собственном журнале с проверкой window_id+side. Все пары корректны, orphan/duplicate entry не найдено.
Время — UTC; slug восстановлен как btc-updown-5m-(window_id*300), а НЕ записан BUY. PTB/final взяты из общей rounds только по BTC+window_id.
P* — приближение projected_final по округлённому m входа и сохранённому PTB. Оно не доказывает точный PTB на BUY: rounds хранит PTB при закрытии/последней записи, source/history latch отсутствуют.
Для ВСЕХ строк trajectory margin, факт запуска cut timer и неуспешные SELL отсутствуют в сохранённых данных. Успешного SELL между BUY и SETTLE нет (иначе position уже закрылась бы).

### A (b_0d55c464)

| BUY→EXIT id / window | BUY UTC / left | Side / entry | PTB / P*≈ | m entry bps | Final TWAP / official | PnL $ | Причина |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5716→5717 / 5970734 | 2026-10-05 17:13:30.453058 / 90s | DOWN / 93¢ | 85236.94234 / 85207.962 | -3.4 | 85241.33563 / UP | -100.53 | J — неизвестно почему cut не закрыл |
| 5736→5737 / 5970750 | 2026-10-05 18:33:30.956041 / 90s | DOWN / 95¢ | 85580.81907 / 85544.019 | -4.3 | 85585.30765 / UP | -100.37 | J — неизвестно почему cut не закрыл |
| 5788→5789 / 5970785 | 2026-10-05 21:28:30.911031 / 90s | UP / 84¢ | 85816.58534 / 85827.741 | 1.3 | 85806.92910 / DOWN | -48.99 | J — неизвестно почему cut не закрыл |
| 5902→5903 / 5970880 | 2026-10-06 05:23:32.344477 / 90s | DOWN / 71¢ | 85648.25221 / 85641.400 | -0.8 | 85648.88117 / UP | -102.86 | J — неизвестно почему cut не закрыл |
| 5930→5931 / 5970903 | 2026-10-06 07:18:32.727533 / 90s | UP / 60¢ | 85331.38753 / 85340.774 | 1.1 | 85330.44413 / DOWN | -104.67 | J — неизвестно почему cut не закрыл |
| 5968→5970 / 5970936 | 2026-10-06 10:03:33.123425 / 90s | UP / 95¢ | 85966.52536 / 86018.105 | 6.0 | 85965.36224 / DOWN | -100.37 | J — неизвестно почему cut не закрыл |
| 6004→6006 / 5970962 | 2026-10-06 12:14:09.964211 / 53s | DOWN / 31¢ | 86301.39476 / 86256.518 | -5.2 | 86347.78802 / UP | -1.99 | J — неизвестно почему cut не закрыл |
| 6126→6127 / 5971046 | 2026-10-06 19:13:34.415936 / 90s | DOWN / 79¢ | 85475.89744 / 85457.093 | -2.2 | 85476.48549 / UP | -24.14 | J — неизвестно почему cut не закрыл |
| 6174→6175 / 5971083 | 2026-10-06 22:18:47.956234 / 77s | DOWN / 71¢ | 85573.27787 / 85564.721 | -1.0 | 85573.50300 / UP | -14.61 | J — неизвестно почему cut не закрыл |
| 6506→6507 / 5971332 | 2026-10-07 19:03:37.885371 / 90s | UP / 85¢ | 83370.80755 / 83384.981 | 1.7 | 83361.99777 / DOWN | -24.70 | J — неизвестно почему cut не закрыл |
| 6554→6555 / 5971375 | 2026-10-07 22:38:38.226151 / 90s | UP / 85¢ | 83235.89623 / 83257.538 | 2.6 | 83234.72542 / DOWN | -101.24 | J — неизвестно почему cut не закрыл |
| 6568→6569 / 5971393 | 2026-10-08 00:08:39.615247 / 89s | UP / 72¢ | 83224.86688 / 83229.860 | 0.6 | 83224.72221 / DOWN | -102.72 | J — неизвестно почему cut не закрыл |
| 6618→6619 / 5971433 | 2026-10-08 03:28:38.823938 / 90s | UP / 57¢ | 82913.04919 / 82916.366 | 0.4 | 82909.66433 / DOWN | -18.00 | J — неизвестно почему cut не закрыл |


| Window / slug | SETTLE UTC | Источник записанного расчёта |
| --- | --- | --- |
| btc-updown-5m-1791220200 | 2026-10-05T17:16:30.650305+00:00 | round 1:10–1:15PM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791225000 | 2026-10-05T18:36:30.692913+00:00 | round 2:30–2:35PM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791235500 | 2026-10-05T21:31:31.080085+00:00 | round 5:25–5:30PM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791264000 | 2026-10-06T05:26:32.245837+00:00 | round 1:20–1:25AM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791270900 | 2026-10-06T07:21:32.581686+00:00 | round 3:15–3:20AM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791280800 | 2026-10-06T10:06:32.962753+00:00 | round 6:00–6:05AM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791288600 | 2026-10-06T12:15:03.422997+00:00 | round 8:10–8:15AM ET resolved UP (local TWAP math (decisive, 5.4 bps)) |
| btc-updown-5m-1791313800 | 2026-10-06T19:16:34.400806+00:00 | round 3:10–3:15PM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791324900 | 2026-10-06T22:21:34.783500+00:00 | round 6:15–6:20PM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791399600 | 2026-10-07T19:06:37.817414+00:00 | round 3:00–3:05PM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791412500 | 2026-10-07T22:41:36.465653+00:00 | round 6:35–6:40PM ET resolved DOWN (official resolution) |
| btc-updown-5m-1791417900 | 2026-10-08T00:11:38.446858+00:00 | round 8:05–8:10PM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791429900 | 2026-10-08T03:31:39.023384+00:00 | round 11:25–11:30PM ET resolved DOWN (provisional local math — auto-corrects if official differs) |

### B (b_384a1650)

| BUY→EXIT id / window | BUY UTC / left | Side / entry | PTB / P*≈ | m entry bps | Final TWAP / official | PnL $ | Причина |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5843→5845 / 5970829 | 2026-10-06 01:09:08.368630 / 53s | UP / 88¢ | 86028.72784 / 86051.095 | 2.6 | 86024.26923 / DOWN | -100.95 | I — нет выхода по adverse |
| 5923→5925 / 5970900 | 2026-10-06 07:04:07.540683 / 55s | DOWN / 71¢ | 85287.92033 / 85256.364 | -3.7 | 85290.67976 / UP | -38.98 | I — нет выхода по adverse |
| 5969→5971 / 5970936 | 2026-10-06 10:04:12.015755 / 51s | UP / 86¢ | 85966.52536 / 85988.017 | 2.5 | 85965.36224 / DOWN | -17.40 | I — нет выхода по adverse |
| 6005→6007 / 5970962 | 2026-10-06 12:14:09.989105 / 53s | DOWN / 31¢ | 86301.39476 / 86256.518 | -5.2 | 86347.78802 / UP | -1.99 | I — нет выхода по adverse |
| 6010→6011 / 5970964 | 2026-10-06 12:24:31.163334 / 32s | UP / 88¢ | 86227.82864 / 86250.248 | 2.6 | 86223.35539 / DOWN | -47.97 | I — нет выхода по adverse |
| 6030→6031 / 5970981 | 2026-10-06 13:49:16.847764 / 47s | DOWN / 88¢ | 86334.07921 / 86302.999 | -3.6 | 86337.05174 / UP | -22.21 | I — нет выхода по adverse |
| 6053→6055 / 5970996 | 2026-10-06 15:04:08.589804 / 55s | UP / 82¢ | 86585.26829 / 86612.976 | 3.2 | 86562.16349 / DOWN | -101.54 | I — нет выхода по adverse |
| 6079→6081 / 5971010 | 2026-10-06 16:14:11.607996 / 52s | DOWN / 74¢ | 85749.01290 / 85726.718 | -2.6 | 85754.23306 / UP | -30.33 | I — нет выхода по adverse |
| 6337→6339 / 5971225 | 2026-10-07 10:09:06.787644 / 60s | DOWN / 90¢ | 83715.95175 / 83683.303 | -3.9 | 83724.50602 / UP | -49.88 | I — нет выхода по adverse |
| 6348→6349 / 5971231 | 2026-10-07 10:39:08.768419 / 58s | DOWN / 90¢ | 83691.60562 / 83670.683 | -2.5 | 83695.08778 / UP | -100.78 | I — нет выхода по adverse |
| 6384→6385 / 5971260 | 2026-10-07 13:04:13.796279 / 53s | UP / 90¢ | 83449.38569 / 83511.973 | 7.5 | 83441.37137 / DOWN | -63.49 | I — нет выхода по adverse |
| 6457→6459 / 5971300 | 2026-10-07 16:24:07.754596 / 59s | UP / 90¢ | 83553.78816 / 83580.525 | 3.2 | 83518.15425 / DOWN | -100.78 | I — нет выхода по adverse |
| 6633→6635 / 5971443 | 2026-10-08 04:19:13.782826 / 55s | UP / 90¢ | 82612.20712 / 82636.165 | 2.9 | 82609.80564 / DOWN | -100.78 | I — нет выхода по adverse |


| Window / slug | SETTLE UTC | Источник записанного расчёта |
| --- | --- | --- |
| btc-updown-5m-1791248700 | 2026-10-06T01:11:31.707783+00:00 | round 9:05–9:10PM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791270000 | 2026-10-06T07:06:32.622679+00:00 | round 3:00–3:05AM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791280800 | 2026-10-06T10:06:33.007133+00:00 | round 6:00–6:05AM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791288600 | 2026-10-06T12:15:03.450441+00:00 | round 8:10–8:15AM ET resolved UP (local TWAP math (decisive, 5.4 bps)) |
| btc-updown-5m-1791289200 | 2026-10-06T12:26:33.376738+00:00 | round 8:20–8:25AM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791294300 | 2026-10-06T13:51:33.536489+00:00 | round 9:45–9:50AM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791298800 | 2026-10-06T15:06:33.576688+00:00 | round 11:00–11:05AM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791303000 | 2026-10-06T16:16:33.956453+00:00 | round 12:10–12:15PM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791367500 | 2026-10-07T10:11:36.546744+00:00 | round 6:05–6:10AM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791369300 | 2026-10-07T10:41:36.545745+00:00 | round 6:35–6:40AM ET resolved UP (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791378000 | 2026-10-07T13:06:36.978469+00:00 | round 9:00–9:05AM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791390000 | 2026-10-07T16:26:37.259490+00:00 | round 12:20–12:25PM ET resolved DOWN (provisional local math — auto-corrects if official differs) |
| btc-updown-5m-1791432900 | 2026-10-08T04:21:39.008832+00:00 | round 12:15–12:20AM ET resolved DOWN (provisional local math — auto-corrects if official differs) |

Классификация A–J:

| Категория | Bot A | Bot B |
| --- | --- | --- |
| A stayed favorable until too late | Не доказано | Не требуется cut |
| B cut threshold never crossed | Не доказано | Cut отсутствует |
| C confirmation not completed | Не доказано | Cut отсутствует |
| D SELL failed | Не доказано | Нет adverse SELL |
| E liquidity failure | Не доказано | Не доказано |
| F stale data | Риск кода, исторически не доказано | Риск кода, исторически не доказано |
| G near-close disabled | Не доказано; cut до roll разрешён | Cut отсутствует |
| H restart/interruption | Не доказано | Не доказано |
| I intentional hold by strategy | 0 атрибутированных | 13 |
| J exact failure chain unknown | 13 | 0 на уровне механизма hold; путь цены неизвестен |

«Не доказано» не равно нулю фактических событий. Категории считаются взаимоисключающей атрибуцией доступных доказательств, не предположениями.
У A **8 из13** сохранённых финальных отклонений от PTB лежат внутри±0.5bps на проигравшей стороне. Это показывает возможность проиграть внутри cut deadband, но не доказывает, что threshold никогда не пересекался раньше: final TWAP и live projected series различны.
Остальные5 также нельзя объяснить без траектории. 12 из13 потерь A пришли из входов75..90s, одна —53s.

**Уточнение об official:** все указанные совпадения относятся к полю outcome_official локальной базы. parse_resolution допускает ценовой порог 0.999/0.001 без обязательного финального oracle-status (см.H9); независимую on-chain финальность каждого исхода этот аудит не подтверждает.

13/13 исходов каждого бота имеют сохранённый официальный результат и совпадают с ним; не обнаружено evidence, что конкретно эти потери — ошибочный local verdict.
Пример пользователя найден у B: BUY id6633, UP90¢, +2.9bps,55s → SETTLE id6635, −$100.78, window5971443, official DOWN. У B по проекту нет cut; это не доказательство неудачного SELL.
Проверенные .log относятся к сентябрю; терминальный файл279136 показывает запуск05.10, но не содержит tick-by-tick trajectories рассматриваемых потерь. Старые CSV/JSON data/_stats* не использовались как источник истины и их пишущие скрипты не запускались.

## N. Settings Audit

Все текущие значения подтверждены DB и API; история изменений параметров НЕ сохраняется, поэтому нельзя доказать неизменность настроек за всю выборку.

| UI setting / params key | A | B | Default A/B | Validation | Семантика |
| --- | --- | --- | --- | --- | --- |
| Enter in last (s) / entry_window_s | 90 | 60 | 90 / 55 | 15..240 float | Верхняя граница remaining, нижняя hard-coded10s |
| Min projected margin / min_margin_bps | 0.3 | 2.5 | 1.5 / 2.5 | 0.1..50 float; UI step0.1 | abs(m)≥value |
| Max entry price / max_entry_cents | 95 | 90 | 88 / 88 | 50..95 float | ask≤cap; минимальной цены входа нет |
| Cut when against / cut_margin_bps | 0.5 | Нет | 0.5 / — | 0..10; UI step0.1 | Строго m<−c дляUP / m>c дляDOWN |
| Cut confirmation / cut_confirm_s | 2 | Нет | 2 / — | 0..15 float | now−adverse_since≥s*1000 |
| Order size / order_usd | 100 | 100 | 1 / 1 | 1..500 float | USD notional до комиссии; cap ask_size |

DB field bots.data.params.key; API JSON params.key; frontend input.dataset.key формируется из catalog /api/strategies. При создании defaults объединяются с прошедшими clamp настройками. Unknown keys игнорируются.
sanitize_params приводит к float и ограничивает min/max, но не квантует UI step и не принуждает целые секунды. UI step не серверная точность.
POST /api/bots/{id}/update обновляет RAM-словарь и SQLite сразу; restart не нужен. Cut settings могут начать действовать на уже открытой позиции/старом timer без reset.
LOT «% capital» на frontend пересчитывается в фиксированный order_usd при Apply; постоянного backend процентного риск-сайзинга это не создаёт.
Hard-coded overrides: minimum10s entry, один entry, profit99, spread≤20¢, min1share, fee0.07, settlement waits. Ни entry price floor, ни max-dollar-loss в этих стратегиях нет.
Стартовый seed A (если нет одноимённой стратегии) — другой источник defaults: min_margin0.3, cap95, order1. Текущий A order100; нельзя выдавать seed за фактическую конфигурацию.

## O. P&L / Position Sizing

p = ask/100, q = min(order_usd/p, ask_size), если ask_size задан; иначе q=order_usd/p.
entry_fee = 0.07*q*min(p,1−p).
BUY cash -= q*p + entry_fee; BUY.pnl записывает отрицательный денежный расход, **не отдельный реализованный убыток**.
SELL при bid b (в долларах): cash += q*b−0.07*q*min(b,1−b);
realized pnl = этот приход − (q*p+entry_fee).
SETTLE WIN: q−cost−entry_fee. SETTLE LOSS: −cost−entry_fee. Дополнительной settlement fee модель не берёт.
Поэтому суммирование pnl всех BUY+SELL/SETTLE ошибочно дважды учитывает вход. Здесь суммируются только завершения (+ADJUST при наличии).
Fee формула описывает **именно код симуляции**; актуальность относительно тарифа биржи в этой фазе не проверялась.

Размер убытка различается из-за ask_size partial buy, лота, входной комиссии и частичного возврата денег при cut. Paper SELL частичных исполнений не поддерживает.
Подтверждённые примеры B: 90¢*111.111… +fee = $100.78;90¢*70+fee=$63.49;90¢*55+fee=$49.88;88¢*25+fee=$22.21;86¢*20+fee=$17.40.
Это полные потери фактически купленного количества, не обязательно полные $100. Лот не добирает недостающий ask volume в следующих проходах.
Нехватка cash для cost+fee отменяет вход целиком, не уменьшает order_usd. При хорошем стакане order100 может потерять до107$ на p≤0.5.

Геометрия при notional$100, достаточной ask liquidity. Одинакова для A/B, кроме доступности cut. Sell50 — иллюстрация произвольного cut bid, не прогноз и не гарантированный уровень стопа.

| Entry ¢ | q shares | Entry fee $ | Profit sell99 $ | Profit settleWIN $ | SettleLOSS $ | Sell at50 $ | WR break-even sell99/fullLOSS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 40 | 250.0000 | 7.00 | 140.32 | 143.00 | -107.00 | 9.25 | 43.26% |
| 50 | 200.0000 | 7.00 | 90.86 | 93.00 | -107.00 | -14.00 | 54.08% |
| 60 | 166.6667 | 4.67 | 60.22 | 62.00 | -104.67 | -27.17 | 63.48% |
| 70 | 142.8571 | 3.00 | 38.33 | 39.86 | -103.00 | -36.57 | 72.88% |
| 75 | 133.3333 | 2.33 | 29.57 | 31.00 | -102.33 | -40.33 | 77.58% |
| 80 | 125.0000 | 1.75 | 21.91 | 23.25 | -101.75 | -43.63 | 82.28% |
| 82 | 121.9512 | 1.54 | 19.11 | 20.41 | -101.54 | -44.83 | 84.16% |
| 85 | 117.6471 | 1.24 | 15.15 | 16.41 | -101.24 | -46.53 | 86.98% |
| 87 | 114.9425 | 1.05 | 12.67 | 13.90 | -101.05 | -47.60 | 88.86% |
| 88 | 113.6364 | 0.95 | 11.47 | 12.68 | -100.95 | -48.11 | 89.80% |
| 90 | 111.1111 | 0.78 | 9.14 | 10.33 | -100.78 | -49.11 | 91.68% |

Для фактического capA95¢: full100 notional → win sell99 ≈$3.77, full loss≈$100.37; нужны примерно26.6 таких выигрыша для одной полной потери. Cut может уменьшить потерю, но фиксированной нижней границы у него нет.

Проверка учёта:
- все479/50 BUY сопоставлены с одним завершением; side/window совпадают; незакрытых BUY нет; duplicate windows нет.
- pending_official пуст; ADJUST/VOID отсутствуют.
- net журнала A−764.63 против cash delta−764.686068; B−219.09 против−219.062064. Несколько центов разницы ожидаемы из-за round(pnl,2) на каждой записи при float cash.
- Wins/losses из завершений точно совпали с persisted stats. Формульных ошибок знака DOWN или cents/dollars в текущем paper buy/sell не обнаружено.
- Средние gross_profit/loss ниже — суммы положительных/отрицательных **net-of-model-fees** P&L, не прибыль до комиссии.
- Max drawdown ниже — от последовательности реализованных P&L по времени закрытия, от стартового equity1000, не внутрисделочный mark-to-market. Абсолютная просадка может превышать1000, если equity до этого выросло.

## P. Historical Performance — Bot A

Период доступного журнала: 2026-10-05T12:06:40.103645+00:00 — 2026-10-08T08:43:51.265302+00:00. Он начинается RESET05.10; создан бот в августе, но прежние сделки удалены reset и не восстановлены.

| Metric | Value |
| --- | --- |
| Rows / completed | 964 / 479 |
| W / L / zero | 378 / 101 / 0 |
| Win rate | 78.91% |
| Gross profit $ | 4516.63 |
| Gross loss $ | 5281.26 |
| Net P&L $ | -764.63 |
| Average / median $ | -1.60 / 4.15 |
| Largest win / loss $ | 219.33 / -104.67 |
| Profit factor | 0.8552 |
| Avg Win / Avg Loss magnitude $ | 11.95 / 52.29 |
| Payoff ratio W/L | 0.2285 |
| Break-even WR | 81.40% |
| SETTLE WIN / LOSS | 5 / 13 |
| Profit-target / cut exits | 372 / 89 |
| Mean entry / abs margin | 81.34¢ / 3.228bps |
| Realized max drawdown $ | 1322.03 |
| BUY/close anomalies | 0 |
| Missing round / missing entry reason | 0 / 0 |

| Exit group | N | W | L | Net $ | Mean $ |
| --- | --- | --- | --- | --- | --- |
| cut | 89 | 1 | 88 | -4434.42 | -49.82 |
| profit | 372 | 372 | 0 | 4420.85 | 11.88 |
| SETTLE | 18 | 5 | 13 | -751.06 | -41.73 |

| Side | N | WR % | Net $ | Avg $ | SETTLE LOSS |
| --- | --- | --- | --- | --- | --- |
| UP | 236 | 79.24 | -210.63 | -0.89 | 7 |
| DOWN | 243 | 78.60 | -554.00 | -2.28 | 6 |

| BUY day UTC | N | WR % | Net $ | SETTLE LOSS |
| --- | --- | --- | --- | --- |
| 2026-10-05 | 91 | 86.81 | 304.04 | 3 |
| 2026-10-06 | 171 | 75.44 | -1258.99 | 6 |
| 2026-10-07 | 161 | 80.75 | 583.87 | 2 |
| 2026-10-08 | 56 | 71.43 | -393.55 | 2 |

Control events: RESET 2026-10-05T12:06:40.103Z; START 2026-10-05T12:06:43.377Z; STOP 2026-10-06T08:35:03.421Z; START 2026-10-06T08:37:20.828Z; STOP 2026-10-06T10:41:12.589Z; START 2026-10-06T10:41:36.101Z.
## Q. Historical Performance — Bot B

Период доступного журнала: 2026-10-05T12:06:25.247040+00:00 — 2026-10-08T08:44:40.867785+00:00. Он начинается RESET05.10; создан бот в августе, но прежние сделки удалены reset и не восстановлены.

| Metric | Value |
| --- | --- |
| Rows / completed | 106 / 50 |
| W / L / zero | 37 / 13 / 0 |
| Win rate | 74.00% |
| Gross profit $ | 557.99 |
| Gross loss $ | 777.08 |
| Net P&L $ | -219.09 |
| Average / median $ | -4.38 / 8.63 |
| Largest win / loss $ | 87.25 / -101.54 |
| Profit factor | 0.7181 |
| Avg Win / Avg Loss magnitude $ | 15.08 / 59.78 |
| Payoff ratio W/L | 0.2523 |
| Break-even WR | 79.85% |
| SETTLE WIN / LOSS | 3 / 13 |
| Profit-target / cut exits | 34 / 0 |
| Mean entry / abs margin | 79.44¢ / 4.340bps |
| Realized max drawdown $ | 429.67 |
| BUY/close anomalies | 0 |
| Missing round / missing entry reason | 0 / 0 |

| Exit group | N | W | L | Net $ | Mean $ |
| --- | --- | --- | --- | --- | --- |
| profit | 34 | 34 | 0 | 521.92 | 15.35 |
| SETTLE | 16 | 3 | 13 | -741.01 | -46.31 |

| Side | N | WR % | Net $ | Avg $ | SETTLE LOSS |
| --- | --- | --- | --- | --- | --- |
| UP | 26 | 73.08 | -219.34 | -8.44 | 7 |
| DOWN | 24 | 75.00 | 0.25 | 0.01 | 6 |

| BUY day UTC | N | WR % | Net $ | SETTLE LOSS |
| --- | --- | --- | --- | --- |
| 2026-10-05 | 6 | 100.00 | 99.00 | 0 |
| 2026-10-06 | 16 | 50.00 | -225.73 | 8 |
| 2026-10-07 | 22 | 81.82 | -124.34 | 4 |
| 2026-10-08 | 6 | 83.33 | 31.98 | 1 |

Control events: RESET 2026-10-05T12:06:25.247Z; START 2026-10-05T12:06:35.722Z; STOP 2026-10-07T08:10:43.037Z; START 2026-10-07T08:10:58.477Z; STOP 2026-10-08T08:27:40.144Z; START 2026-10-08T08:37:06.414Z.
## R. Entry Price Analysis

EXACT по сохранённым ценам (они округлены до0.1¢); диапазоны полуоткрытые: 50–59 означает [50,60).
Largest loss показывает минимальный отрицательный P&L, а если потерь нет — «нет».

### Bot A

| Entry ¢ | N | W | L | WR % | Net $ | Avg $ | Largest loss $ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <50 | 17 | 9 | 8 | 52.94 | 359.92 | 21.17 | -102.12 |
| 50–59 | 23 | 12 | 11 | 52.17 | -58.60 | -2.55 | -95.45 |
| 60–69 | 46 | 25 | 21 | 54.35 | -227.84 | -4.95 | -104.67 |
| 70–74 | 33 | 19 | 14 | 57.58 | -246.22 | -7.46 | -102.86 |
| 75–79 | 48 | 34 | 14 | 70.83 | -426.54 | -8.89 | -100.99 |
| 80–84 | 60 | 47 | 13 | 78.33 | -189.78 | -3.16 | -100.40 |
| 85–87 | 37 | 31 | 6 | 83.78 | -6.86 | -0.19 | -101.24 |
| 88–89 | 39 | 36 | 3 | 92.31 | 169.93 | 4.36 | -49.32 |
| 90+ | 176 | 165 | 11 | 93.75 | -138.64 | -0.79 | -100.53 |

### Bot B

| Entry ¢ | N | W | L | WR % | Net $ | Avg $ | Largest loss $ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <50 | 3 | 2 | 1 | 66.67 | 55.93 | 18.64 | -1.99 |
| 50–59 | 3 | 3 | 0 | 100.00 | 120.30 | 40.10 | нет |
| 60–69 | 0 | 0 | 0 | — | 0.00 | — | нет |
| 70–74 | 7 | 5 | 2 | 71.43 | 4.03 | 0.58 | -38.98 |
| 75–79 | 4 | 4 | 0 | 100.00 | 86.27 | 21.57 | нет |
| 80–84 | 9 | 8 | 1 | 88.89 | 3.06 | 0.34 | -101.54 |
| 85–87 | 6 | 5 | 1 | 83.33 | 21.20 | 3.53 | -17.40 |
| 88–89 | 5 | 2 | 3 | 40.00 | -149.37 | -29.87 | -100.95 |
| 90+ | 13 | 8 | 5 | 61.54 | -360.51 | -27.73 | -100.78 |

B: ≥88¢ —18 trades, net−$509.88; <88¢ —32 trades, net+$290.79. Это существенный наблюдаемый вклад дорогих входов в убыток именно B.
A: ≥90¢ —176 trades,93.75% WR,net−$138.64; 88–89¢ —39 trades,+$169.93. Основная потеря не ограничивается самыми дорогими входами:75–79¢ −$426.54.
Низкие цены сами по себе не признаны плохими: A<50¢ +$359.92 при WR52.94%, B<50¢ +$55.93 (N3).
Это описания фактических групп, НЕ доказательство, что установка другого cap воспроизвела бы этот результат. Лот/size, время и margin конфундируют связь.

## S. Projected Margin Analysis

APPROXIMATION для принадлежности к true-margin группам: reason хранит один десятичный знак. Суммы и N групп по записанному значению — EXACT. Граница3.0, например, может включать исходный2.96.
Добавлены <2bps группы из-за фактического thresholdA0.3. Во всех529 закрытых сделках margin reason распознан.

### Bot A

| |m| bps | N | WR % | Net $ | Avg $ | SETTLE LOSS |
| --- | --- | --- | --- | --- | --- |
| <0.3 | 0 | — | 0.00 | — | 0 |
| 0.3–0.99 | 84 | 57.14 | -656.38 | -7.81 | 3 |
| 1–1.99 | 93 | 74.19 | 16.04 | 0.17 | 4 |
| 2–2.49 | 48 | 79.17 | -356.01 | -7.42 | 1 |
| 2.5–2.99 | 49 | 79.59 | -56.78 | -1.16 | 1 |
| 3–3.99 | 67 | 83.58 | -300.04 | -4.48 | 1 |
| 4–4.99 | 55 | 90.91 | 86.74 | 1.58 | 1 |
| 5–7.49 | 54 | 94.44 | 193.48 | 3.58 | 2 |
| 7.5–9.99 | 15 | 93.33 | 75.94 | 5.06 | 0 |
| 10+ | 14 | 92.86 | 232.38 | 16.60 | 0 |

### Bot B

| |m| bps | N | WR % | Net $ | Avg $ | SETTLE LOSS |
| --- | --- | --- | --- | --- | --- |
| <0.3 | 0 | — | 0.00 | — | 0 |
| 0.3–0.99 | 0 | — | 0.00 | — | 0 |
| 1–1.99 | 0 | — | 0.00 | — | 0 |
| 2–2.49 | 0 | — | 0.00 | — | 0 |
| 2.5–2.99 | 24 | 75.00 | -164.92 | -6.87 | 6 |
| 3–3.99 | 14 | 64.29 | -187.71 | -13.41 | 5 |
| 4–4.99 | 3 | 100.00 | 102.50 | 34.17 | 0 |
| 5–7.49 | 3 | 66.67 | 33.54 | 11.18 | 1 |
| 7.5–9.99 | 2 | 50.00 | -55.38 | -27.69 | 1 |
| 10+ | 4 | 100.00 | 52.88 | 13.22 | 0 |

A0.3–0.99bps:84 сделки,−$656.38 — сильный наблюдаемый проблемный сегмент, но 1–1.99bps даёт+$16.04, зависимость не строго монотонная.
B2.5–3.99bps:38 сделок,−$352.63; ≥4bps:12 сделок,+$133.54, однако7.5–9.99bps имеет−$55.38 при N2. Большой margin не гарантирует победу.
Разделение UP/DOWN целиком приведено в P/Q. Детальные ячейки side×margin требуют осторожности из-за малых N; знак используется только для выбора стороны, сила в таблицах абсолютная.


### Дополнительное разделение margin по UP/DOWN

EXACT по округлённым журналам; диапазоны [lo,hi). У B группа 2–3 фактически начинается с2.5. Малые N не дают причинных выводов.

| Bot | Side | abs(m) bps | N | WR % | Net $ |
|---|---|---|---|---|---|
| b_0d55c464 | UP | <1 | 42 | 52.38 | -150.33 |
| b_0d55c464 | UP | 1–2 | 45 | 75.56 | -44.65 |
| b_0d55c464 | UP | 2–3 | 49 | 85.71 | -60.20 |
| b_0d55c464 | UP | 3–5 | 55 | 83.64 | -147.60 |
| b_0d55c464 | UP | 5–10 | 40 | 95.00 | 97.54 |
| b_0d55c464 | UP | 10+ | 5 | 100.00 | 94.61 |
| b_0d55c464 | DOWN | <1 | 42 | 61.90 | -506.05 |
| b_0d55c464 | DOWN | 1–2 | 48 | 72.92 | 60.69 |
| b_0d55c464 | DOWN | 2–3 | 48 | 72.92 | -352.59 |
| b_0d55c464 | DOWN | 3–5 | 67 | 89.55 | -65.70 |
| b_0d55c464 | DOWN | 5–10 | 29 | 93.10 | 171.88 |
| b_0d55c464 | DOWN | 10+ | 9 | 88.89 | 137.77 |
| b_384a1650 | UP | <1 | 0 | — | 0.00 |
| b_384a1650 | UP | 1–2 | 0 | — | 0.00 |
| b_384a1650 | UP | 2–3 | 15 | 73.33 | -105.94 |
| b_384a1650 | UP | 3–5 | 8 | 75.00 | -68.35 |
| b_384a1650 | UP | 5–10 | 2 | 50.00 | -55.38 |
| b_384a1650 | UP | 10+ | 1 | 100.00 | 10.33 |
| b_384a1650 | DOWN | <1 | 0 | — | 0.00 |
| b_384a1650 | DOWN | 1–2 | 0 | — | 0.00 |
| b_384a1650 | DOWN | 2–3 | 9 | 77.78 | -58.98 |
| b_384a1650 | DOWN | 3–5 | 9 | 66.67 | -16.86 |
| b_384a1650 | DOWN | 5–10 | 3 | 66.67 | 33.54 |
| b_384a1650 | DOWN | 10+ | 3 | 100.00 | 42.55 |

## T. Price × Margin Analysis

В каждой ячейке N / WR% / net$. «—» = нет наблюдений; * = N<10, описательная малая выборка, не надёжный параметрический вывод.
Диапазоны margin [2,3),[3,5),[5,10),≥10; <2 добавлен для A. Строка90+ включает цены до95 уA, ровно90 уB.

### Bot A

| Entry ¢ | <2bps | 2–3bps | 3–5bps | 5–10bps | 10+bps |
| --- | --- | --- | --- | --- | --- |
| <60 | 33 / 51.5 / 240.39 | 3* / 66.7 / 40.17 | 2* / 50.0 / -74.11 | 1* / 0.0 / -1.99 | 1* / 100.0 / 96.86 |
| 60–74 | 60 / 53.3 / -447.63 | 12 / 58.3 / -72.87 | 3* / 66.7 / -43.50 | 2* / 50.0 / 31.27 | 2* / 100.0 / 58.67 |
| 75–84 | 52 / 73.1 / -474.99 | 30 / 76.7 / -150.77 | 21 / 71.4 / -72.83 | 3* / 100.0 / 42.46 | 2* / 100.0 / 39.81 |
| 85–87 | 10 / 90.0 / 35.25 | 11 / 81.8 / -113.87 | 10 / 80.0 / 18.00 | 6* / 83.3 / 53.76 | — |
| 88–89 | 8* / 100.0 / 49.10 | 13 / 92.3 / 50.30 | 13 / 92.3 / 38.19 | 3* / 100.0 / 24.32 | 2* / 50.0 / 8.02 |
| 90+ | 14 / 92.9 / -42.46 | 28 / 85.7 / -165.75 | 73 / 93.2 / -79.05 | 54 / 98.1 / 119.60 | 7* / 100.0 / 29.02 |

### Bot B

| Entry ¢ | <2bps | 2–3bps | 3–5bps | 5–10bps | 10+bps |
| --- | --- | --- | --- | --- | --- |
| <60 | — | 3* / 100.0 / 74.16 | 1* / 100.0 / 87.25 | 1* / 0.0 / -1.99 | 1* / 100.0 / 16.81 |
| 60–74 | — | 3* / 66.7 / -11.96 | 3* / 66.7 / 7.96 | — | 1* / 100.0 / 8.03 |
| 75–84 | — | 7* / 100.0 / 91.59 | 3* / 66.7 / -54.47 | 2* / 100.0 / 34.50 | 1* / 100.0 / 17.71 |
| 85–87 | — | 5* / 80.0 / 13.49 | 1* / 100.0 / 7.71 | — | — |
| 88–89 | — | 2* / 0.0 / -148.92 | 3* / 66.7 / -0.45 | — | — |
| 90+ | — | 4* / 50.0 / -183.28 | 6* / 66.7 / -133.21 | 2* / 50.0 / -54.35 | 1* / 100.0 / 10.33 |

Цена в одиночку не объясняет A: в90+ и margin3–5, N73, net−79.05; в90+ и5–10, N54, net+119.60. УB при90¢ сегменты2–3 и3–5 отрицательны, но N4/N6.
Это основание исследовать совместную калибровку, а не утверждать превосходство модели price×margin: out-of-sample сравнения, калибровки вероятности и контроля размера позиции не выполнялись.

## U. Entry Timing Analysis

Left восстановлен из BUY.reason (округление до1s), а не как end−trades.t: последний — другой, локальный timebase. Группы возле границ приблизительны.

### Bot A

| Left s | N | WR % | Net $ | SETTLE LOSS N | SETTLE LOSS % |
| --- | --- | --- | --- | --- | --- |
| 0–29 | 0 | — | 0.00 | 0 | — |
| 30–39 | 1 | 100.00 | 94.90 | 0 | 0.00 |
| 40–49 | 1 | 100.00 | 1.98 | 0 | 0.00 |
| 50–59 | 10 | 90.00 | 24.86 | 1 | 10.00 |
| 60–74 | 24 | 91.67 | 18.71 | 0 | 0.00 |
| 75–90 | 443 | 77.88 | -905.08 | 12 | 2.71 |
| >90 | 0 | — | 0.00 | 0 | — |

### Bot B

| Left s | N | WR % | Net $ | SETTLE LOSS N | SETTLE LOSS % |
| --- | --- | --- | --- | --- | --- |
| 0–29 | 1 | 100.00 | 10.33 | 0 | 0.00 |
| 30–39 | 1 | 0.00 | -47.97 | 1 | 100.00 |
| 40–49 | 5 | 80.00 | 18.17 | 1 | 20.00 |
| 50–59 | 31 | 67.74 | -355.33 | 10 | 32.26 |
| 60–74 | 12 | 91.67 | 155.71 | 1 | 8.33 |
| 75–90 | 0 | — | 0.00 | 0 | — |
| >90 | 0 | — | 0.00 | 0 | — |

A75–90s:443/479 сделок,−$905.08; остальные36,+$140.45. Выборка поздних входов мала и отобрана тем, что ранний вход не прошёл; это НЕ доказательство причинного эффекта задержки.
B50–59s:31,−$355.33; записанные60s:12,+$155.71. Поэтому утверждение «любые T−60 входы плохи» не подтверждено. Группа60s может содержать фактические59.5..60s.
У A доT−60 фиксированной части финального TWAP при L60 вообще нет — это механическое объяснение риска раннего входа, независимое от небольшой выборки.

## V. Bot A vs Bot B


| Metric | A b_0d55c464 | B b_384a1650 |
| --- | --- | --- |
| Strategy | twap_lockin_cut | twap_lockin |
| Completed | 479.00 | 50.00 |
| Win rate % | 78.91 | 74.00 |
| Net $ | -764.63 | -219.09 |
| Avg $ | -1.60 | -4.38 |
| Profit factor | 0.86 | 0.72 |
| Mean entry ¢ | 81.34 | 79.44 |
| Mean \|margin\| bps | 3.23 | 4.34 |
| Avg Win $ | 11.95 | 15.08 |
| Avg Loss magnitude $ | 52.29 | 59.78 |
| Break-even WR % | 81.40 | 79.85 |
| Payoff W/L | 0.23 | 0.25 |
| Realized max DD $ | 1322.03 | 429.67 |
| Largest loss $ | -104.67 | -101.54 |
| SETTLE LOSS rate | 2.71% | 26.00% |
| Cut exit rate | 18.58% | 0% |


EV = P(win)*AvgWin − P(loss)*AvgLoss; здесь zero=0.
A:0.789144*11.948757−0.210856*52.289703=−$1.5963 на сделку.
B:0.74*15.080811−0.26*59.775385=−$4.3818.
Break-even = AvgLoss/(AvgWin+AvgLoss). 80% побед не спасает, если средняя потеря более чем в4 раза больше выигрыша.
Дневной пример B07.10:WR81.82%, net−$124.34.

Общая календарная выборка после RESET почти совпадает, но у A479 входов против50; настройки/время/стороны/размеры fill различаются. Все50 раундов B присутствуют уA:

| Только50 общих windows | A | B |
| --- | --- | --- |
| N | 50 | 50 |
| WR % | 54.00 | 74.00 |
| Net $ | -283.19 | -219.09 |
| Mean entry ¢ | 75.04 | 79.44 |
| Cut exits | 21 | 0 |
| SETTLE LOSS | 2 | 13 |

Даже на общих раундах это НЕ matched entry test: стороны и входы могут различаться. A выглядит лучше по avg loss/settle-loss rate и хуже по абсолютному net; на общих windows netA−283.19 хуже netB−219.09. Причинный вывод «cut улучшает/портит стратегию» по этим двум запускам недопустим.
89 cut выходов−4434.42 не равны «вреду cut»: без cut часть могла потерять весь вход, часть восстановиться. Нужен trajectory replay или явно ограниченный контрфактуал удержания тех же купленных позиций.

## W. Data/Timing Alignment


| Metric | Источник | Частота | Timestamp basis / риск | A/B |
| --- | --- | --- | --- | --- |
| PTB | last official RTDS TWAP60; fallbacks | Один latch /300s | poly clock floor; нет source-ts gate | Один объект |
| BTC spot | Binance trade WS | На каждый tick | exchange ts ребейзится receive-source offsets | Общий; не входит в margin |
| Chainlink | RTDS crypto_prices_chainlink | Ожидаемо ~1s, не гарантия | Payload epoch ms, monotonic accepted samples | Общий |
| Live TWAP | RTDS sixty, иначе local rolling | При RTDS updates | Official value cached без age guard | Общий |
| Projected final | local rolling Chainlink | Пересчёт на eval ~≤4Hz | Оценка на end; holding last price | Одинаков |
| Entry signal | (projected−PTB)/PTB*10000 | При eval | Poly now для window eligibility | Разные thresholds |
| Cut signal | Та же проекция | При eval, не новый sample | Timer poly_now; не monotonic wall clock | ТолькоA |
| Quote | CLOB market WS top | При изменении best price/size | Local receive→poly; нет slug в quote | Общий |
| Settlement | Gamma outcome / local fallback | Poll4s + backscan | DB asset+window_id, market slug epoch | Общая функция |
| Trade log t | time.time()*1000 | По событию | LOCAL, не poly_now entry_t | По bot_id |

OffsetTracker хранит240 receive−source samples и берёт минимум (clock skew плюс минимальная сетевая задержка); это не точная синхронизация часов.
В RTDS offset обновляется по Chainlink/TWAP30/TWAP60, объединённым в одном tracker. Discovery Gamma использует локальный UTC datetime, pipeline — скорректированное poly time. Возможен разный текущий round на границе.
После _roll_window last_quote не очищается; _on_new_market очищает только _up_mids, а не last_quote. Quote payload не несёт window/slug.
RTDS silent-stall30s относится к потоку parseable frames в целом. Один живой topic может скрыть замерший Chainlink/TWAP60. _last_ref_ms обновляется на любом live kind, health RTDS тоже не доказывает свежесть Chainlink.
CLOB health основан на последнем изменении top-of-book (60s), не отдельном heartbeat; тихий стакан может быть нормальным. Но evaluator вообще не читает этот health.
Исторические left и window позволяют приблизительно оценить локальный clock offset, но восстановить per-tick задержки/обрывы невозможно.

## X. Bugs and Suspicious Behavior

Severity относится к достоверности paper результатов/риску стратегии; конкретные потери не приписываются багу без доказательства. **CRITICAL подтверждённых не найдено.**


| ID / severity | Доказательство | Влияние / границы |
| --- | --- | --- |
| H1 HIGH — stale oracle accepted | bots.py:1199; metrics.py:85; pipeline.py:218,615 | Нет проверки возраста/полноты; один старый sample даёт прогноз бесконечно. ДляA stale margin также может подтвердить или не дать cut. Синтетический пример подтверждён. |
| H2 HIGH — PTB carry без timestamp | pipeline.py:615–692 | twap60 кэш переносится без проверки принадлежности закрываемому окну/age. Source=official не доказывает правильную границу; возможен неверный сигнал обоих. |
| H3 HIGH — wrong/stale quote market | bots.py:713–749,984; pipeline.py:705; polymarket.py:298,493 | Нет сверки quote window/token с позицией; old last_quote не очищается. При lag/discovery/disconnect возможно paper исполнение по чужому раунду. В раннем входеA риск не ограничен первыми2s, если market завис надолго. |
| H4 HIGH — paper sell ignores liquidity | bots.py:984–1000 | Вся позиция мгновенно по bid, без bid_size/depth. Завышает реализуемость cut/profit; BUY ограничивает ask_size, SELL нет. A/B могут повторно использовать один и тот же paper depth. |
| H5 HIGH — decisive-local not reconciled | bots.py:1042–1050,1093; _reconcile:1103 | При local \|margin\|≥5 provisional остаётсяFalse, official later не корректирует balance. В срезе0 несовпадений settlement с сохранённым official; риск доказан по ветке, инцидент не доказан. |
| H6 HIGH — failed cut re-arms delay | bots.py:1276–1282 | Результат sell игнорируется, timer сброшен даже при отсутствии bid. При восстановлении bid надо заново ждать confirm; синтетически подтверждено. |
| H7 HIGH — one-entry state lost on restart | bots.py:340,692,1189 | _rt не сохраняется; trade history не восстанавливает entries. После restart возможен повторный вход в уже торговавшийся round; timer открытой позиции тоже обнулён. В срезе повторных entry-window0. |
| H8 HIGH — trade/state non-atomic | bots.py:455,1163; storage.py:88,114 | Trade INSERT commit немедленный, bot JSON позже (throttle2s), без общей транзакции/recovery. Crash может оставить trade без позиции/денег или старую позицию после exit. В срезе парные аномалии0. |
| H9 HIGH — official-label по цене | feeds/polymarket.py:111 parse_resolution; _poll_resolution_once:333 | outcomePrices около 1/0 принимаются как resolved даже без umaResolutionStatus=resolved. Poll проверяет конец рынка, но не финальность oracle. Записанный outcome_official — результат этого парсера, а не независимо проверенный on-chain finality. Ложный исход в срезе не доказан. |
| M1 MEDIUM — stale/None confirmation gap | bots.py:1270–1282 | None не сбрасывает timer, замершие данные считаются повторными подтверждениями. Не гарантируется непрерывная цепочка новых adverse наблюдений. |
| M2 MEDIUM — serial evaluator blocking | bots.py:662,1002; HTTP timeout15s | await settlement lookup задерживает обработку второго бота; exception handler снаружи for пропускает остаток прохода. Исторический инцидент не доказан. |
| M3 MEDIUM — incomplete evidence | storage.py schema; bots.py:1163 | Не сохранены source ages/PTB source/forecast trajectory/failed attempts/settings version. ПричиныA settlement losses не реконструируемы. |
| M4 MEDIUM — reconciliation TTL | bots.py:1103 | Через15min pending удаляется даже без official, после этого поздняя поправка не применяется. |
| L1 LOW — округление/неясные counters | bots.py:1157–1184, public_view | Trade pnl округлён2digits, shares2, price1; rounds только settlements. Мелкий разрыв суммы P&L vs cash ожидаем. |
| L2 LOW — UI обещает small loss | STRATEGIES twap_lockin_cut | Нет $-стопа, риск почти полного убытка. Это недостаток формулировки/модели, не неверная формула margin. |

Не обнаружены: прямое протекание params/balance/position/cut timer между A/B; неверный bot_id вставки; неправильный dispatch B; DOWN sign inversion; ошибочный ×100/×10000.
Не проверялись реальные ордера: дубли real execution / on-chain fees / V2 в данном scope неприменимы.
H1/H2/H3 — отдельные узлы одной цепочки качества входных данных; нельзя суммировать их как число исторических инцидентов.

## Y. Backtest/Data Limitations

Классификация:
- **EXACT:** persisted identity, runtime API identity/settings; counts и сумма сохранённых rounded завершений; matching по bot_id/window/side; partition результатов по сохранённой цене; source code behavior.
- **APPROXIMATION:** истинные margin/time buckets, recovered projected_final, величины из округлённых shares; correspondence closed-round PTB к entry PTB; nominal quote execution vs реальная ликвидность.
- **FILTERED COUNTERFACTUAL:** взять только уже совершённые trades entry<88 и суммировать; не симуляция cap87, потому что бот мог бы войти позже/другой стороной/размером.
- **NOT RECONSTRUCTABLE:** точные траектории сигналов и cut timer; причины отсутствия SELL уA; новые entry_window/min_margin/cut_confirm/cap при полном replay; полная историческая точность PTB и latency; история настроек до среза.
Проценты маленьких групп — описательные. Контроль ложных открытий, интервалы неопределённости и out-of-sample в Phase1 не выполнялись.
History RESET05.10 уничтожила более ранние trades этих ID. MAX_TRADES_KEPT300 влияет на bot JSON/UI, но SQLite сохраняет все оставшиеся964/106 событий.
Стартовые seed/defaults не приписываются всему периоду. Старые экспортные файлы могут описывать другой срез и не прибавляются к SQLite.

Воспроизводимость запросов (обязательно отдельный параметр id):
```sql
SELECT data, updated_ms FROM bots WHERE id = ?;
SELECT * FROM trades WHERE bot_id = ? ORDER BY t ASC, id ASC;
SELECT * FROM rounds WHERE asset = 'BTC' AND window_id = ?;
```
Источник открыт mode=ro + query_only + BEGIN, приложение не импортировалось.
Pair algorithm: по времени/id BUY открывает pending только внутри одного bot_id; SELL/SETTLE/VOID закрывает; проверяются window_id+side; ADJUST присоединялся бы к своему завершению (в срезе0); START/STOP/RESET не считаются сделками. Проверены0 unmatched/duplicate pairs.
N completed = количество закрытых пар. W/L = pnl>0/<0. PF=sum positive/abs(sum negative). Avg/median по closed pnl. Settlement-loss rate = count(loss SETTLE)/N, не доля только среди settlements.
Матрицы и marginal группы не объединяют A/B; common-window comparison отдельно обозначен.

## Z. Phase 2 Investigation Priorities

1. **Сначала наблюдаемость и alignment:** записывать per-bot/per-round decision snapshots: точные PTB/source/timestamps, officialTWAP60, local projection, oracle age/coverage, quote market/age/depth, clock offsets, config version. Проверить timestamp-correct carry на референсе без смены стратегии.
2. **Изучить все13 settlement losses A:** после появления траекторий различить deadband, поздний разворот, отсутствие continuous confirm, bid=0, stale и задержки. Отдельно исследовать89 cut-выходов и контрфактуальное удержание тех же позиций.
3. **Сверить реалистичность исполнения/учёта:** bid depth, full vs partial fills, комиссии, stale token attribution, decisive-local correction и crash consistency. Не оптимизировать параметры на искажённом simulator.
4. **Контролируемое сравнение cut/no-cut с одинаковыми входами:** один поток, одинаковые параметры входа, round/side/size, разница только exit policy; оценка net EV/хвостов/drawdown out-of-sample.
5. **Исследовать price×margin×time:** проверять ранний A90s и слабыйA<1bps; дорогиеB≥88¢ и margin2.5–4; учитывать liquidity/notional и малыеN. Это кандидаты исследований, не выбранные production thresholds.

Ни min entry price, ни новые пороги, ни другие изменения не внесены. Phase1 завершена отчётом; дальнейшие изменения требуют отдельного разрешения.
