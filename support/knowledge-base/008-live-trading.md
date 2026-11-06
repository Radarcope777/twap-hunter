# 008 Live trading

URL `/real`. Add account (private key encrypted). Import paper bot. Start explicitly.

Orders: FOK only. BUY spends full `order_usd` at best ask. Geoblock and FOK kills are CLOB responses.

Settlement does not flatten-sell. Redeem pays winners; losers redeem to 0. Cash updates on next collateral sync.

Card Open PnL is wallet Data API mark for the asset, shared across bots on that account.

Two live bots same asset/account share tokens — they can consume each other’s shares.
