# 009 Risk management

Implemented: `order_usd`, strategy clamps, one position (except dip book), time/spread/min-share filters, unread-cash withhold, FOK all-or-nothing, pending_op block, 99¢ lock, optional TP/SL cents, cut/switch on some strategies.

Not implemented: daily loss limit, exposure cap, STOP file, Telegram, FAK, order retry.

VOID after 10 minutes: position cleared, cash not refunded.

Fee near 50¢ is large because of the 7% taker curve.
