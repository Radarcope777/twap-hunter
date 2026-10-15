"""Polymarket CLOB client helpers — authenticated balance + order placement.

Uses the unified ``polymarket-client`` SDK (SecureClient). Price-channel
auth for PolyBolt is derived from the same API credentials.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from backend.accounts import decrypt_key
from backend.storage import storage

logger = logging.getLogger(__name__)

REAL_MIN_ORDER_SHARES = 1.0   # match paper / Polymarket $1 lots
_CLIENT_CACHE: dict[str, "ClobSession"] = {}
_POLYBOLT_AUTH: dict | None = None


@dataclass
class ClobSession:
    client: object
    signature_type: int
    funder: str
    balance: float
    v2: bool = False


def _parse_usdc(raw: str | int | None) -> float:
    try:
        return round(int(raw or 0) / 1_000_000.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _private_key(account: dict) -> str:
    pk = decrypt_key(account["encrypted_key"]).strip()
    return pk if pk.startswith("0x") else f"0x{pk}"


def _wallet(account: dict) -> str:
    return (
        account.get("funder_address")
        or account.get("proxy_wallet")
        or account.get("address")
        or ""
    )


def _read_balance(client) -> float:
    try:
        bal = client.get_balance_allowance(asset_type="COLLATERAL")
        return _parse_usdc(getattr(bal, "balance", None))
    except Exception as exc:
        logger.debug("_read_balance failed: %r", exc)
        return 0.0


def _eoa(private_key: str) -> str:
    from eth_account import Account

    pk = private_key.strip()
    if not pk.startswith("0x"):
        pk = f"0x{pk}"
    return Account.from_key(pk).address


def _l2_auth_from_private_key(private_key: str) -> dict:
    """Derive CLOB L2 apiKey/secret/passphrase. Values are never logged."""
    from polymarket import SecureClient

    pk = private_key.strip()
    if not pk.startswith("0x"):
        pk = f"0x{pk}"
    # Pass the signer EOA so create() does not deploy a new deposit wallet.
    client = SecureClient.create(private_key=pk, wallet=_eoa(pk))
    try:
        creds = client._ctx.credentials
        return {
            "apiKey": creds.key,
            "secret": creds.secret,
            "passphrase": creds.passphrase,
        }
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass


def discover_clob_auth(account: dict) -> ClobSession | None:
    """Open one SecureClient for the stored funder wallet."""
    from polymarket import SecureClient

    pk = _private_key(account)
    funder = _wallet(account) or _eoa(pk)
    try:
        client = SecureClient.create(private_key=pk, wallet=funder)
        cash = _read_balance(client)
        session = ClobSession(client, 0, funder or str(client._ctx.wallet), cash, v2=True)
        account["funder_address"] = session.funder
        storage.save_account(account)
        logger.info("clob auth for %s: balance=$%.2f", account.get("label"), cash)
        return session
    except Exception as exc:
        logger.warning("clob auth failed for %s: %s", account.get("label"), type(exc).__name__)
        return None


def polybolt_auth_reset() -> None:
    global _POLYBOLT_AUTH
    _POLYBOLT_AUTH = None


def polybolt_auth_payload() -> dict | None:
    """CLOB L2 trio for PolyBolt, derived by the SDK from a signer key.

    Matches the official PolyBolt example: private key in, L2 credentials out.
    Values are never logged.
    """
    global _POLYBOLT_AUTH
    if _POLYBOLT_AUTH is not None:
        return _POLYBOLT_AUTH
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    if pk:
        try:
            _POLYBOLT_AUTH = _l2_auth_from_private_key(pk)
            logger.info("polybolt credentials derived from POLYMARKET_PRIVATE_KEY")
            return _POLYBOLT_AUTH
        except Exception as exc:
            logger.warning("polybolt derive from env key failed: %s", type(exc).__name__)
    for account in storage.load_accounts().values():
        if not account.get("encrypted_key"):
            continue
        try:
            _POLYBOLT_AUTH = _l2_auth_from_private_key(_private_key(account))
            logger.info("polybolt credentials derived from account %s", account.get("id"))
            return _POLYBOLT_AUTH
        except Exception as exc:
            logger.warning("polybolt credentials failed: %s", type(exc).__name__)
    return None


def get_session(account: dict) -> ClobSession:
    cache_key = account.get("id") or account.get("proxy_wallet") or account.get("address")
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    session = discover_clob_auth(account)
    if session is None:
        raise RuntimeError("could not authenticate CLOB client")
    _CLIENT_CACHE[cache_key] = session
    return session


def make_client(account: dict):
    return get_session(account).client


def sync_clob_balance(account: dict) -> float:
    """Return collateral balance from CLOB, in USDC."""
    try:
        session = get_session(account)
        cash = _read_balance(session.client)
        session.balance = cash
        return cash
    except Exception as exc:
        logger.warning("sync_clob_balance failed: %r", exc)
        return 0.0


def _min_buy_usd(client, token_id: str, price: float) -> float:
    """Minimum USD for a CLOB buy given min_order_size."""
    min_shares = REAL_MIN_ORDER_SHARES
    try:
        ob = client.get_order_book(token_id=str(token_id))
        if ob:
            raw = ob.get("min_order_size") if isinstance(ob, dict) else getattr(ob, "min_order_size", None)
            if raw:
                min_shares = max(min_shares, float(raw))
    except Exception:
        pass
    if price <= 0:
        return min_shares
    return round(min_shares * price * 1.02, 2)


def token_share_balance(account: dict, token_id: str) -> float:
    """Conditional-token share balance for a market outcome."""
    session = get_session(account)
    bal = session.client.get_balance_allowance(
        asset_type="CONDITIONAL", token_id=str(token_id))
    try:
        return int(getattr(bal, "balance", 0) or 0) / 1_000_000.0
    except (TypeError, ValueError):
        return 0.0


def _num(v, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _order_dict(order) -> dict:
    return {
        "id": order.id,
        "status": order.status,
        "price": float(order.price),
        "size": float(order.original_size),
        "original_size": float(order.original_size),
        "size_matched": float(order.size_matched),
    }


def _trade_dict(trade) -> dict:
    return {
        "id": trade.id,
        "size": float(trade.size),
        "price": float(trade.price),
        "transaction_hash": trade.transaction_hash or "",
        "side": trade.side,
    }


def fetch_clob_order(account: dict, order_id: str) -> dict:
    """Authenticated CLOB order record (fill size, price, status)."""
    if not order_id:
        return {}
    try:
        raw = get_session(account).client.get_order(order_id=str(order_id))
        return _order_dict(raw)
    except Exception as exc:
        logger.debug("get_order %s: %r", order_id, exc)
        return {}


def fetch_clob_trades(account: dict, trade_ids: list[str] | None = None,
                      asset_id: str | None = None) -> list[dict]:
    """Authenticated CLOB trade records for this account."""
    session = get_session(account)
    out: list[dict] = []
    ids = [str(i) for i in (trade_ids or []) if i]
    queries = [{"id": tid} for tid in ids] or ([{"asset_id": str(asset_id)}] if asset_id else [{}])
    for query in queries:
        try:
            page = session.client.list_account_trades(**query).first_page()
            out.extend(_trade_dict(t) for t in page.items)
        except Exception as exc:
            logger.debug("list_account_trades %s: %r", query.get("id"), exc)
    seen = set()
    uniq = []
    for t in out:
        key = t.get("id") or t.get("transaction_hash") or id(t)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    return uniq


def resolve_fill(account: dict, order_resp: dict | None, side: str) -> dict:
    """Fetch the real CLOB fill after posting — never use local quote math."""
    import time as _time

    resp = order_resp if isinstance(order_resp, dict) else {}
    oid = resp.get("orderID") or resp.get("order_id") or resp.get("id")
    trade_ids = [str(x) for x in (resp.get("tradeIDs") or []) if x]
    is_buy = side.upper() == "BUY"

    order: dict = {}
    trades: list[dict] = []
    for _ in range(4):
        if oid:
            order = fetch_clob_order(account, str(oid)) or order
        if trade_ids:
            trades = fetch_clob_trades(account, trade_ids) or trades
        matched = _num(order.get("size_matched") or order.get("size"))
        if trades or matched > 0 or _num(resp.get("takingAmount") or resp.get("makingAmount")) > 0:
            if trades or matched > 0:
                break
        _time.sleep(0.35)

    making = _num(resp.get("makingAmount") or order.get("makingAmount"))
    taking = _num(resp.get("takingAmount") or order.get("takingAmount"))
    trade_size = sum(_num(t.get("size")) for t in trades)
    trade_notional = sum(_num(t.get("size")) * _num(t.get("price")) for t in trades)
    trade_fee = sum(_num(t.get("fee") or t.get("fee_amount") or t.get("taker_fee")) for t in trades)
    avg_px = (trade_notional / trade_size) if trade_size else 0.0

    if is_buy:
        shares = taking or trade_size or _num(order.get("size_matched") or order.get("original_size"))
        usd = making or trade_notional
    else:
        shares = making or trade_size or _num(order.get("size_matched") or order.get("original_size"))
        usd = taking or trade_notional

    price = avg_px or _num(order.get("price"))
    if (not price) and shares > 0 and usd > 0:
        price = usd / shares
    if usd <= 0 and shares > 0 and price > 0:
        usd = shares * price

    status = str(order.get("status") or resp.get("status") or "").upper()
    hashes = list(resp.get("transactionsHashes") or [])
    for t in trades:
        h = t.get("transaction_hash") or t.get("transactionHash")
        if h and h not in hashes:
            hashes.append(h)

    return {
        "order_id": oid,
        "status": status,
        "shares": shares,
        "usd": usd,
        "price": price,
        "price_cents": round(price * 100.0, 2) if price else None,
        "fee": trade_fee,
        "tx_hashes": hashes,
        "filled": shares > 0 and status not in ("CANCELLED", "CANCELED", "EXPIRED"),
    }


def place_market_order(
    account: dict,
    token_id: str,
    side: str,
    amount: float,
    price: float = 0,
) -> dict:
    """Place a FOK market order. amount = USD for BUY, shares for SELL."""
    from polymarket import RejectedOrder

    session = get_session(account)
    is_buy = side.upper() == "BUY"
    if is_buy:
        resp = session.client.place_market_order(
            token_id=str(token_id),
            side="BUY",
            amount=float(amount),
            max_price=float(price) if price > 0 else None,
            order_type="FOK",
        )
    else:
        resp = session.client.place_market_order(
            token_id=str(token_id),
            side="SELL",
            shares=float(amount),
            min_price=float(price) if price > 0 else None,
            order_type="FOK",
        )
    if isinstance(resp, RejectedOrder):
        raise RuntimeError(resp.message or resp.code)
    return {
        "orderID": resp.order_id,
        "status": resp.status,
        "makingAmount": str(resp.making_amount),
        "takingAmount": str(resp.taking_amount),
        "tradeIDs": list(resp.trade_ids),
        "transactionsHashes": list(resp.transactions_hashes),
        "success": True,
    }


def token_for_side(pipe, side: str) -> str | None:
    market = getattr(getattr(pipe, "polymarket", None), "market", None) or {}
    key = "up_token" if side.upper() == "UP" else "down_token"
    tok = market.get(key)
    return str(tok) if tok else None
