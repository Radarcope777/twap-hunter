# Telegram

**Not implemented.**

There is no Telegram bot token env var, no notifier module, and no report send in `BotManager` or `RealBotManager`. Operator alerts are:

- Dashboard activity log (levels: signal, skip, order, wait, error)
- Process stdout logs

Do not configure `TELEGRAM_BOT_TOKEN` expecting this app to use it. It will be ignored.

If a future release adds Telegram, document it only after code exists.
