"""Polymarket CLOB client helpers — authenticated balance + order placement.

Uses the unified ``polymarket-client`` SDK (SecureClient). Price-channel
auth for PolyBolt is derived from the same API credentials.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from backend.accounts import decrypt_key
from backend.storage import storage

logger = logging.getLogger(__name__)

REAL_MIN_ORDER_SHARES = 1.0   # match paper / Polymarket $1 lots
SHARE_BASE_UNITS = 1_000_000  # Protocol V2 docs: 1_000_000 is one share
_CLIENT_CACHE: dict[str, "ClobSession"] = {}
_SESSION_LOCK = threading.Lock()
_POLYBOLT_AUTH: dict | None = None
REDEEM_BATCH = 5


class BalanceReadError(RuntimeError):
    """Share balance could not be read. Distinct from a confirmed zero."""


@dataclass
class ClobSession:
    client: object
    signature_type: int
    funder: str
    balance: float
    v2: bool = False


def _sdk_balance_field(raw):
    """BalanceAllowance.balance from an SDK object or dict. Missing is unread."""
    if raw is None:
        raise BalanceReadError("cash balance response missing balance")
    if isinstance(raw, dict):
        if "balance" not in raw:
            raise BalanceReadError("cash balance response missing balance")
        return raw["balance"]
    if hasattr(raw, "balance"):
        return getattr(raw, "balance")
    raise BalanceReadError("cash balance response missing balance")


def read_collateral_usdc(client) -> float:
    """CLOB COLLATERAL in USDC. Raises BalanceReadError if unread.

    A confirmed empty wallet is ``0.0``. Transport failures and malformed
    bodies are not treated as zero.
    """
    try:
        bal = client.get_balance_allowance(asset_type="COLLATERAL")
    except BalanceReadError:
        raise
    except Exception as exc:
        raise BalanceReadError(
            f"cash balance request failed: {type(exc).__name__}"
        ) from exc
    try:
        units = parse_share_base_units(_sdk_balance_field(bal))
    except BalanceReadError as exc:
        raise BalanceReadError(f"cash {exc}") from exc
    return round(units / SHARE_BASE_UNITS, 2)


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


def _env_builder_present() -> bool:
    return bool(
        os.environ.get("POLYMARKET_BUILDER_API_KEY", "").strip()
        and os.environ.get("POLYMARKET_BUILDER_SECRET", "").strip()
        and os.environ.get("POLYMARKET_BUILDER_PASSPHRASE", "").strip()
    )


def _builder_from_account(account: dict):
    """Stored or env Builder key for gasless redeem. None when trading-only."""
    from polymarket import BuilderApiKey

    blob = account.get("encrypted_builder")
    if blob:
        data = json.loads(decrypt_key(blob))
        return BuilderApiKey(
            key=data["key"], secret=data["secret"], passphrase=data["passphrase"],
        )
    key = os.environ.get("POLYMARKET_BUILDER_API_KEY", "").strip()
    secret = os.environ.get("POLYMARKET_BUILDER_SECRET", "").strip()
    passphrase = os.environ.get("POLYMARKET_BUILDER_PASSPHRASE", "").strip()
    if key and secret and passphrase:
        return BuilderApiKey(key=key, secret=secret, passphrase=passphrase)
    return None


def _close_session(session: ClobSession) -> None:
    close = getattr(session.client, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        pass


def _session_cache_key(account: dict) -> str:
    return account.get("id") or account.get("proxy_wallet") or account.get("address")


def drop_session(account: dict) -> None:
    """Close and forget the cached client so the next open can carry a new key."""
    cache_key = _session_cache_key(account)
    with _SESSION_LOCK:
        session = _CLIENT_CACHE.pop(cache_key, None)
    if session is not None:
        _close_session(session)


def discover_clob_auth(account: dict) -> ClobSession | None:
    """Open one SecureClient for the stored funder wallet."""
    from polymarket import SecureClient

    pk = _private_key(account)
    funder = _wallet(account) or _eoa(pk)
    api_key = None
    try:
        api_key = _builder_from_account(account)
    except Exception:
        logger.warning("builder key unreadable for %s", account.get("id"))
    try:
        kwargs = {"private_key": pk, "wallet": funder}
        if api_key is not None:
            kwargs["api_key"] = api_key
        client = SecureClient.create(**kwargs)
        try:
            cash = read_collateral_usdc(client)
        except BalanceReadError:
            cash = 0.0
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
    cache_key = _session_cache_key(account)
    with _SESSION_LOCK:
        cached = _CLIENT_CACHE.get(cache_key)
        if cached is not None:
            ctx_key = getattr(getattr(cached.client, "_ctx", None), "api_key", None)
            want_key = bool(account.get("encrypted_builder")) or _env_builder_present()
            if want_key and ctx_key is None:
                _CLIENT_CACHE.pop(cache_key, None)
                cached_to_close = cached
            else:
                cached_to_close = None
                return cached
        else:
            cached_to_close = None
        session = discover_clob_auth(account)
        if session is None:
            raise RuntimeError("could not authenticate CLOB client")
        _CLIENT_CACHE[cache_key] = session
    if cached_to_close is not None:
        _close_session(cached_to_close)
    return session


def make_client(account: dict):
    return get_session(account).client


def v2_redeem_position_id(token: str) -> str | None:
    """Return the token only when the SDK can redeem it as a V2 position id."""
    text = str(token or "").strip()
    if not text:
        return None
    try:
        from polymarket._internal.protocol import decode_v2_outcome_position_id

        decode_v2_outcome_position_id(text)
    except Exception:
        return None
    return text


def _safe_redeem_reason(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if any(word in lowered for word in ("secret", "passphrase", "private", "api_key", "apikey")):
        return type(exc).__name__
    if type(exc).__name__ == "UserInputError":
        return text[:160]
    return type(exc).__name__


def ensure_redeem_client(account: dict):
    """Client that can submit redeem. Deposit wallets get one stored builder key."""
    session = get_session(account)
    client = session.client
    ctx = getattr(client, "_ctx", None)
    if ctx is None:
        return client
    if getattr(ctx, "wallet_type", None) == "EOA" or getattr(ctx, "api_key", None) is not None:
        return client
    created = client.create_builder_api_key()
    from backend.accounts import encrypt_key

    account["encrypted_builder"] = encrypt_key(json.dumps({
        "key": created.key,
        "secret": created.secret,
        "passphrase": created.passphrase,
    }))
    storage.save_account(account)
    drop_session(account)
    return get_session(account).client


def _asset_conditions(rows) -> dict[str, str]:
    found: dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("asset_id") or "")
        cid = str(row.get("condition_id") or "")
        if pid and cid and pid not in found:
            found[pid] = cid
    return found


def collect_redeem_targets(queued, rows, held_tokens, skip_keys) -> list[dict]:
    """Resolved positions to redeem. Skips the token the bot still holds.

    A bare CLOB token id is not a V2 position id. Redeem those by the market
    condition id, taken from the queue or from a Data API row for that token.
    """
    held = {str(token) for token in held_tokens if token}
    skip = {str(key) for key in (skip_keys or [])}
    by_asset = _asset_conditions(rows)
    blocked_conditions = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("condition_id") or "")
        pid = str(row.get("asset_id") or "")
        if cid and pid in held:
            blocked_conditions.add(cid)
    out = []
    seen = set()

    def add(key, condition_id, position_id, label) -> None:
        key = str(key or "")
        condition_id = str(condition_id or "")
        position_id = str(position_id or "")
        if not condition_id and position_id:
            condition_id = by_asset.get(position_id, "")
        if not key or key in seen or key in skip:
            return
        if position_id and position_id in held:
            return
        if condition_id and condition_id in blocked_conditions:
            return
        redeem_position = None
        if not condition_id:
            redeem_position = v2_redeem_position_id(position_id)
            if not redeem_position:
                return
        seen.add(key)
        if condition_id:
            seen.add(condition_id)
        if position_id:
            seen.add(position_id)
        out.append({
            "key": key,
            "condition_id": condition_id or None,
            "position_id": None if condition_id else redeem_position,
            "label": label or key[:12],
        })

    for item in queued or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("condition_id") or "")
        pid = str(item.get("position_id") or "")
        add(cid or pid, cid, pid, item.get("label") or "settled position")
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("redeemable"):
            continue
        if float(row.get("size") or 0) <= 0:
            continue
        pid = str(row.get("asset_id") or "")
        cid = str(row.get("condition_id") or "")
        outcome = str(row.get("outcome") or "").upper()
        label = f"{outcome} {float(row.get('size') or 0):.4f} sh".strip()
        add(cid or pid, cid, pid, label)
    return out


def unredeemable_queue_items(queued, rows, targets, held_tokens, skip_keys) -> list[dict]:
    """Queue entries that can never be sent: no condition id and not a V2 position id.

    Held tokens and anything already targeted or in the retry pause stay queued.
    """
    targeted = {str(item.get("key") or "") for item in targets or []}
    skip = {str(key) for key in (skip_keys or [])}
    held = {str(token) for token in held_tokens if token}
    by_asset = _asset_conditions(rows)
    out = []
    seen = set()
    for item in queued or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("condition_id") or "")
        pid = str(item.get("position_id") or "")
        key = cid or pid
        if not key or key in targeted or key in skip or key in seen:
            continue
        seen.add(key)
        if pid and pid in held:
            continue
        resolved = cid or by_asset.get(pid, "")
        if resolved or v2_redeem_position_id(pid):
            continue
        out.append({"key": key, "label": item.get("label") or key[:12]})
    return out


def redeem_resolved(account: dict, *, condition_id: str | None = None,
                    position_id: str | None = None) -> dict:
    """Redeem one resolved market. Does not credit cash; the next balance sync does.

    ``condition_id`` redeems every outcome the wallet still holds. A lone
    ``position_id`` redeems that V2 outcome, including a losing one worth $0.
    """
    if condition_id:
        kwargs = {"condition_id": str(condition_id)}
    elif position_id:
        kwargs = {"position_id": str(position_id)}
    else:
        raise ValueError("redeem needs condition_id or position_id")
    client = ensure_redeem_client(account)
    from polymarket.errors import UserInputError

    try:
        outcome = client.redeem_positions(**kwargs).wait()
    except UserInputError as exc:
        if "no balance" in str(exc).lower():
            return {"ok": True, "already": True, "transaction_hash": None}
        raise
    tx = getattr(outcome, "transaction_hash", None) or getattr(outcome, "transactionHash", None)
    return {"ok": True, "already": False, "transaction_hash": tx}


def redeem_account_positions(account: dict, queued, held_tokens, skip_keys) -> dict:
    """Redeem up to a few resolved positions. Safe to run off the trading loop."""
    from backend.feeds.account_data import fetch_redeemable_positions, invalidate_snapshot

    try:
        rows = fetch_redeemable_positions(account)
    except Exception as exc:
        logger.warning("redeemable positions unread: %s", type(exc).__name__)
        rows = []
    targets = collect_redeem_targets(queued, rows, held_tokens, skip_keys)[:REDEEM_BATCH]
    drops = unredeemable_queue_items(queued, rows, targets, held_tokens, skip_keys)
    if not targets and not drops:
        return {"notes": [], "done": [], "skip": {}}
    notes = []
    done = []
    skip = {}
    now = time.time()
    redeemed = False
    if targets:
        try:
            ensure_redeem_client(account)
        except Exception as exc:
            notes.append({
                "level": "wait",
                "msg": f"REDEEM withheld: {_safe_redeem_reason(exc)}",
            })
        else:
            for target in targets:
                try:
                    result = redeem_resolved(
                        account,
                        condition_id=target.get("condition_id"),
                        position_id=target.get("position_id"),
                    )
                except Exception as exc:
                    notes.append({
                        "level": "wait",
                        "msg": f"REDEEM withheld: {_safe_redeem_reason(exc)} — {target['label']}",
                    })
                    skip[target["key"]] = now + 60
                    continue
                done.append(target["key"])
                skip[target["key"]] = now + 900
                redeemed = True
                if result.get("already"):
                    continue
                tx = str(result.get("transaction_hash") or "")
                short = f" tx {tx[:10]}" if tx else ""
                notes.append({"level": "order", "msg": f"REDEEM {target['label']}{short}"})
    for item in drops:
        done.append(item["key"])
        notes.append({
            "level": "wait",
            "msg": f"REDEEM skipped: token is not a v2 position id — {item['label']}",
        })
    if redeemed:
        try:
            invalidate_snapshot(account)
        except Exception:
            pass
    return {"notes": notes, "done": done, "skip": skip}


def sync_clob_balance(account: dict) -> float:
    """Return collateral USDC. Raises BalanceReadError if the CLOB read fails."""
    try:
        session = get_session(account)
    except Exception as exc:
        raise BalanceReadError(
            f"cash balance request failed: {type(exc).__name__}"
        ) from exc
    cash = read_collateral_usdc(session.client)
    session.balance = cash
    return cash


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


def protocol_version(raw) -> str:
    """Return 'v1' or 'v2'. Unknown/missing is an error, never a silent V1."""
    if not isinstance(raw, str) or not raw.strip():
        raise BalanceReadError("unknown position protocol version")
    version = raw.strip().lower()
    if version not in ("v1", "v2"):
        raise BalanceReadError(f"unsupported position protocol version {version!r}")
    return version


def conditional_asset_type(version: str) -> str:
    """CLOB asset_type for a position's own protocol, not the SDK flavor."""
    ver = protocol_version(version)
    return "CONDITIONAL-V2" if ver == "v2" else "CONDITIONAL"


_BASE_UNIT_INT = re.compile(r"[0-9]+$")


def parse_share_base_units(raw) -> int:
    """Return a non-negative integer of share base units from an SDK balance.

    SDK ``BalanceAllowance.balance`` is ``int``. Bool, float (including
    whole numbers, NaN, inf), None, and malformed strings are errors.
    A decimal-digit string is accepted only when it is a whole integer.
    """
    if raw is None:
        raise BalanceReadError("share balance response missing balance")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise BalanceReadError(
            f"share balance is not an integer base-unit count ({type(raw).__name__})"
        )
    if isinstance(raw, int):
        if raw < 0:
            raise BalanceReadError("share balance is negative")
        return raw
    text = raw.strip()
    if not _BASE_UNIT_INT.fullmatch(text):
        raise BalanceReadError("share balance is not an integer")
    return int(text)


def token_share_balance(account: dict, token_id: str, *, version: str | None) -> float:
    """Conditional-token share balance in shares (base units / 1_000_000).

    Raises BalanceReadError on unknown version, transport failure, or a
    malformed response. A confirmed zero holding is ``0.0``, not an empty
    order book.
    """
    asset_type = conditional_asset_type(version)
    if not token_id:
        raise BalanceReadError("missing token_id")
    try:
        session = get_session(account)
        bal = session.client.get_balance_allowance(
            asset_type=asset_type, token_id=str(token_id))
    except BalanceReadError:
        raise
    except Exception as exc:
        raise BalanceReadError(
            f"share balance request failed: {type(exc).__name__}"
        ) from exc
    if bal is None or not hasattr(bal, "balance"):
        raise BalanceReadError("share balance response missing balance")
    units = parse_share_base_units(getattr(bal, "balance"))
    return units / SHARE_BASE_UNITS


FILL_CONFIRMED = "confirmed_fill"
FILL_PENDING = "pending_settlement"
FILL_EMPTY = "confirmed_empty"
FILL_UNCERTAIN = "uncertain"
CONFIRMED_REJECT = "confirmed_reject"
UNKNOWN_SEND_RESULT = "unknown_send_result"

# SDK 0.13.0 maps this exact CLOB errorMsg to RejectedOrder.code = fok_not_filled.
_FOK_NOT_FILLED_MSG = (
    "order couldn't be fully filled. FOK orders are fully filled or killed."
)
_FAK_NOT_FILLED_MSG = (
    "no orders found to match with FAK order. "
    "FAK orders are partially filled or killed if no match is found."
)
_CONFIRMED_REJECT_CODES = frozenset({
    "unmatched", "market_not_ready", "not_enough_balance",
    "invalid_nonce", "invalid_expiration",
    "post_only_would_cross", "post_only_mode",
    "fok_not_filled", "fak_not_filled",
})

_TRADE_OK = frozenset({"CONFIRMED"})
_TRADE_FAIL = frozenset({"FAILED"})
_TRADE_PENDING = frozenset({
    "MATCHED", "MATCHED_NOT_BROADCASTED", "MINED", "RETRYING",
})
_TRADE_KNOWN = _TRADE_OK | _TRADE_FAIL | _TRADE_PENDING
_BASE_UNIT_SCALE = Decimal(SHARE_BASE_UNITS)
# Outcome share price is a probability in [0, 1], not tick size or order caps.
_OUTCOME_PRICE_MIN = Decimal("0")
_OUTCOME_PRICE_MAX = Decimal("1")


def _parse_qty(raw):
    """Parse a SDK Decimal/number. Zero is a value; missing and invalid differ."""
    if raw is None or raw == "":
        return None, "missing"
    if isinstance(raw, bool):
        return None, "invalid"
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None, "invalid"
    if not value.is_finite():
        return None, "invalid"
    return value, "value"


def _qty_float(raw):
    value, kind = _parse_qty(raw)
    if kind == "invalid":
        return None, "invalid"
    if kind == "missing" or value is None:
        return None, "missing"
    return float(value), "value"


def _ids(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values or ():
        text = str(item).strip() if item is not None else ""
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _hashes(values) -> list[str]:
    return [h for h in _ids(values) if h.lower() not in ("none", "null")]


def order_response_to_dict(resp, *, side: str | None = None,
                           asset_id: str | None = None) -> dict:
    """Normalize AcceptedOrder, RejectedOrder, or a plain mapping."""
    from polymarket import RejectedOrder

    if isinstance(resp, RejectedOrder) or (
        isinstance(resp, dict) and (
            resp.get("ok") is False
            or resp.get("rejected")
            or str(resp.get("code") or "").lower() in (
                "fok_not_filled", "fak_not_filled", "unmatched",
            )
        ) and not resp.get("order_id") and not resp.get("orderID")
    ):
        code = getattr(resp, "code", None) if not isinstance(resp, dict) else resp.get("code")
        message = getattr(resp, "message", None) if not isinstance(resp, dict) else resp.get("message")
        return {
            "ok": False,
            "rejected": True,
            "unknown_send": False,
            "outcome": CONFIRMED_REJECT,
            "code": code,
            "message": message,
            "order_id": None,
            "orderID": None,
            "status": "rejected",
            "side": side,
            "asset_id": asset_id,
            "making_amount": None,
            "taking_amount": None,
            "makingAmount": None,
            "takingAmount": None,
            "trade_ids": [],
            "tradeIDs": [],
            "transactions_hashes": [],
            "transactionsHashes": [],
            "success": False,
        }
    if isinstance(resp, dict):
        oid = resp.get("order_id") or resp.get("orderID") or resp.get("id")
        hashes = resp.get("transactions_hashes") or resp.get("transactionsHashes") or []
        trade_ids = resp.get("trade_ids") or resp.get("tradeIDs") or []
        making = resp.get("making_amount", resp.get("makingAmount"))
        taking = resp.get("taking_amount", resp.get("takingAmount"))
        status = resp.get("status")
        rejected = bool(resp.get("rejected") or resp.get("ok") is False)
        unknown = bool(resp.get("unknown_send"))
        outcome = resp.get("outcome")
        if unknown:
            outcome = UNKNOWN_SEND_RESULT
        elif rejected:
            outcome = CONFIRMED_REJECT
        elif not outcome:
            outcome = "accepted"
        return {
            "ok": resp.get("ok", True) and not rejected and not unknown,
            "rejected": rejected and not unknown,
            "unknown_send": unknown,
            "outcome": outcome,
            "code": resp.get("code"),
            "message": resp.get("message"),
            "order_id": oid,
            "orderID": oid,
            "status": status,
            "side": resp.get("side") or side,
            "asset_id": resp.get("asset_id") or asset_id,
            "making_amount": making,
            "taking_amount": taking,
            "makingAmount": making,
            "takingAmount": taking,
            "trade_ids": _ids(trade_ids),
            "tradeIDs": _ids(trade_ids),
            "transactions_hashes": _hashes(hashes),
            "transactionsHashes": _hashes(hashes),
            "success": bool(resp.get("success", True)),
        }
    oid = getattr(resp, "order_id", None)
    hashes = list(getattr(resp, "transactions_hashes", ()) or ())
    trade_ids = list(getattr(resp, "trade_ids", ()) or ())
    making = getattr(resp, "making_amount", None)
    taking = getattr(resp, "taking_amount", None)
    status = getattr(resp, "status", None)
    return {
        "ok": True,
        "rejected": False,
        "unknown_send": False,
        "outcome": "accepted",
        "code": None,
        "message": None,
        "order_id": oid,
        "orderID": oid,
        "status": status,
        "side": side,
        "asset_id": asset_id,
        "making_amount": making,
        "taking_amount": taking,
        "makingAmount": making,
        "takingAmount": taking,
        "trade_ids": _ids(trade_ids),
        "tradeIDs": _ids(trade_ids),
        "transactions_hashes": _hashes(hashes),
        "transactionsHashes": _hashes(hashes),
        "success": True,
    }


def _order_dict(order) -> dict:
    asset = getattr(order, "asset_id", None) or getattr(order, "token_id", None)
    return {
        "id": getattr(order, "id", None),
        "status": getattr(order, "status", None),
        "side": getattr(order, "side", None),
        "asset_id": str(asset) if asset not in (None, "") else None,
        "price": getattr(order, "price", None),
        "original_size": getattr(order, "original_size", None),
        "size_matched": getattr(order, "size_matched", None),
        "associate_trades": _ids(getattr(order, "associate_trades", ()) or ()),
        "order_type": getattr(order, "order_type", None),
        "outcome": getattr(order, "outcome", None),
        "fetch_error": None,
    }


def _field(obj, *names, default=None):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _maker_orders(trade) -> list[dict]:
    out: list[dict] = []
    for maker in _field(trade, "maker_orders") or ():
        out.append({
            "order_id": _field(maker, "order_id"),
            "asset_id": _field(maker, "asset_id"),
            "side": _field(maker, "side"),
            "price": _field(maker, "price"),
            "matched_amount": _field(maker, "matched_amount"),
        })
    return out


def _maker_order_ids(makers: list[dict]) -> list[str]:
    return _ids(m.get("order_id") for m in makers)


def _trade_dict(trade) -> dict:
    asset = _field(trade, "asset_id", "token_id")
    fee_raw = _field(trade, "fee", "fee_amount")
    hash_raw = _field(trade, "transaction_hash", "transactionHash")
    makers = _maker_orders(trade)
    return {
        "id": _field(trade, "id"),
        "status": _field(trade, "status"),
        "size": _field(trade, "size"),
        "price": _field(trade, "price"),
        "side": _field(trade, "side"),
        "trader_side": _field(trade, "trader_side"),
        "taker_order_id": _field(trade, "taker_order_id"),
        "asset_id": str(asset) if asset not in (None, "") else None,
        "transaction_hash": hash_raw,
        "fee": fee_raw,
        "fee_rate_bps": _field(trade, "fee_rate_bps"),
        "maker_orders": makers,
        "maker_order_ids": _maker_order_ids(makers),
        "fetch_error": None,
    }


def _trade_status(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if upper.startswith("TRADE_STATUS_"):
        upper = upper[len("TRADE_STATUS_"):]
    return upper


def _same_token(left, right) -> bool:
    if left in (None, "") or right in (None, ""):
        return True
    return str(left) == str(right)


def _maker_leg(trade: dict, order_id: str | None) -> dict | None:
    if not order_id:
        return None
    hits = [m for m in (trade.get("maker_orders") or [])
            if str(m.get("order_id") or "") == str(order_id)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return {"_conflict": True, "legs": hits}
    return None


def _trade_fingerprint(trade: dict) -> tuple:
    return (
        _trade_status(trade.get("status")),
        str(trade.get("size")),
        str(trade.get("price")),
        str(trade.get("taker_order_id")),
        str(trade.get("trader_side")),
        str(trade.get("asset_id")),
        str(trade.get("side")),
    )


def fetch_clob_order(account: dict, order_id: str) -> dict:
    """Authenticated CLOB order record. Errors are not an empty order."""
    if not order_id:
        return {"id": None, "fetch_error": "missing order_id"}
    try:
        raw = get_session(account).client.get_order(order_id=str(order_id))
    except Exception as exc:
        logger.debug("get_order %s: %r", order_id, exc)
        return {
            "id": str(order_id),
            "fetch_error": f"get_order failed: {type(exc).__name__}",
        }
    parsed = _order_dict(raw)
    returned_id = parsed.get("id")
    if returned_id in (None, ""):
        parsed["id"] = str(order_id)
    elif str(returned_id) != str(order_id):
        parsed["id_mismatch"] = True
    return parsed


def fetch_clob_trades(account: dict, trade_ids: list[str] | None = None,
                      asset_id: str | None = None) -> dict:
    """Fetch trades by id. Never infers 'no trades' from a request failure.

    ``asset_id`` is accepted for signature compatibility but is not used to
    attach unrelated fills to an order.
    """
    del asset_id
    ids = _ids(trade_ids)
    if not ids:
        return {"trades": [], "fetch_error": None, "missing_ids": []}
    try:
        session = get_session(account)
    except Exception as exc:
        return {
            "trades": [],
            "fetch_error": f"list_account_trades failed: {type(exc).__name__}",
            "missing_ids": list(ids),
        }
    out: list[dict] = []
    errors: list[str] = []
    missing_ids: list[str] = []
    for tid in ids:
        try:
            page = session.client.list_account_trades(id=tid).first_page()
            items = getattr(page, "items", None)
            if items is None:
                items = page if isinstance(page, list) else []
            found = False
            for raw in items:
                parsed = _trade_dict(raw)
                out.append(parsed)
                if str(parsed.get("id") or "") == tid:
                    found = True
            if not found:
                missing_ids.append(tid)
        except Exception as exc:
            logger.debug("list_account_trades %s: %r", tid, exc)
            errors.append(f"{tid}:{type(exc).__name__}")
    fetch_error = None
    if errors and not out:
        fetch_error = "list_account_trades failed: " + ",".join(errors)
    elif errors:
        fetch_error = "list_account_trades partial: " + ",".join(errors)
    return {
        "trades": out,
        "fetch_error": fetch_error,
        "missing_ids": missing_ids,
    }


def _share_base_units(qty: Decimal) -> int | None:
    """Convert a reported share/USDC quantity to integer base units, or None.

    Docs require fill math in integer base units (1_000_000 = 1 share / 1 pUSD).
    A value that is not an exact number of base units cannot be compared as a
    documented fill amount.
    """
    scaled = qty * _BASE_UNIT_SCALE
    as_int = scaled.to_integral_value(rounding=ROUND_DOWN)
    if scaled != as_int:
        return None
    return int(as_int)


def _from_base_units(units: int) -> Decimal:
    return Decimal(units) / _BASE_UNIT_SCALE


def _usd_estimate_for_trade(trade: dict, role: str, order_id) -> tuple[Decimal | None, str | None]:
    """Unproven notional from displayed size and price. Never an accounting source.

    Documented fill math is ``floor(makerAssetFill × takerAmount / makerAmount)``
    per maker fill. ``ClobTrade`` / ``MakerOrder`` do not carry signed amounts.
    ``makerAssetFill`` is collateral for a BUY maker and shares for a SELL maker,
    so ``matched_amount × price`` cannot be labeled exact even when maker_orders
    are present. Extra rows do not make the product contractual.
    """
    if role == "maker":
        maker = _maker_leg(trade, str(order_id) if order_id else "")
        if not isinstance(maker, dict) or maker.get("_conflict"):
            return None, None
        amt, ak = _parse_qty(maker.get("matched_amount"))
        px, pk = _parse_qty(maker.get("price"))
        if ak != "value" or pk != "value" or amt is None or px is None:
            return None, None
        side = str(maker.get("side") or "").upper() or "unknown"
        return amt * px, f"estimate:maker_matched_amount_times_price:{side}:unsigned"
    makers = [m for m in (trade.get("maker_orders") or []) if isinstance(m, dict)]
    if makers:
        total = Decimal(0)
        for maker in makers:
            amt, ak = _parse_qty(maker.get("matched_amount"))
            px, pk = _parse_qty(maker.get("price"))
            if ak != "value" or pk != "value" or amt is None or px is None:
                return None, None
            total += amt * px
        return total, "estimate:maker_orders_matched_amount_times_price:unsigned"
    size, sk = _parse_qty(trade.get("size"))
    px, pk = _parse_qty(trade.get("price"))
    if sk != "value" or pk != "value" or size is None or px is None:
        return None, None
    return size * px, "estimate:trade_size_times_price:unsigned"


def _outcome_price_ok(px) -> bool:
    if px is None:
        return False
    return px.is_finite() and _OUTCOME_PRICE_MIN <= px <= _OUTCOME_PRICE_MAX


def _fill_result(*, state: str, order_id, side: str, status,
                 shares, usd, price, fee, fee_known: bool,
                 tx_hashes: list, trade_ids: list, trade_statuses: list,
                 asset_id=None, error=None, original_size=None,
                 size_matched=None, amounts_ready: bool | None = None,
                 shares_known: bool = False, usd_known: bool = False,
                 shares_source: str | None = None,
                 usd_source: str | None = None,
                 usd_estimate=None, usd_estimate_source: str | None = None) -> dict:
    filled = state == FILL_CONFIRMED
    if amounts_ready is None:
        amounts_ready = False
    accounting_ready = bool(amounts_ready) and state == FILL_CONFIRMED
    final = (
        state == FILL_EMPTY
        or (state == FILL_CONFIRMED and accounting_ready)
    )
    price_cents = None
    if price is not None:
        price_cents = round(float(price) * 100.0, 2)
    return {
        "state": state,
        "final": final,
        "filled": filled,
        "amounts_ready": accounting_ready,
        "shares_known": bool(shares_known) and shares is not None,
        "usd_known": bool(usd_known) and usd is not None,
        "shares_source": shares_source,
        "usd_source": usd_source,
        "usd_estimate": None if usd_estimate is None else float(usd_estimate),
        "usd_estimate_source": usd_estimate_source,
        "order_id": order_id,
        "side": side,
        "asset_id": asset_id,
        "status": status,
        "shares": None if shares is None else float(shares),
        "usd": None if usd is None else float(usd),
        "price": None if price is None else float(price),
        "price_cents": price_cents,
        "fee": None if fee is None else float(fee),
        "fee_known": fee_known,
        "tx_hashes": list(tx_hashes or []),
        "trade_ids": list(trade_ids or []),
        "trade_statuses": list(trade_statuses or []),
        "original_size": None if original_size is None else float(original_size),
        "size_matched": None if size_matched is None else float(size_matched),
        "error": error,
    }


def _uncertain(oid, side_u, post_status, hashes, trade_ids, error,
               asset_id=None, original_size=None, size_matched=None,
               trade_statuses=None):
    return _fill_result(
        state=FILL_UNCERTAIN, order_id=oid, side=side_u, status=post_status,
        shares=None, usd=None, price=None, fee=None, fee_known=False,
        tx_hashes=hashes, trade_ids=trade_ids,
        trade_statuses=list(trade_statuses or []),
        asset_id=asset_id, error=error,
        original_size=original_size, size_matched=size_matched,
        amounts_ready=False,
    )


def _fill_leg_for_order(trade: dict, order_id: str, order_side: str,
                        expected_asset) -> tuple[str, dict | None]:
    """Return (role, leg) or ('conflict'|'unknown', None).

    Docs: a trade belongs to an order as taker via ``taker_order_id``, or as
    maker via ``maker_orders[].order_id``. ``ClobTrade.side`` is the trade
    side, not automatically the maker order's side.
    """
    trader_side = str(trade.get("trader_side") or "").upper()
    taker_hit = str(trade.get("taker_order_id") or "") == str(order_id)
    maker = _maker_leg(trade, order_id)
    if isinstance(maker, dict) and maker.get("_conflict"):
        return "conflict", None
    maker_hit = maker is not None
    if trader_side == "TAKER":
        if not taker_hit or maker_hit:
            return "conflict", None
        if str(trade.get("side") or "").upper() not in ("", order_side):
            return "conflict", None
        if not _same_token(trade.get("asset_id"), expected_asset):
            return "conflict", None
        return "taker", {
            "size": trade.get("size"),
            "price": trade.get("price"),
            "asset_id": trade.get("asset_id"),
        }
    if trader_side == "MAKER":
        if taker_hit or not maker_hit:
            return "conflict", None
        maker_side = str(maker.get("side") or "").upper()
        if maker_side and maker_side != order_side:
            return "conflict", None
        if not _same_token(maker.get("asset_id") or trade.get("asset_id"), expected_asset):
            return "conflict", None
        return "maker", {
            "size": maker.get("matched_amount"),
            "price": maker.get("price"),
            "asset_id": maker.get("asset_id") or trade.get("asset_id"),
        }
    if taker_hit and not maker_hit:
        return _fill_leg_for_order(
            {**trade, "trader_side": "TAKER"}, order_id, order_side, expected_asset)
    if maker_hit and not taker_hit:
        return _fill_leg_for_order(
            {**trade, "trader_side": "MAKER"}, order_id, order_side, expected_asset)
    return "unknown", None


def resolve_fill(account: dict, order_resp: dict | None, side: str) -> dict:
    """Classify a posted CLOB order without blocking on chain settlement.

    States
    ------
    confirmed_fill
        Every expected trade ID from the placement response and the order
        record is present, belongs to this order, and is CONFIRMED. Share and
        notional amounts are known. ``original_size`` is never the fill.
        Empty hashes do not block CONFIRMED; a hash alone is not success.
    pending_settlement
        The expected set is complete and at least one trade is MATCHED/MINED/
        RETRYING, with none FAILED unless mixed (mixed is uncertain).
    confirmed_empty
        Placement rejected, or the complete expected set is all FAILED.
    uncertain
        Incomplete trade set, empty successful lookup of an expected ID,
        identity/side/asset conflict, maker/taker conflict, invalid amounts,
        missing fill price, or DELAYED/LIVE without matched size.

    BUY: making_amount = collateral given, taking_amount = shares received.
    SELL: the reverse. Placement making/taking describe immediate fills
    listed in the POST ``trade_ids``; later associate_trades are not the
    same set and must not inherit that notional. Settlement confirmation is
    independent of whether documented USD is known. ``matched_amount × price``
    is an estimate only. Fees are not mixed into making/taking. Outcome fill
    prices must lie in documented [$0.00, $1.00]. SDK Decimal units, not
    divided by 1_000_000. Fee missing remains unknown. Order limit price is
    not used as fill price.
    """
    resp = order_response_to_dict(order_resp, side=side)
    side_u = (side or resp.get("side") or "").upper() or "BUY"
    if resp.get("rejected") or resp.get("ok") is False:
        return _fill_result(
            state=FILL_EMPTY, order_id=resp.get("order_id"), side=side_u,
            status="rejected", shares=0, usd=0, price=None, fee=None,
            fee_known=False, tx_hashes=[], trade_ids=[], trade_statuses=[],
            asset_id=resp.get("asset_id"),
            error=str(resp.get("message") or resp.get("code") or "rejected"),
            amounts_ready=True, shares_known=True, usd_known=True,
            shares_source="empty_rejected", usd_source="empty_rejected",
        )

    oid = resp.get("order_id")
    post_status = str(resp.get("status") or "").strip()
    post_l = post_status.lower()
    placement_ids = list(resp.get("trade_ids") or [])
    expected_ids = list(placement_ids)
    hashes = list(resp.get("transactions_hashes") or [])
    asset_id = resp.get("asset_id")

    order: dict = {}
    if oid:
        order = fetch_clob_order(account, str(oid))
        if order.get("fetch_error"):
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                order.get("fetch_error"), asset_id,
                order.get("original_size"), order.get("size_matched"))
        if order.get("id_mismatch"):
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "fetched order id does not match requested order id",
                order.get("asset_id") or asset_id,
                order.get("original_size"), order.get("size_matched"))
        expected_ids = _ids(list(expected_ids) + list(order.get("associate_trades") or []))
        if order.get("asset_id") and asset_id and not _same_token(order.get("asset_id"), asset_id):
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "order asset_id does not match placement asset_id",
                asset_id, order.get("original_size"), order.get("size_matched"))
        asset_id = asset_id or order.get("asset_id")
        order_side = str(order.get("side") or "").upper()
        if order_side and order_side != side_u:
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "order side does not match requested side",
                asset_id, order.get("original_size"), order.get("size_matched"))
        if order.get("status"):
            post_status = str(order.get("status"))
            post_l = post_status.lower()

    matched_qty, matched_kind = _parse_qty(order.get("size_matched") if order else None)
    original_qty, orig_kind = _parse_qty(order.get("original_size") if order else None)
    making, making_kind = _parse_qty(resp.get("making_amount"))
    taking, taking_kind = _parse_qty(resp.get("taking_amount"))
    if orig_kind == "invalid" or "invalid" in (matched_kind, making_kind, taking_kind):
        return _uncertain(
            oid, side_u, post_status, hashes, expected_ids,
            "invalid numeric field", asset_id, original_qty, matched_qty)
    if matched_qty is not None and matched_qty < 0:
        return _uncertain(
            oid, side_u, post_status, hashes, expected_ids,
            "negative size_matched", asset_id, original_qty, matched_qty)
    if (making is not None and making < 0) or (taking is not None and taking < 0):
        return _uncertain(
            oid, side_u, post_status, hashes, expected_ids,
            "negative making/taking amount", asset_id, original_qty, matched_qty)

    trades_blob = (
        fetch_clob_trades(account, trade_ids=expected_ids)
        if expected_ids else {"trades": [], "fetch_error": None, "missing_ids": []}
    )
    if trades_blob.get("fetch_error"):
        return _uncertain(
            oid, side_u, post_status, hashes, expected_ids,
            trades_blob.get("fetch_error"), asset_id, original_qty, matched_qty)
    missing_ids = list(trades_blob.get("missing_ids") or [])

    by_id: dict[str, dict] = {}
    for t in (trades_blob.get("trades") or []):
        key = str(t.get("id") or "")
        if not key:
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "trade record missing id", asset_id, original_qty, matched_qty)
        if key in by_id:
            if _trade_fingerprint(by_id[key]) != _trade_fingerprint(t):
                return _uncertain(
                    oid, side_u, post_status, hashes, expected_ids,
                    f"conflicting duplicate trade id {key}",
                    asset_id, original_qty, matched_qty)
            continue
        by_id[key] = t

    raw_trades = []
    for t in by_id.values():
        if expected_ids and str(t.get("id")) not in expected_ids:
            continue
        raw_trades.append(t)

    if missing_ids or (expected_ids and any(eid not in by_id for eid in expected_ids)):
        absent = missing_ids or [eid for eid in expected_ids if eid not in by_id]
        return _uncertain(
            oid, side_u, post_status, hashes, expected_ids,
            "incomplete trade set: missing " + ",".join(absent),
            asset_id, original_qty, matched_qty,
            trade_statuses=[_trade_status(t.get("status")) for t in raw_trades])

    trade_share_units = 0
    fee_sum = Decimal(0)
    fee_known = True
    saw_fee = False
    statuses: list[str] = []
    accounted: list[dict] = []
    if not raw_trades:
        fee_known = False

    for trade in raw_trades:
        status_u = _trade_status(trade.get("status"))
        statuses.append(status_u)
        role = "taker"
        if oid:
            role, leg = _fill_leg_for_order(trade, str(oid), side_u, asset_id)
            if role in ("conflict", "unknown") or not leg:
                return _uncertain(
                    oid, side_u, post_status, hashes, expected_ids,
                    f"trade {trade.get('id')} does not consistently belong to order",
                    asset_id, original_qty, matched_qty, statuses)
            size, size_kind = _parse_qty(leg.get("size"))
            px, px_kind = _parse_qty(leg.get("price"))
        else:
            size, size_kind = _parse_qty(trade.get("size"))
            px, px_kind = _parse_qty(trade.get("price"))
        if size_kind == "invalid" or px_kind == "invalid":
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "invalid trade numeric field", asset_id, original_qty, matched_qty,
                statuses)
        if size_kind == "value" and size is not None and size < 0:
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "negative trade size", asset_id, original_qty, matched_qty, statuses)
        if px_kind == "value" and px is not None and not _outcome_price_ok(px):
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                f"outcome fill price {px} outside documented [$0.00, $1.00]",
                asset_id, original_qty, matched_qty, statuses)
        if size_kind != "value" or size is None:
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "trade size missing", asset_id, original_qty, matched_qty, statuses)
        share_u = _share_base_units(size)
        if share_u is None:
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "trade size is not an integer number of base units",
                asset_id, original_qty, matched_qty, statuses)
        if role == "taker":
            makers = [m for m in (trade.get("maker_orders") or []) if isinstance(m, dict)]
            if makers:
                maker_share_u = 0
                for maker in makers:
                    amt, ak = _parse_qty(maker.get("matched_amount"))
                    if ak != "value" or amt is None:
                        return _uncertain(
                            oid, side_u, post_status, hashes, expected_ids,
                            "maker fill size missing",
                            asset_id, original_qty, matched_qty, statuses)
                    mu = _share_base_units(amt)
                    if mu is None:
                        return _uncertain(
                            oid, side_u, post_status, hashes, expected_ids,
                            "maker fill size is not an integer number of base units",
                            asset_id, original_qty, matched_qty, statuses)
                    maker_share_u += mu
                if maker_share_u != share_u:
                    return _uncertain(
                        oid, side_u, post_status, hashes, expected_ids,
                        "maker fill sizes do not sum to trade size",
                        asset_id, original_qty, matched_qty, statuses)
        trade_share_units += share_u
        est, est_src = _usd_estimate_for_trade(trade, role, oid)
        accounted.append({
            "id": str(trade.get("id") or ""),
            "share_units": share_u,
            "role": role,
            "trade": trade,
            "usd_estimate": est,
            "usd_estimate_source": est_src,
        })
        fee_val, fee_kind = _parse_qty(trade.get("fee"))
        if fee_kind == "missing":
            fee_known = False
        elif fee_kind == "invalid":
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "invalid fee field", asset_id, original_qty, matched_qty, statuses)
        else:
            saw_fee = True
            fee_sum += fee_val or Decimal(0)
        h = trade.get("transaction_hash")
        if h:
            hashes = _hashes(list(hashes) + [h])
    if not saw_fee:
        fee_known = False
        fee_sum = None

    is_buy = side_u == "BUY"
    # BUY: making = collateral given, taking = shares received.
    # SELL: making = shares given, taking = collateral received.
    # AcceptedOrder making/taking are the placement fill set (trade_ids),
    # not later associate_trades. Fees are a separate field; not mixed in.
    post_shares = taking if is_buy else making
    post_usd = making if is_buy else taking
    shares = _from_base_units(trade_share_units) if raw_trades else None
    shares_source = "trade_sizes" if raw_trades else None
    usd = None
    usd_source = None
    usd_known = False
    placement_set = set(placement_ids)
    accounted_ids = {row["id"] for row in accounted}
    later_ids = [eid for eid in expected_ids if eid not in placement_set]
    placement_complete = bool(
        placement_ids and accounted and all(pid in accounted_ids for pid in placement_ids)
    )
    if placement_complete:
        imm = [row for row in accounted if row["id"] in placement_set]
        imm_share_units = sum(row["share_units"] for row in imm)
        if post_shares is not None:
            post_share_u = _share_base_units(post_shares)
            if post_share_u is None or post_share_u != imm_share_units:
                return _uncertain(
                    oid, side_u, post_status, hashes, expected_ids,
                    "POST fill size conflicts with the same placement trade set",
                    asset_id, original_qty, matched_qty, statuses)
        if post_usd is not None and imm_share_units > 0:
            implied = post_usd / _from_base_units(imm_share_units)
            if not _outcome_price_ok(implied):
                return _uncertain(
                    oid, side_u, post_status, hashes, expected_ids,
                    f"implied fill price {implied} outside documented [$0.00, $1.00]",
                    asset_id, original_qty, matched_qty, statuses)
        if post_usd is not None and not later_ids:
            usd = post_usd
            usd_source = "post_placement"
            usd_known = True
    est_parts = [row["usd_estimate"] for row in accounted]
    if est_parts and all(p is not None for p in est_parts):
        usd_estimate = sum(est_parts, Decimal(0))
        srcs = {row["usd_estimate_source"] for row in accounted if row.get("usd_estimate_source")}
        usd_estimate_source = srcs.pop() if len(srcs) == 1 else (
            "estimate:mixed_unsigned" if srcs else None
        )
    else:
        usd_estimate = None
        usd_estimate_source = None
    if shares is None and post_shares is not None and not raw_trades and (
        post_l == "matched" or (matched_qty or 0) > 0
    ) and not later_ids:
        shares = post_shares
        shares_source = "post_placement"
    if matched_kind == "value" and matched_qty is not None and shares is not None:
        matched_u = _share_base_units(matched_qty)
        share_u = _share_base_units(shares)
        if matched_u is None or share_u is None or matched_u != share_u:
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                "size_matched conflicts with trade sizes",
                asset_id, original_qty, matched_qty, statuses)
    if shares is not None and shares < 0:
        return _uncertain(
            oid, side_u, post_status, hashes, expected_ids,
            "negative fill size", asset_id, original_qty, matched_qty, statuses)
    if usd is not None and usd < 0:
        return _uncertain(
            oid, side_u, post_status, hashes, expected_ids,
            "negative fill notional", asset_id, original_qty, matched_qty, statuses)

    price = None
    if shares and usd is not None and shares > 0:
        price = usd / shares
        if not _outcome_price_ok(price):
            return _uncertain(
                oid, side_u, post_status, hashes, expected_ids,
                f"implied fill price {price} outside documented [$0.00, $1.00]",
                asset_id, original_qty, matched_qty, statuses)

    complete = not expected_ids or (
        not missing_ids and all(eid in by_id for eid in expected_ids)
    )
    status_u = [_trade_status(s) for s in statuses]
    unknown_status = any(s not in _TRADE_KNOWN for s in status_u)
    all_failed = (
        complete and bool(status_u) and not unknown_status
        and all(s in _TRADE_FAIL for s in status_u)
        and len(status_u) == len(expected_ids or status_u)
    )
    all_ok = (
        complete and bool(status_u) and not unknown_status
        and all(s in _TRADE_OK for s in status_u)
        and (not expected_ids or len(status_u) == len(expected_ids))
    )
    any_pending = any(s in _TRADE_PENDING for s in status_u)
    any_ok = any(s in _TRADE_OK for s in status_u)
    any_fail = any(s in _TRADE_FAIL for s in status_u)
    matched_flag = (
        post_l == "matched"
        or (matched_qty is not None and matched_qty > 0)
        or any_ok or any_pending
        or bool(expected_ids)
    )
    shares_known = shares is not None
    amounts_ready = False

    if unknown_status:
        state = FILL_UNCERTAIN
        error = "unknown or empty trade status"
        usd_known = False
    elif any_ok and any_fail:
        state = FILL_UNCERTAIN
        error = "mixed CONFIRMED and FAILED trades"
        usd_known = False
    elif all_failed:
        state = FILL_EMPTY
        error = "all associated trades FAILED"
        shares, usd, price = Decimal(0), Decimal(0), None
        shares_source, usd_source = "empty_failed", "empty_failed"
        shares_known, usd_known, amounts_ready = True, True, True
        usd_estimate, usd_estimate_source = None, None
    elif all_ok:
        state = FILL_CONFIRMED
        error = None
        amounts_ready = bool(shares_known and usd_known)
    elif post_l == "matched" and not expected_ids and not (
        matched_qty and matched_qty > 0
    ) and not raw_trades:
        state = FILL_UNCERTAIN
        error = "matched status without fill evidence"
        usd_known = False
    elif any_pending or (complete and any_ok and not all_ok):
        state = FILL_PENDING
        error = None
        usd_known = False
    elif post_l in ("delayed", "live", "unmatched") and not matched_flag:
        state = FILL_UNCERTAIN
        error = f"order {post_l} with no matched size"
        shares = matched_qty if matched_kind == "value" else Decimal(0)
        usd = None
        price = None
        shares_source = "size_matched" if matched_kind == "value" else None
        usd_source = None
        usd_known = False
        usd_estimate, usd_estimate_source = None, None
    elif post_l in ("cancelled", "canceled", "expired") and not matched_flag:
        state = FILL_EMPTY
        error = f"order {post_l}"
        shares, usd, price = Decimal(0), Decimal(0), None
        shares_source, usd_source = "empty_cancelled", "empty_cancelled"
        shares_known, usd_known, amounts_ready = True, True, True
        usd_estimate, usd_estimate_source = None, None
    elif post_l == "rejected" or resp.get("rejected"):
        state = FILL_EMPTY
        error = "rejected"
        shares, usd, price = Decimal(0), Decimal(0), None
        shares_source, usd_source = "empty_rejected", "empty_rejected"
        shares_known, usd_known, amounts_ready = True, True, True
        usd_estimate, usd_estimate_source = None, None
    elif matched_flag and not all_ok:
        state = FILL_PENDING
        error = None
        usd_known = False
    else:
        state = FILL_UNCERTAIN
        error = "insufficient fill evidence"
        usd_known = False

    if state == FILL_EMPTY and hashes:
        state = FILL_UNCERTAIN
        error = "FAILED/empty fill but transaction hashes present"
        amounts_ready = False
        usd_known = False

    if state != FILL_CONFIRMED:
        amounts_ready = True if state == FILL_EMPTY else False

    return _fill_result(
        state=state, order_id=oid, side=side_u, status=post_status or state,
        shares=shares, usd=usd, price=price,
        fee=fee_sum if fee_known else None, fee_known=fee_known,
        tx_hashes=hashes, trade_ids=expected_ids, trade_statuses=statuses,
        asset_id=asset_id, error=error,
        original_size=original_qty, size_matched=matched_qty,
        amounts_ready=amounts_ready,
        shares_known=shares_known, usd_known=usd_known and state == FILL_CONFIRMED,
        shares_source=shares_source, usd_source=usd_source if usd_known and state == FILL_CONFIRMED else None,
        usd_estimate=usd_estimate, usd_estimate_source=usd_estimate_source,
    )


def _reject_code(raw) -> bool:
    text = str(raw or "").strip().lower()
    return text in _CONFIRMED_REJECT_CODES


def _fok_kill_text(raw) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    if _FOK_NOT_FILLED_MSG in text or _FAK_NOT_FILLED_MSG in text:
        return True
    return (
        "couldn't be fully filled" in text
        or "fok orders are fully filled or killed" in text
        or "fok_not_filled" in text
        or "fak_not_filled" in text
        or "no orders found to match with fak" in text
    )


def classify_send_exception(exc: BaseException) -> str:
    """Split proven FOK/reject from unproven transport after POST.

    CONFIRMED_REJECT: the CLOB/SDK proved the order was not accepted or not
    filled. UNKNOWN_SEND_RESULT: the POST may have left the matching engine.
    """
    from polymarket.errors import (
        CancelledSigningError,
        ConnectionLostError,
        InsufficientLiquidityError,
        RequestRejectedError,
        SigningError,
        TimeoutError as PolyTimeoutError,
        TransportError,
        UnexpectedResponseError,
        UserInputError,
    )

    if isinstance(exc, (
        InsufficientLiquidityError, UserInputError, SigningError,
        CancelledSigningError,
    )):
        return CONFIRMED_REJECT
    if _reject_code(getattr(exc, "code", None)) or _fok_kill_text(exc):
        return CONFIRMED_REJECT
    if isinstance(exc, RequestRejectedError):
        status = int(getattr(exc, "status", 0) or 0)
        if status in (408, 425) or status >= 500 or status == 0:
            return UNKNOWN_SEND_RESULT
        return CONFIRMED_REJECT
    if isinstance(exc, (
        PolyTimeoutError, TimeoutError, TransportError, ConnectionLostError,
        ConnectionError, ConnectionResetError, BrokenPipeError,
        UnexpectedResponseError,
    )):
        return UNKNOWN_SEND_RESULT
    try:
        import httpx
        if isinstance(exc, (
            httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError,
        )):
            return UNKNOWN_SEND_RESULT
    except Exception:
        pass
    return UNKNOWN_SEND_RESULT


def _empty_place_fields(*, side, asset_id, outcome, code, message, error_type):
    unknown = outcome == UNKNOWN_SEND_RESULT
    return {
        "ok": False,
        "rejected": not unknown,
        "unknown_send": unknown,
        "outcome": outcome,
        "code": code,
        "message": message,
        "error_type": error_type,
        "order_id": None,
        "orderID": None,
        "status": "unknown" if unknown else "rejected",
        "side": side,
        "asset_id": asset_id,
        "making_amount": None,
        "taking_amount": None,
        "makingAmount": None,
        "takingAmount": None,
        "trade_ids": [],
        "tradeIDs": [],
        "transactions_hashes": [],
        "transactionsHashes": [],
        "success": False,
    }


def _send_failure_dict(exc: BaseException, *, side: str, asset_id: str) -> dict:
    return _empty_place_fields(
        side=side, asset_id=asset_id,
        outcome=classify_send_exception(exc),
        code=getattr(exc, "code", None),
        message=str(exc),
        error_type=type(exc).__name__,
    )


def place_market_order(
    account: dict,
    token_id: str,
    side: str,
    amount: float,
    price: float = 0,
) -> dict:
    """Place a FOK market order. amount = USD for BUY, shares for SELL.

    Never raises on CLOB/SDK send failure: returns a dict with ``outcome``
    ``confirmed_reject`` or ``unknown_send_result``.
    """
    side_u = side.upper()
    asset_id = str(token_id)
    try:
        session = get_session(account)
        if side_u == "BUY":
            resp = session.client.place_market_order(
                token_id=asset_id,
                side="BUY",
                amount=float(amount),
                max_price=float(price) if price > 0 else None,
                order_type="FOK",
            )
        else:
            resp = session.client.place_market_order(
                token_id=asset_id,
                side="SELL",
                shares=float(amount),
                min_price=float(price) if price > 0 else None,
                order_type="FOK",
            )
    except Exception as exc:
        logger.warning("place_market_order %s: %s: %s",
                       classify_send_exception(exc), type(exc).__name__, exc)
        return _send_failure_dict(exc, side=side_u, asset_id=asset_id)
    return order_response_to_dict(resp, side=side_u, asset_id=asset_id)


def token_for_side(pipe, side: str) -> str | None:
    from backend.feeds.polymarket import token_id_for_side

    market = getattr(getattr(pipe, "polymarket", None), "market", None) or {}
    return token_id_for_side(market, side)
