"""Real-trading bots — live CLOB execution linked to Polymarket accounts."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from backend.config import CONFIG
from backend.engine.bots import (
    STRATEGIES, taker_fee, _EVALUATORS, _fresh_rt, sanitize_params,
    lock_profit_if_bid,
    LOCAL_SETTLE_MARGIN_BPS, OFFICIAL_WAIT_MS, RECONCILE_TTL_MS, VOID_DEADLINE_MS,
)
from backend.engine.clob_executor import (
    FILL_CONFIRMED, FILL_EMPTY, FILL_PENDING, FILL_UNCERTAIN,
    REAL_MIN_ORDER_SHARES, BalanceReadError, place_market_order, resolve_fill,
    protocol_version, redeem_account_positions, sync_clob_balance,
    token_for_side, token_share_balance,
)
from backend.engine.pipeline import round_label_et
from backend.feeds.account_data import filter_for_asset, invalidate_snapshot
from backend.storage import storage

logger = logging.getLogger(__name__)


def fetch_official_outcome(asset: str, window_id: int) -> str | None:
    """Gamma outcome for one closed 5m round. None when it is not published yet."""
    import httpx
    from backend.feeds.polymarket import parse_resolution

    slug = f"{str(asset).lower()}-updown-5m-{int(window_id) * (CONFIG.window_ms // 1000)}"
    try:
        resp = httpx.get(
            f"{CONFIG.gamma_base_url}/markets",
            params={"slug": slug, "closed": "true"},
            timeout=5.0,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        return parse_resolution(resp.json())
    except Exception:
        return None

COPY_ALL = "ALL"
# After this, an unproven send may be dropped from the token's share balance.
# A fresh intent inside the window is not released. No second POST.
RECONCILE_GIVE_UP_MS = 90_000


def activity_level(msg: str) -> str:
    """Settlement holds are a wait, not a failed order.

    Confirmed refusals stay ``error``: zero shares, a killed FOK, an unread
    balance, a missing token. Only the phrases that mean "CLOB has not
    finished this order yet" become ``wait``.
    """
    text = msg or ""
    low = text.lower()
    if "still unproven" in low or "held for reconcile" in low:
        return "wait"
    if low.startswith("redeem withheld") or low.startswith("redeem skipped"):
        return "wait"
    if (
        low.endswith("sell withheld")
        or low.endswith("flatten withheld")
        or low.endswith("buy withheld")
    ):
        return "wait"
    return "error"


class RealBotManager:
    def __init__(self, hub, bot_manager) -> None:
        self._hub = hub
        self._bot_manager = bot_manager
        self.bots: dict[str, dict] = storage.load_real_bots()
        self._rt: dict[str, dict] = {}
        self._dirty: set[str] = set()
        self._loop_task: asyncio.Task | None = None
        self._boot_task: asyncio.Task | None = None
        self._redeem_inflight: set[str] = set()
        self._redeem_notes: list[tuple[str, dict]] = []
        self._redeem_skip: dict[str, float] = {}
        self._normalize_statuses()
        for bot in self.bots.values():
            self._ensure_fields(bot)

    def _ensure_fields(self, bot: dict) -> None:
        bot.setdefault("balance", 0.0)
        bot.setdefault("start_balance", 0.0)
        bot.setdefault("position", None)
        bot.setdefault("trades", [])
        bot.setdefault("stats", {"rounds": 0, "wins": 0, "losses": 0, "fees": 0.0})
        bot.setdefault("last_exec_error", None)
        bot.setdefault("balance_unread", False)
        bot.setdefault("orders_placed", 0)
        bot.setdefault("activity_log", [])
        bot.setdefault("pending_op", None)
        bot.setdefault("redeem_queue", [])

    def _normalize_statuses(self) -> None:
        for bot in self.bots.values():
            s = bot.get("status", "stopped")
            if s in ("disarmed", "armed"):
                bot["status"] = "running" if s == "armed" else "stopped"

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="real-bot-loop")
        self._boot_task = asyncio.create_task(self._boot_running(), name="real-bot-boot")

    async def _boot_running(self) -> None:
        for bot in self.bots.values():
            account = storage.get_account(bot.get("account_id", ""))
            if self._op_blocks(bot):
                try:
                    self._reconcile_pending(bot, account, force=True)
                except Exception:
                    logger.exception("boot reconcile failed for %s", bot.get("id"))
            if bot.get("status") != "running" or bot.get("asset") == COPY_ALL:
                continue
            try:
                await self._hub.retain(bot["asset"], bot["id"])
            except Exception:
                logger.exception("retain failed for real bot %s", bot["id"])
            if account:
                try:
                    self._sync_balance(bot, account)
                except Exception:
                    logger.exception("balance sync failed for real bot %s", bot["id"])

    async def stop(self) -> None:
        if self._boot_task is not None:
            self._boot_task.cancel()
            try:
                await self._boot_task
            except (asyncio.CancelledError, Exception):
                pass
            self._boot_task = None
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None
        self._save(force=True)

    def _save(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        for bot_id in list(self._dirty):
            bot = self.bots.get(bot_id)
            if bot is not None:
                storage.save_real_bot(bot)
        self._dirty.clear()

    def _running(self, bot: dict) -> bool:
        return bot.get("status") == "running"

    def _cash_status_error(self, msg) -> bool:
        text = str(msg or "")
        return (
            text.startswith("CLOB cash unread")
            or text.startswith("CLOB balance $0.00")
            or "deposit USDC at polymarket.com" in text
        )

    def _sync_balance(self, bot: dict, account: dict) -> float | None:
        try:
            cash = sync_clob_balance(account)
        except BalanceReadError as exc:
            bot["balance_unread"] = True
            msg = f"CLOB cash unread: {exc}"
            bot["last_exec_error"] = msg
            if bot.get("_last_cash_status") != msg:
                bot["_last_cash_status"] = msg
                self._activity(bot, "error", msg)
            self._dirty.add(bot["id"])
            return None
        bot["balance_unread"] = False
        bot["balance"] = cash
        bot["balance_synced_ms"] = time.time() * 1000.0
        if bot.get("start_balance", 0) <= 0 and cash > 0:
            bot["start_balance"] = cash
        if cash > 0:
            if self._cash_status_error(bot.get("last_exec_error")):
                bot["last_exec_error"] = None
            bot["_last_cash_status"] = None
        else:
            msg = (
                "CLOB balance $0.00 — deposit USDC at polymarket.com to place trades"
            )
            bot["last_exec_error"] = msg
            if bot.get("_last_cash_status") != msg:
                bot["_last_cash_status"] = msg
                self._activity(bot, "error", msg)
        self._dirty.add(bot["id"])
        return cash

    def _clob_fill(self, bot: dict, account: dict, resp: dict, side: str,
                   token_id: str | None = None,
                   version: str | None = None) -> dict:
        """Confirm fill from Polymarket CLOB, then resync cash."""
        fill = resolve_fill(account, resp, side)
        if token_id and side.upper() == "BUY":
            try:
                token_share_balance(account, token_id, version=version)
            except BalanceReadError as exc:
                fill["share_balance_error"] = str(exc)
        self._sync_balance(bot, account)
        invalidate_snapshot(account)
        return fill

    def _reconcile_blocked(self, pos) -> bool:
        return bool(
            isinstance(pos, dict)
            and (pos.get("fill_uncertain") or pos.get("needs_reconcile"))
        )

    def _op_blocks(self, bot: dict) -> bool:
        """App rule: unfinished CLOB send blocks BUY/SELL/flatten across restart."""
        op = bot.get("pending_op")
        if not isinstance(op, dict):
            return False
        if op.get("needs_reconcile"):
            return True
        return op.get("state") in ("intent", "posted", "uncertain")

    def _persist_bot(self, bot: dict) -> None:
        storage.save_real_bot(bot)
        self._dirty.discard(bot.get("id"))

    @staticmethod
    def _kept_condition_id(op: dict | None, pos) -> str:
        """Keep the market condition across a reconcile rebuild."""
        cid = str((op or {}).get("condition_id") or "")
        if cid:
            return cid
        if isinstance(pos, dict):
            return str(pos.get("condition_id") or "")
        return ""

    def _begin_intent(
        self, bot: dict, *, intent: str, side: str, token_id, version,
        window_id, amount, price_limit, account_id, market_side=None,
        condition_id=None,
    ) -> dict | None:
        if self._op_blocks(bot):
            return None
        op = {
            "id": f"op_{uuid.uuid4().hex[:12]}",
            "bot_id": bot.get("id"),
            "account_id": account_id,
            "intent": intent,
            "side": side,
            "market_side": market_side,
            "token_id": token_id,
            "condition_id": str(condition_id or ""),
            "protocol_version": version,
            "window_id": window_id,
            "requested_amount": amount,
            "price_limit": price_limit,
            "created_ms": time.time() * 1000.0,
            "state": "intent",
            "needs_reconcile": False,
            "order_id": None,
            "trade_ids": None,
            "making_amount": None,
            "taking_amount": None,
            "transactions_hashes": None,
            "error": None,
        }
        bot["pending_op"] = op
        try:
            self._persist_bot(bot)
        except Exception as exc:
            bot["pending_op"] = None
            bot["last_exec_error"] = (
                f"could not persist order intent — order not sent ({exc})"
            )
            self._activity(bot, "error", bot["last_exec_error"])
            return None
        return op

    def _record_placement(self, bot: dict, resp) -> None:
        op = bot.get("pending_op")
        if not isinstance(op, dict) or not isinstance(resp, dict):
            return
        if "order_id" in resp or "orderID" in resp:
            oid = resp.get("order_id")
            if oid is None:
                oid = resp.get("orderID")
            op["order_id"] = oid
        if "trade_ids" in resp or "tradeIDs" in resp:
            tids = resp.get("trade_ids")
            if tids is None:
                tids = resp.get("tradeIDs")
            op["trade_ids"] = None if tids is None else list(tids)
        if "making_amount" in resp or "makingAmount" in resp:
            raw = resp.get("making_amount")
            if raw is None and "makingAmount" in resp:
                raw = resp.get("makingAmount")
            op["making_amount"] = None if raw in (None, "") else raw
        if "taking_amount" in resp or "takingAmount" in resp:
            raw = resp.get("taking_amount")
            if raw is None and "takingAmount" in resp:
                raw = resp.get("takingAmount")
            op["taking_amount"] = None if raw in (None, "") else raw
        if "transactions_hashes" in resp or "transactionsHashes" in resp:
            hs = resp.get("transactions_hashes")
            if hs is None:
                hs = resp.get("transactionsHashes")
            op["transactions_hashes"] = None if hs is None else list(hs)
        if resp.get("rejected") or resp.get("ok") is False:
            op["state"] = "rejected"
            op["needs_reconcile"] = False
        elif op.get("state") == "intent":
            op["state"] = "posted"

    def _mark_uncertain(self, bot: dict, msg: str, pos: dict | None = None) -> None:
        op = bot.get("pending_op")
        if isinstance(op, dict):
            op["state"] = "uncertain"
            op["needs_reconcile"] = True
            op["error"] = msg
        if isinstance(pos, dict):
            pos["needs_reconcile"] = True
            pos["fill_uncertain"] = True
        bot["last_exec_error"] = msg
        self._activity(bot, "error", msg)
        try:
            self._persist_bot(bot)
        except Exception:
            logger.exception("failed to persist uncertain op for %s", bot.get("id"))

    def _finish_reject(self, bot: dict, msg: str) -> None:
        op = bot.get("pending_op")
        if isinstance(op, dict):
            op["state"] = "rejected"
            op["needs_reconcile"] = False
            op["error"] = msg
            try:
                self._persist_bot(bot)
            except Exception:
                logger.exception("failed to persist reject for %s", bot.get("id"))
        bot["pending_op"] = None
        bot["last_exec_error"] = msg
        self._activity(bot, "error", msg)
        try:
            self._persist_bot(bot)
        except Exception:
            logger.exception("failed to clear rejected op for %s", bot.get("id"))

    def _clear_pending(self, bot: dict) -> None:
        bot["pending_op"] = None
        try:
            self._persist_bot(bot)
        except Exception:
            logger.exception("failed to clear pending op for %s", bot.get("id"))

    def _mark_trade_resumed(self, bot: dict) -> None:
        """Terminal pending_op is done — trading may continue next cycle.

        The current loop tick skips new evaluator entries so a just-restored
        fill cannot immediately POST another BUY.
        """
        bot["last_exec_error"] = None
        bot["_resume_skip_eval"] = True

    def _keep_needs_reconcile(self, bot: dict, msg: str) -> None:
        op = bot.get("pending_op")
        if isinstance(op, dict):
            op["needs_reconcile"] = True
            if op.get("state") in ("intent", "posted"):
                op["state"] = "uncertain"
            op["error"] = msg
        bot["last_exec_error"] = msg
        op = bot.get("pending_op")
        op_id = op.get("id") if isinstance(op, dict) else ""
        self._activity_once(bot, activity_level(msg), msg, f"keep:{op_id}:{msg}")
        try:
            self._persist_bot(bot)
        except Exception:
            self._dirty.add(bot.get("id"))

    def _activity_once(self, bot: dict, level: str, msg: str, key: str) -> None:
        bot["last_exec_error"] = msg
        if bot.get("_act_once") == key:
            return
        bot["_act_once"] = key
        self._activity(bot, level, msg)

    def _placement_legs(self, op: dict) -> tuple[float, float] | None:
        """Return (usd, shares). BUY making/taking; SELL reversed. No cash."""
        try:
            making = float(op.get("making_amount"))
            taking = float(op.get("taking_amount"))
        except (TypeError, ValueError):
            return None
        if making <= 0 or taking <= 0:
            return None
        if str(op.get("side") or "").upper() == "SELL":
            return taking, making
        return making, taking

    def _fill_with_placement_amounts(self, op: dict, fill: dict) -> dict:
        """Confirmed trades with unknown USD/shares may use the POST notional."""
        if fill.get("state") != FILL_CONFIRMED or fill.get("amounts_ready", True):
            return fill
        legs = self._placement_legs(op)
        if legs is None:
            return fill
        usd, shares = legs
        out = dict(fill)
        out["shares"] = shares
        out["usd"] = usd
        out["price"] = usd / shares
        out["price_cents"] = (usd / shares) * 100.0
        out["amounts_ready"] = True
        out["error"] = None
        return out

    def _unproven_may_release(self, op: dict, now_ms: float,
                              current_window_id) -> bool:
        wid = op.get("window_id")
        if current_window_id is not None and wid is not None and wid != current_window_id:
            return True
        created = op.get("created_ms")
        if created is None:
            return False
        try:
            age = now_ms - float(created)
        except (TypeError, ValueError):
            return False
        return age >= RECONCILE_GIVE_UP_MS

    def _release_unproven(self, bot: dict, account: dict, op: dict) -> None:
        """Drop a stuck op from the token share balance. Never POSTs, never uses cash."""
        token_id = op.get("token_id")
        if not token_id:
            self._keep_needs_reconcile(bot, "reconcile held — pending token_id missing")
            return
        try:
            shares = token_share_balance(
                account, str(token_id), version=op.get("protocol_version"))
        except BalanceReadError as exc:
            self._keep_needs_reconcile(
                bot, f"reconcile held — share balance unread ({exc})")
            return
        if shares <= 0:
            pos = bot.get("position")
            if not isinstance(pos, dict) or str(pos.get("token_id") or "") == str(token_id):
                bot["position"] = None
            self._activity_once(
                bot, "error",
                "unproven op cleared — token balance 0, no retry",
                f"release0:{op.get('id')}",
            )
            self._clear_pending(bot)
            self._mark_trade_resumed(bot)
            try:
                self._persist_bot(bot)
            except Exception:
                self._dirty.add(bot.get("id"))
            return
        side = op.get("market_side")
        pos = bot.get("position")
        if side not in ("UP", "DOWN") and isinstance(pos, dict):
            side = pos.get("side")
        if side not in ("UP", "DOWN"):
            self._keep_needs_reconcile(
                bot, "reconcile held — shares exist but market side unknown")
            return
        legs = self._placement_legs(op)
        cost = legs[0] if legs and str(op.get("side") or "").upper() == "BUY" else (
            pos.get("cost") if isinstance(pos, dict) else None
        )
        if isinstance(pos, dict) and cost is not None:
            old_shares = float(pos.get("shares") or 0)
            if old_shares > float(shares) > 0:
                cost = float(cost) * (float(shares) / old_shares)
        bot["position"] = {
            "side": side,
            "shares": float(shares),
            "cost": None if cost is None else float(cost),
            "entry_cents": None,
            "fee_paid": None,
            "window_id": op.get("window_id"),
            "entry_t": op.get("created_ms"),
            "asset": (pos or {}).get("asset") if isinstance(pos, dict) else None,
            "token_id": token_id,
            "condition_id": self._kept_condition_id(op, pos),
            "protocol_version": op.get("protocol_version"),
            "order_id": op.get("order_id"),
            "tx_hashes": op.get("transactions_hashes") or [],
            "trade_ids": list(op.get("trade_ids") or []),
            "fill_state": "share_balance",
            "fill_final": True,
            "fill_uncertain": False,
            "needs_reconcile": False,
        }
        self._activity_once(
            bot, "order",
            f"unproven op cleared — {float(shares):.4f} sh on token, no retry",
            f"release:{op.get('id')}",
        )
        self._clear_pending(bot)
        self._mark_trade_resumed(bot)
        try:
            self._persist_bot(bot)
        except Exception:
            self._dirty.add(bot.get("id"))

    def _reconcile_pending(self, bot: dict, account: dict | None, *,
                           force: bool = False, current_window_id=None) -> None:
        """Prove an in-flight CLOB op from order/trade records. Never infers from cash."""
        op = bot.get("pending_op")
        if not isinstance(op, dict) or not self._op_blocks(bot):
            return
        now_ms = time.time() * 1000.0
        last = float(op.get("last_reconcile_ms") or 0)
        if not force and last and (now_ms - last) < 5_000:
            return
        op["last_reconcile_ms"] = now_ms
        if account is None:
            self._keep_needs_reconcile(bot, "reconcile withheld — no linked account")
            return

        oid = op.get("order_id") or op.get("orderID")
        trade_ids = list(op.get("trade_ids") or op.get("tradeIDs") or [])
        if not oid and not trade_ids:
            if self._unproven_may_release(op, now_ms, current_window_id):
                self._release_unproven(bot, account, op)
                return
            self._keep_needs_reconcile(
                bot,
                "pending_op has no order_id or trade_ids — cannot prove fill",
            )
            return

        side = str(op.get("side") or "").upper() or "BUY"
        resp = {
            "ok": True,
            "order_id": oid,
            "trade_ids": trade_ids,
            "making_amount": op.get("making_amount"),
            "taking_amount": op.get("taking_amount"),
            "transactions_hashes": op.get("transactions_hashes") or [],
            "side": side,
            "asset_id": op.get("token_id"),
        }
        try:
            fill = resolve_fill(account, resp, side)
        except Exception as exc:
            logger.warning("reconcile resolve_fill failed %s: %r", bot.get("id"), exc)
            if self._unproven_may_release(op, now_ms, current_window_id):
                self._release_unproven(bot, account, op)
                return
            self._keep_needs_reconcile(bot, f"reconcile fill read failed: {exc}")
            return

        fill = self._fill_with_placement_amounts(op, fill)
        state = fill.get("state")
        intent = str(op.get("intent") or "").lower()
        if state in (FILL_PENDING, FILL_UNCERTAIN) or (
            state == FILL_CONFIRMED and not fill.get("amounts_ready", True)
        ):
            if self._unproven_may_release(op, now_ms, current_window_id):
                self._release_unproven(bot, account, op)
                return
            self._keep_needs_reconcile(
                bot, fill.get("error") or f"reconcile {state} — still unproven")
            return
        if state == FILL_EMPTY:
            pos = bot.get("position")
            if intent == "buy" and isinstance(pos, dict):
                pos_oid = pos.get("order_id")
                if pos_oid and oid and str(pos_oid) == str(oid):
                    bot["position"] = None
            elif intent in ("sell", "flatten") and isinstance(pos, dict):
                pos["needs_reconcile"] = False
                pos["fill_uncertain"] = False
            self._finish_reject(
                bot, fill.get("error") or "order confirmed not filled")
            self._mark_trade_resumed(bot)
            try:
                self._persist_bot(bot)
            except Exception:
                self._dirty.add(bot.get("id"))
            return
        if state != FILL_CONFIRMED:
            self._keep_needs_reconcile(
                bot, fill.get("error") or f"reconcile {state} — still unproven")
            return

        if intent == "buy":
            self._restore_buy_from_fill(bot, op, fill)
        elif intent in ("sell", "flatten"):
            self._restore_close_from_fill(bot, op, fill, intent)
        else:
            self._keep_needs_reconcile(
                bot, f"unknown pending intent {intent!r} — held for reconcile")

    def _restore_buy_from_fill(self, bot: dict, op: dict, fill: dict) -> None:
        market_side = op.get("market_side")
        pos = bot.get("position")
        if not market_side and isinstance(pos, dict):
            market_side = pos.get("side")
        if market_side not in ("UP", "DOWN"):
            self._keep_needs_reconcile(
                bot, "BUY fill proven but market side unknown")
            return
        oid = fill.get("order_id") or op.get("order_id")
        if isinstance(pos, dict) and pos.get("order_id") and oid:
            if str(pos["order_id"]) != str(oid):
                self._keep_needs_reconcile(
                    bot, "BUY fill order_id conflicts with existing position")
                return
        shares = fill.get("shares")
        usd = fill.get("usd")
        bot["position"] = {
            "side": market_side,
            "shares": None if shares is None else float(shares),
            "cost": None if usd is None else float(usd),
            "entry_cents": (
                None if fill.get("price_cents") is None
                else float(fill.get("price_cents"))
            ),
            "fee_paid": (
                None if not fill.get("fee_known") or fill.get("fee") is None
                else float(fill.get("fee"))
            ),
            "window_id": op.get("window_id"),
            "entry_t": op.get("created_ms"),
            "asset": (pos or {}).get("asset") if isinstance(pos, dict) else None,
            "token_id": op.get("token_id"),
            "condition_id": self._kept_condition_id(op, pos),
            "protocol_version": op.get("protocol_version"),
            "order_id": oid,
            "tx_hashes": fill.get("tx_hashes") or [],
            "trade_ids": fill.get("trade_ids") or [],
            "fill_state": fill.get("state"),
            "fill_final": True,
            "fill_uncertain": False,
            "needs_reconcile": False,
        }
        rt = self._rt.setdefault(bot["id"], _fresh_rt())
        wid = op.get("window_id")
        if wid is not None and rt.setdefault("entries", {}).get(wid, 0) < 1:
            rt["entries"][wid] = 1
        cents = fill.get("price_cents")
        self._activity(
            bot, "order",
            f"BUY {market_side} {float(shares or 0):.4f} sh"
            + (f" @ {cents:.1f}¢" if cents is not None else "")
            + f" (${float(usd or 0):.4f} from CLOB) — reconciled"
            + (f" id={str(oid)[:16]}…" if oid else ""),
        )
        self._clear_pending(bot)
        self._mark_trade_resumed(bot)
        try:
            self._persist_bot(bot)
        except Exception:
            self._dirty.add(bot.get("id"))

    def _restore_close_from_fill(self, bot: dict, op: dict, fill: dict,
                                 intent: str) -> None:
        pos = bot.get("position")
        proceeds = fill.get("usd")
        sh = fill.get("shares")
        if proceeds is None or sh is None:
            self._keep_needs_reconcile(
                bot, "SELL fill amounts not final — held for reconcile")
            return
        if not isinstance(pos, dict):
            self._clear_pending(bot)
            self._mark_trade_resumed(bot)
            try:
                self._persist_bot(bot)
            except Exception:
                self._dirty.add(bot.get("id"))
            return
        cost = float(pos.get("cost") or 0) + float(pos.get("fee_paid") or 0)
        pnl = float(proceeds) - cost
        px = fill.get("price_cents")
        self._activity(
            bot, "order",
            f"SELL {pos.get('side')} {float(sh):.4f} sh"
            + (" flatten" if intent == "flatten" else "")
            + (f" @ {px:.1f}¢" if px else "")
            + f" ${float(proceeds):.4f} from CLOB — reconciled",
        )
        self._clear_pending(bot)
        self._finish_close(
            bot, pos, "SELL", pnl,
            "round ended — flatten leftover" if intent == "flatten" else "reconciled sell",
        )
        self._mark_trade_resumed(bot)
        try:
            self._persist_bot(bot)
        except Exception:
            self._dirty.add(bot.get("id"))

    def _hold_for_reconcile(self, bot: dict, pos: dict | None, msg: str) -> None:
        if isinstance(pos, dict):
            pos["needs_reconcile"] = True
        op = bot.get("pending_op")
        if isinstance(op, dict) and op.get("state") in ("intent", "posted", "uncertain"):
            op["needs_reconcile"] = True
            if op.get("state") == "intent":
                op["state"] = "uncertain"
            op["error"] = msg
        key = f"hold:{(op or {}).get('id') if isinstance(op, dict) else ''}:{msg}"
        if bot.get("_act_once") == key:
            bot["last_exec_error"] = msg
            return
        self._activity_once(bot, activity_level(msg), msg, key)
        try:
            self._persist_bot(bot)
        except Exception:
            self._dirty.add(bot["id"])

    def _activity(self, bot: dict, level: str, msg: str) -> None:
        entry = {"t": time.time() * 1000.0, "level": level, "msg": msg}
        log = bot.setdefault("activity_log", [])
        log.append(entry)
        if len(log) > 150:
            bot["activity_log"] = log[-150:]
        self._dirty.add(bot["id"])
        if level in ("error", "order", "wait"):
            logger.info("real bot %s [%s] %s", bot["id"], level, msg)

    def _quote(self, pipe, side: str):
        return self._bot_manager._quote(pipe, side)

    def _log(self, bot: dict, action: str, side, price_cents, shares, pnl, reason, wid=None):
        trade = {
            "t": time.time() * 1000.0,
            "action": action,
            "side": side,
            "price_cents": price_cents,
            "shares": shares,
            "pnl": pnl,
            "reason": reason,
            "window_id": wid,
            "round_label": round_label_et(wid) if wid is not None else None,
        }
        bot.setdefault("trades", []).append(trade)
        if len(bot["trades"]) > 500:
            bot["trades"] = bot["trades"][-500:]
        self._dirty.add(bot["id"])

    def _bump_winloss(self, bot: dict, pnl: float) -> None:
        if pnl > 0:
            bot["stats"]["wins"] += 1
        elif pnl < 0:
            bot["stats"]["losses"] += 1

    def buy(self, bot: dict, rt: dict, pipe, side: str, order_usd: float,
            max_entry_cents: float, reason: str) -> bool:
        if self._op_blocks(bot):
            bot["last_exec_error"] = "unfinished operation — new buy withheld"
            return False
        pos = bot.get("position")
        if pos is not None:
            if self._reconcile_blocked(pos):
                bot["last_exec_error"] = (
                    "position held for reconcile — new buy withheld"
                )
            return False
        account = storage.get_account(bot.get("account_id", ""))
        if account is None:
            bot["last_exec_error"] = "no linked account"
            self._activity(bot, "error", "no linked account")
            return False
        if bot.get("balance_unread"):
            msg = bot.get("last_exec_error") or "CLOB cash unread — buy withheld"
            bot["last_exec_error"] = msg
            if bot.get("_last_skip") != msg:
                bot["_last_skip"] = msg
                self._activity(bot, "skip", msg)
            return False

        now = pipe.poly_now_ms()
        if pipe.window_end_ms - now < 5000 or now - pipe.window_start_ms < 2000:
            return False
        ask, ask_size, bid = self._quote(pipe, side)
        if ask is None or ask > max_entry_cents or ask <= 0 or ask >= 100:
            return False
        if bid is None or bid <= 0 or (ask - bid) > 20:
            return False

        price = ask / 100.0
        shares = order_usd / price
        if ask_size is not None:
            shares = min(shares, ask_size)
        min_shares = REAL_MIN_ORDER_SHARES
        need_usd = round(min_shares * price, 2)
        if shares < min_shares:
            msg = (
                f"skip BUY {side}: lot ${order_usd:.2f} = {shares:.2f} sh @ {ask:.0f}¢ "
                f"— CLOB min is {min_shares:.0f} sh (~${need_usd:.2f}). Raise LOT."
            )
            bot["last_exec_error"] = msg
            if bot.get("_last_skip") != msg:
                bot["_last_skip"] = msg
                self._activity(bot, "skip", msg)
            return False
        cost = shares * price
        fee = taker_fee(shares, price)
        if cost + fee > bot.get("balance", 0):
            msg = (f"insufficient balance ${bot.get('balance', 0):.2f} "
                   f"need ${cost + fee:.2f} for {shares:.1f} sh @ {ask:.0f}¢")
            bot["last_exec_error"] = msg
            self._activity(bot, "skip", msg)
            return False

        market = getattr(getattr(pipe, "polymarket", None), "market", None) or {}
        try:
            version = protocol_version(market.get("version") if isinstance(market, dict) else None)
        except BalanceReadError:
            bot["last_exec_error"] = "market protocol version unknown — buy withheld"
            self._activity(bot, "error", bot["last_exec_error"])
            return False
        token_id = token_for_side(pipe, side)
        if not token_id:
            bot["last_exec_error"] = "no market token id"
            self._activity(bot, "error", "no market token id")
            return False

        self._activity(bot, "signal",
                         f"BUY {side} ${order_usd:.2f} @ {ask:.0f}¢ — {reason}")
        op = self._begin_intent(
            bot, intent="buy", side="BUY", token_id=token_id, version=version,
            window_id=pipe.window_id, amount=order_usd, price_limit=price,
            account_id=bot.get("account_id"), market_side=side,
            condition_id=str(market.get("condition_id") or "") if isinstance(market, dict) else "",
        )
        if op is None:
            return False
        try:
            resp = place_market_order(account, token_id, "BUY", order_usd, price)
        except Exception as exc:
            logger.warning("real buy send result unknown %s: %r", bot["id"], exc)
            self._mark_uncertain(
                bot, f"BUY send result unknown: {exc}")
            return False
        if isinstance(resp, dict) and resp.get("unknown_send"):
            self._mark_uncertain(
                bot, f"BUY send result unknown: {resp.get('message') or resp.get('code')}")
            return False
        bot["orders_placed"] = bot.get("orders_placed", 0) + 1
        self._record_placement(bot, resp)
        try:
            self._persist_bot(bot)
        except Exception:
            logger.exception("persist placement ids failed for %s", bot["id"])
        if isinstance(resp, dict) and (resp.get("rejected") or resp.get("ok") is False):
            self._finish_reject(
                bot,
                f"BUY rejected: {resp.get('message') or resp.get('code')}",
            )
            return False
        try:
            fill = self._clob_fill(
                bot, account, resp, "BUY", token_id, version=version)
        except Exception as exc:
            logger.warning("real buy fill read failed %s: %r", bot["id"], exc)
            self._mark_uncertain(
                bot, f"BUY posted; fill read failed: {exc}")
            return False

        state = fill.get("state")
        oid = fill.get("order_id") or (resp.get("order_id") if isinstance(resp, dict) else None)
        read_error = fill.get("share_balance_error")
        hold = state in (FILL_PENDING, FILL_UNCERTAIN) or bool(read_error)
        if state == FILL_EMPTY and not hold:
            msg = fill.get("error") or "BUY confirmed not filled"
            if isinstance(resp, dict) and (resp.get("order_id") or resp.get("orderID")):
                self._mark_uncertain(bot, msg)
            else:
                self._finish_reject(bot, msg)
            return False
        if state != FILL_CONFIRMED or not fill.get("amounts_ready", True):
            hold = True
        shares_filled = fill.get("shares")
        usd_filled = fill.get("usd")
        px = fill.get("price")
        entry_cents = fill.get("price_cents")
        if entry_cents is not None:
            entry_cents = float(entry_cents)
        fee_paid = fill.get("fee") if fill.get("fee_known") else None
        if fee_paid is not None:
            fee_paid = float(fee_paid)

        if hold:
            if read_error:
                bot["last_exec_error"] = (
                    "BUY posted; share balance unread — fill uncertain, held for reconcile"
                    f" ({read_error})"
                )
            else:
                bot["last_exec_error"] = (
                    fill.get("error") or f"BUY {state} — held for reconcile"
                )
            self._activity(
                bot, activity_level(bot["last_exec_error"] or ""), bot["last_exec_error"])
            op = bot.get("pending_op")
            if isinstance(op, dict):
                op["needs_reconcile"] = True
                if op.get("state") == "posted":
                    op["state"] = "uncertain" if state == FILL_UNCERTAIN else op["state"]
                op["error"] = bot["last_exec_error"]
        else:
            bot["last_exec_error"] = None
            cents = "" if entry_cents is None else f" @ {entry_cents:.1f}¢"
            self._activity(
                bot, "order",
                f"BUY {side} {float(shares_filled or 0):.4f} sh{cents} "
                f"(${float(usd_filled or 0):.4f} from CLOB) — {reason}"
                + (f" id={str(oid)[:16]}…" if oid else ""),
            )
            logger.info("REAL BUY %s %s fill=%s resp=%s", bot["id"], side, fill, resp)

        bot["position"] = {
            "side": side,
            "shares": None if shares_filled is None else float(shares_filled),
            "cost": None if usd_filled is None else float(usd_filled),
            "entry_cents": entry_cents,
            "fee_paid": fee_paid,
            "window_id": pipe.window_id, "entry_t": now,
            "asset": pipe.asset.key,
            "token_id": token_id,
            "condition_id": str(market.get("condition_id") or "") if isinstance(market, dict) else "",
            "protocol_version": version,
            "order_id": oid,
            "tx_hashes": fill.get("tx_hashes") or [],
            "trade_ids": fill.get("trade_ids") or [],
            "fill_state": state,
            "fill_final": bool(fill.get("final")) and not hold,
            "fill_uncertain": hold,
            "needs_reconcile": hold,
        }
        wid = pipe.window_id
        rt["entries"][wid] = rt["entries"].get(wid, 0) + 1
        if not hold:
            self._clear_pending(bot)
        else:
            try:
                self._persist_bot(bot)
            except Exception:
                self._dirty.add(bot["id"])
        return True

    def _finish_close(self, bot: dict, pos: dict, action: str, pnl: float, reason: str) -> None:
        self._bump_winloss(bot, pnl)
        bot["position"] = None
        bot["last_exec_error"] = None
        self._dirty.add(bot["id"])

    def _round_row(self, asset: str, window_id: int) -> dict | None:
        getter = getattr(storage, "get_round", None)
        if not callable(getter):
            return None
        try:
            row = getter(asset, window_id)
        except Exception:
            return None
        return row if isinstance(row, dict) else None

    def _resolve_round_outcome(self, pipe, pos: dict, now: float):
        """Official first, then decisive local math, then a 90s wait, then provisional.

        Returns (outcome, how, provisional, waiting). A void is (None, "void", ...).
        """
        wid = pos.get("window_id")
        asset = pos.get("asset") or ""
        end_ms = (int(wid) + 1) * CONFIG.window_ms
        results = getattr(pipe, "round_results", None) or {}
        result = results.get(wid) if isinstance(results, dict) else None
        if not isinstance(result, dict):
            result = {}
        if result.get("source") == "market" and result.get("outcome"):
            return result.get("outcome"), "official resolution", False, False
        db_row = self._round_row(asset, int(wid))
        if db_row is not None and db_row.get("outcome_official"):
            return db_row["outcome_official"], "official resolution (backscan)", False, False
        if result.get("outcome") is not None:
            final, ptb = result.get("final_twap"), result.get("ptb")
            margin = 0.0
            if final and ptb:
                margin = abs((float(final) - float(ptb)) / float(ptb) * 10_000.0)
            if margin >= LOCAL_SETTLE_MARGIN_BPS:
                return (
                    result.get("outcome"),
                    f"local TWAP math (decisive, {margin:.1f} bps)",
                    False,
                    False,
                )
        waited = now - end_ms
        if waited < OFFICIAL_WAIT_MS:
            return None, None, False, True
        if result.get("outcome") is not None:
            return (
                result.get("outcome"),
                "provisional local math — official may correct this",
                True,
                False,
            )
        if now >= float(pos.get("fetch_next") or 0):
            pos["fetch_next"] = now + 10_000
            fetched = fetch_official_outcome(asset, int(wid))
            if fetched:
                upserter = getattr(storage, "upsert_round", None)
                if callable(upserter):
                    try:
                        upserter(asset, int(wid), outcome_official=fetched)
                    except Exception:
                        logger.debug("official outcome persist failed", exc_info=True)
                return fetched, "official resolution (direct lookup)", False, False
        if waited < VOID_DEADLINE_MS:
            return None, None, False, True
        return None, "void", False, False

    def _correct_provisional(self, bot: dict, pipe) -> None:
        """Flip a provisional settle when the official outcome disagrees. No cash credit."""
        pending = bot.get("pending_official")
        if not pending or pipe is None:
            return
        now = pipe.poly_now_ms()
        for wid_str in list(pending.keys()):
            entry = pending[wid_str]
            if not isinstance(entry, dict):
                pending.pop(wid_str, None)
                continue
            wid = int(wid_str)
            asset = entry.get("asset") or bot.get("asset") or ""
            results = getattr(pipe, "round_results", None) or {}
            result = results.get(wid) if isinstance(results, dict) else None
            official = None
            if isinstance(result, dict) and result.get("source") == "market":
                official = result.get("outcome")
            if not official:
                db_row = self._round_row(asset, wid)
                if db_row is not None:
                    official = db_row.get("outcome_official")
            if not official:
                if now - float(entry.get("t_ms") or now) > RECONCILE_TTL_MS:
                    pending.pop(wid_str, None)
                    self._dirty.add(bot["id"])
                continue
            pending.pop(wid_str, None)
            self._dirty.add(bot["id"])
            if official == entry.get("outcome"):
                continue
            won_now = official == entry.get("side")
            shares = float(entry.get("shares") or 0)
            delta = shares * (1.0 if won_now else -1.0)
            stats = bot.setdefault("stats", {})
            if won_now:
                stats["wins"] = stats.get("wins", 0) + 1
                stats["losses"] = max(0, stats.get("losses", 0) - 1)
            else:
                stats["wins"] = max(0, stats.get("wins", 0) - 1)
                stats["losses"] = stats.get("losses", 0) + 1
            label = round_label_et(wid)
            self._log(
                bot, "ADJUST", entry.get("side"), 100.0 if won_now else 0.0,
                shares, round(delta, 4),
                f"round {label} OFFICIALLY resolved {official} — "
                f"correcting provisional {entry.get('outcome')}",
                wid,
            )
            self._activity(
                bot, "order",
                f"ADJUST {entry.get('side')} {shares:.4f} sh — round {label} "
                f"official {official}, was {entry.get('outcome')}",
            )

    def _book_resolution(self, bot: dict, pos: dict, settle_shares: float,
                         cost: float, outcome: str | None, how: str | None,
                         provisional: bool, now: float) -> None:
        """Record $1 or $0 per share. Does not credit CLOB cash and does not POST."""
        wid = pos.get("window_id")
        side = pos.get("side")
        label = round_label_et(wid) if wid is not None else ""
        asset = pos.get("asset") or bot.get("asset")
        bot["last_exec_error"] = None
        if outcome is None:
            self._log(
                bot, "VOID", side, None, settle_shares, 0.0,
                f"round {label}: no result — position cleared, cash not refunded",
                wid,
            )
            self._activity(
                bot, "order",
                f"VOID {side} {settle_shares:.4f} sh — round {label}: no result",
            )
        else:
            won = outcome == side
            proceeds = settle_shares * (1.0 if won else 0.0)
            pnl = proceeds - cost
            self._bump_winloss(bot, pnl)
            px = 100.0 if won else 0.0
            self._log(
                bot, "SETTLE", side, px, settle_shares, pnl,
                f"round {label} resolved {outcome} ({how})",
                wid,
            )
            self._activity(
                bot, "order",
                f"SETTLE {side} {settle_shares:.4f} sh @ {px:.0f}¢ "
                f"pnl ${pnl:+.4f} — round {label} resolved {outcome} ({how})",
            )
            if provisional and wid is not None:
                bot.setdefault("pending_official", {})[str(wid)] = {
                    "side": side,
                    "shares": round(settle_shares, 4),
                    "outcome": outcome,
                    "t_ms": now,
                    "asset": asset,
                }
            else:
                self._enqueue_redeem(bot, pos, side, label)
        stats = bot.setdefault("stats", {})
        stats["rounds"] = stats.get("rounds", 0) + 1
        bot["position"] = None
        self._dirty.add(bot["id"])

    def _enqueue_redeem(self, bot: dict, pos: dict, side, label: str) -> None:
        """Remember a settled token so the redeem pass can burn it on Polymarket."""
        cid = str(pos.get("condition_id") or "")
        pid = str(pos.get("token_id") or "")
        if not cid and not pid:
            return
        key = cid or pid
        queue = bot.setdefault("redeem_queue", [])
        if any(str(item.get("condition_id") or item.get("position_id")) == key for item in queue):
            return
        queue.append({
            "condition_id": cid,
            "position_id": pid,
            "label": f"{side} round {label}".strip(),
        })
        if len(queue) > 20:
            del queue[:-20]
        self._dirty.add(bot["id"])

    def _held_tokens(self, account_id: str) -> list[str]:
        held = []
        for bot in self.bots.values():
            if bot.get("account_id") != account_id:
                continue
            token = (bot.get("position") or {}).get("token_id")
            if token:
                held.append(str(token))
        return held

    def _schedule_redeem(self, account: dict) -> None:
        account_id = account.get("id")
        if not account_id or account_id in self._redeem_inflight:
            return
        queued = []
        for bot in self.bots.values():
            if bot.get("account_id") != account_id:
                continue
            queued.extend(bot.get("redeem_queue") or [])
        now = time.time()
        self._redeem_skip = {
            key: until for key, until in self._redeem_skip.items() if until > now
        }
        self._redeem_inflight.add(account_id)
        asyncio.create_task(self._run_redeem(
            account_id, queued, self._held_tokens(account_id), list(self._redeem_skip),
        ))

    async def _run_redeem(self, account_id: str, queued, held, skip) -> None:
        try:
            account = storage.get_account(account_id) or {"id": account_id}
            payload = await asyncio.to_thread(
                redeem_account_positions, account, queued, held, skip,
            )
        except Exception as exc:
            payload = {
                "notes": [{
                    "level": "wait",
                    "msg": f"REDEEM withheld: {type(exc).__name__}",
                }],
                "done": [],
                "skip": {},
            }
        finally:
            self._redeem_inflight.discard(account_id)
        self._redeem_notes.append((account_id, payload))

    def _drain_redeem_notes(self) -> None:
        notes = self._redeem_notes
        self._redeem_notes = []
        for account_id, payload in notes:
            done = {str(key) for key in (payload.get("done") or [])}
            for bot in self.bots.values():
                if bot.get("account_id") != account_id:
                    continue
                if done:
                    bot["redeem_queue"] = [
                        item for item in (bot.get("redeem_queue") or [])
                        if str(item.get("condition_id") or item.get("position_id")) not in done
                    ]
                    self._dirty.add(bot["id"])
            self._redeem_skip.update(payload.get("skip") or {})
            bot = next(
                (
                    b for b in self.bots.values()
                    if b.get("account_id") == account_id and b.get("status") == "running"
                ),
                None,
            )
            if bot is None:
                continue
            for note in payload.get("notes") or []:
                level = note.get("level") or "wait"
                if level != "order":
                    level = activity_level(note.get("msg") or "")
                self._activity(bot, level, note.get("msg") or "REDEEM withheld")

    def _close_stale(self, bot: dict, account: dict, pos: dict, pipe=None) -> None:
        """Settle a leftover position after the 5m round rolls. Never POSTs."""
        if self._op_blocks(bot):
            self._hold_for_reconcile(
                bot, pos,
                "unfinished operation — flatten withheld",
            )
            return
        if self._reconcile_blocked(pos):
            self._hold_for_reconcile(
                bot, pos,
                "position held for reconcile — flatten withheld",
            )
            return
        token_id = pos.get("token_id")
        shares = float(pos.get("shares") or 0)
        version = pos.get("protocol_version")
        try:
            protocol_version(version)
        except BalanceReadError:
            self._hold_for_reconcile(
                bot, pos,
                "position protocol version unknown — held for reconcile",
            )
            return
        if not token_id:
            self._hold_for_reconcile(
                bot, pos,
                "stale position missing token_id — held for reconcile",
            )
            return
        try:
            held = token_share_balance(account, token_id, version=version)
        except BalanceReadError as exc:
            self._hold_for_reconcile(bot, pos, f"share balance unread: {exc}")
            return
        if held <= 0 or shares <= 0:
            self._hold_for_reconcile(
                bot, pos,
                f"flatten: confirmed 0 shares held (held={held:.4f}) — "
                "not treating as round loss; held for reconcile",
            )
            return
        if pipe is None:
            return
        pos["asset"] = pos.get("asset") or bot.get("asset")
        now = pipe.poly_now_ms()
        outcome, how, provisional, waiting = self._resolve_round_outcome(pipe, pos, now)
        if waiting:
            pos["awaiting_official"] = True
            self._activity_once(
                bot, "wait",
                "round ended — waiting for resolution",
                f"settle-wait:{pos.get('window_id')}",
            )
            self._dirty.add(bot["id"])
            return
        settle_shares = min(shares, float(held))
        ratio = settle_shares / shares if shares > 0 else 1.0
        cost = (float(pos.get("cost") or 0) + float(pos.get("fee_paid") or 0)) * ratio
        self._book_resolution(
            bot, pos, settle_shares, cost, outcome, how, provisional, now,
        )

    def sell(self, bot: dict, pipe, reason: str) -> bool:
        pos = bot.get("position")
        if pos is None or pos["window_id"] != pipe.window_id:
            return False
        if self._op_blocks(bot):
            self._hold_for_reconcile(
                bot, pos,
                "unfinished operation — sell withheld",
            )
            return False
        if self._reconcile_blocked(pos):
            self._hold_for_reconcile(
                bot, pos,
                "position held for reconcile — sell withheld",
            )
            return False
        account = storage.get_account(bot.get("account_id", ""))
        if account is None:
            return False

        _ask, _sz, bid = self._quote(pipe, pos["side"])
        if bid is None or bid <= 0:
            return False
        price = bid / 100.0
        shares = float(pos.get("shares") or 0)
        token_id = pos.get("token_id")
        if not token_id:
            bot["last_exec_error"] = "no token for sell"
            return False
        try:
            version = protocol_version(pos.get("protocol_version"))
        except BalanceReadError:
            self._hold_for_reconcile(
                bot, pos,
                "position protocol version unknown — sell withheld for reconcile",
            )
            return False

        self._activity(bot, "signal", f"SELL {pos['side']} {shares:.1f} sh @ {bid:.0f}¢ — {reason}")
        try:
            held = token_share_balance(account, token_id, version=version)
        except BalanceReadError as exc:
            self._hold_for_reconcile(bot, pos, f"share balance unread: {exc}")
            return False
        if held <= 0 or shares <= 0:
            self._hold_for_reconcile(
                bot, pos,
                f"SELL withheld: confirmed 0 shares held (held={held:.4f})",
            )
            return False
        sell_shares = round(min(shares, held) * 0.999, 4)
        if sell_shares <= 0:
            self._hold_for_reconcile(
                bot, pos,
                "SELL withheld: sell size is 0 after sizing",
            )
            return False
        op = self._begin_intent(
            bot, intent="sell", side="SELL", token_id=token_id, version=version,
            window_id=pos.get("window_id"), amount=sell_shares, price_limit=price,
            account_id=bot.get("account_id"), market_side=pos.get("side"),
            condition_id=str(pos.get("condition_id") or ""),
        )
        if op is None:
            self._hold_for_reconcile(
                bot, pos, bot.get("last_exec_error") or "sell intent not persisted",
            )
            return False
        try:
            resp = place_market_order(account, token_id, "SELL", sell_shares, price)
        except Exception as exc:
            logger.warning("real sell send result unknown %s: %r", bot["id"], exc)
            self._mark_uncertain(bot, f"SELL send result unknown: {exc}", pos)
            return False
        if isinstance(resp, dict) and resp.get("unknown_send"):
            self._mark_uncertain(
                bot,
                f"SELL send result unknown: {resp.get('message') or resp.get('code')}",
                pos,
            )
            return False
        bot["orders_placed"] = bot.get("orders_placed", 0) + 1
        self._record_placement(bot, resp)
        try:
            self._persist_bot(bot)
        except Exception:
            logger.exception("persist sell placement failed for %s", bot["id"])
        if isinstance(resp, dict) and (resp.get("rejected") or resp.get("ok") is False):
            self._finish_reject(
                bot,
                f"SELL rejected: {resp.get('message') or resp.get('code')}",
            )
            return False
        try:
            fill = self._clob_fill(
                bot, account, resp, "SELL", token_id, version=version)
        except Exception as exc:
            logger.warning("real sell fill read failed %s: %r", bot["id"], exc)
            self._mark_uncertain(bot, f"SELL posted; fill read failed: {exc}", pos)
            return False

        if fill.get("state") != FILL_CONFIRMED or not fill.get("amounts_ready", True):
            self._hold_for_reconcile(
                bot, pos,
                fill.get("error") or f"SELL {fill.get('state')} — held for reconcile",
            )
            return False
        proceeds = fill.get("usd")
        sh = fill.get("shares")
        if proceeds is None or sh is None:
            self._hold_for_reconcile(
                bot, pos, "SELL fill amounts not final — held for reconcile")
            return False
        px = fill.get("price_cents")
        bot["last_exec_error"] = None
        self._activity(
            bot, "order",
            f"SELL {pos['side']} {float(sh):.4f} sh"
            + (f" @ {px:.1f}¢" if px else "")
            + f" ${float(proceeds):.4f} from CLOB — {reason}",
        )
        logger.info("REAL SELL %s %s fill=%s resp=%s", bot["id"], pos["side"], fill, resp)
        pnl = float(proceeds) - (float(pos.get("cost") or 0) + float(pos.get("fee_paid") or 0))
        self._bump_winloss(bot, pnl)
        bot["position"] = None
        self._clear_pending(bot)
        return True

    async def _loop(self) -> None:
        balance_tick = 0
        while True:
            await asyncio.sleep(0.25)
            balance_tick += 1
            try:
                self._drain_redeem_notes()
                for bot in list(self.bots.values()):
                    if bot.get("status") != "running":
                        continue
                    asset = bot.get("asset")
                    if asset == COPY_ALL:
                        bot["last_exec_error"] = "copy_trader not supported on real yet"
                        continue

                    pipe = self._hub.pipelines.get(asset)
                    if pipe is None:
                        pipe = await self._hub.retain(asset, bot["id"])
                    account = storage.get_account(bot.get("account_id", ""))
                    if account is None:
                        continue

                    if balance_tick % 40 == 0:  # ~10s
                        self._sync_balance(bot, account)
                    if balance_tick % 80 == 1:  # ~20s, and once shortly after start
                        self._schedule_redeem(account)

                    rt = self._rt.setdefault(bot["id"], _fresh_rt())
                    now = pipe.poly_now_ms()
                    wid = pipe.window_id
                    if rt.get("round") != wid:
                        rt["round"] = wid
                        rt["entries"] = {}
                        rt["switches"] = 0
                        rt["adverse_since"] = None

                    pos = bot.get("position")
                    was_blocked = self._op_blocks(bot)
                    if was_blocked:
                        self._reconcile_pending(
                            bot, account, current_window_id=wid)
                    self._correct_provisional(bot, pipe)
                    if self._op_blocks(bot):
                        continue
                    pos = bot.get("position")
                    skip_new = bool(bot.pop("_resume_skip_eval", False))
                    if pos is not None and pos["window_id"] != wid:
                        self._close_stale(bot, account, pos, pipe)
                        continue
                    if pos is not None and lock_profit_if_bid(self, bot, pipe):
                        continue
                    if skip_new:
                        continue

                    fn = _EVALUATORS.get(bot.get("strategy"))
                    if fn is not None:
                        fn(self, bot, rt, pipe, now)

                if balance_tick % 4 == 0:
                    self._save()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("real bot loop error")

    def _synthetic_for_brain(self, bot: dict) -> dict:
        return {
            "id": bot["id"],
            "asset": bot["asset"],
            "strategy": bot["strategy"],
            "params": bot.get("params") or {},
            "position": bot.get("position"),
            "book": bot.get("book"),
            "portfolio": bot.get("portfolio") or {},
            "status": bot.get("status", "stopped"),
        }

    def _position_from_account(self, positions, asset, pipe):
        if not positions:
            return None
        wid = pipe.window_id if pipe else None
        slug_needle = f"{asset.lower()}-updown-5m" if asset != COPY_ALL else ""
        best = None
        for p in positions:
            slug = (p.get("event_slug") or "").lower()
            if asset != COPY_ALL and slug_needle not in slug:
                continue
            outcome = str(p.get("outcome") or "").upper()
            if outcome not in ("UP", "DOWN", "YES", "NO"):
                continue
            if float(p.get("cur_price_cents") or 0) <= 0:
                continue
            side = "UP" if outcome in ("UP", "YES") else "DOWN"
            pos_view = {
                "side": side,
                "shares": round(float(p.get("size") or 0), 4),
                "entry_cents": p.get("avg_price_cents", 0),
                "mark_cents": p.get("cur_price_cents"),
                "cost": round(float(p.get("size") or 0) * float(p.get("avg_price_cents") or 0) / 100, 4),
                "window_id": wid,
                "round_label": round_label_et(wid) if wid else "",
                "waiting_official": False,
                "asset": asset,
                "title": p.get("title") or "",
            }
            if slug_needle and slug_needle in slug:
                return pos_view
            best = best or pos_view
        return best

    def _stats_from_trades(self, trades: list[dict], positions: list[dict] | None = None) -> dict:
        slugs = {t.get("event_slug") for t in trades if t.get("event_slug")}
        wins = losses = 0
        fees = 0.0
        for t in trades:
            pnl = t.get("cash_pnl")
            if pnl is None:
                continue
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
        if not wins and not losses and positions:
            for p in positions:
                pnl = p.get("cash_pnl")
                if pnl is None:
                    continue
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
        return {"wins": wins, "losses": losses, "rounds": len(slugs), "fees": fees}

    def _format_trades_for_ui(self, trades: list[dict]) -> list[dict]:
        out = []
        for t in trades:
            action = str(t.get("action") or t.get("side") or "BUY").upper()
            if action not in ("BUY", "SELL"):
                action = "BUY"
            outcome = str(t.get("outcome") or "").upper()
            if outcome in ("YES",):
                outcome = "UP"
            elif outcome in ("NO",):
                outcome = "DOWN"
            shares = t.get("shares") if t.get("shares") is not None else t.get("size")
            out.append({
                "t": t.get("t"),
                "action": action,
                "side": outcome or None,
                "outcome": outcome or None,
                "price_cents": t.get("price_cents"),
                "shares": shares,
                "size": shares,
                "usd": t.get("usd"),
                "pnl": t.get("cash_pnl") if t.get("cash_pnl") is not None else t.get("pnl"),
                "cash_pnl": t.get("cash_pnl"),
                "reason": t.get("title") or t.get("event_slug") or t.get("reason") or "",
                "round_label": t.get("event_slug") or t.get("round_label") or "",
                "title": t.get("title") or "",
                "event_slug": t.get("event_slug") or "",
                "tx": t.get("tx") or "",
            })
        return out

    def public_view(self, bot: dict, snapshot: dict | None = None,
                    trades_limit: int = 20, include_brain: bool = False) -> dict:
        strat = STRATEGIES.get(bot.get("strategy"), {})
        account = storage.get_account(bot.get("account_id", ""))
        running = self._running(bot)

        positions = trades = []
        bot_cash_pnl = bot_pos_value = 0.0
        cash_balance = 0.0
        if snapshot:
            if snapshot.get("cash_balance") is not None:
                cash_balance = float(snapshot["cash_balance"])
            positions = filter_for_asset(snapshot.get("positions") or [], bot["asset"])
            trades = filter_for_asset(snapshot.get("trades") or [], bot["asset"])
            if bot["asset"] == COPY_ALL:
                positions = snapshot.get("positions") or []
                trades = snapshot.get("trades") or []
            bot_cash_pnl = round(sum(float(p.get("cash_pnl") or 0) for p in positions), 4)
            bot_pos_value = round(sum(float(p.get("current_value") or 0) for p in positions), 4)
        if cash_balance <= 0:
            cash_balance = float(bot.get("balance") or 0)

        pipe = self._hub.pipelines.get(bot["asset"])
        pos_view = self._position_from_account(positions, bot["asset"], pipe)
        if pos_view is None and bot.get("position"):
            # CLOB-confirmed open lot only (fill price/size from Polymarket).
            local_pos = bot["position"]
            mark = None
            if pipe and pipe.last_quote:
                key = "up_bid" if local_pos["side"] == "UP" else "down_bid"
                mark = pipe.last_quote.get(key)
            pos_view = {
                "side": local_pos["side"],
                "shares": round(float(local_pos.get("shares") or 0), 4),
                "entry_cents": local_pos.get("entry_cents"),
                "mark_cents": mark,
                "cost": round(float(local_pos.get("cost") or 0), 4),
                "window_id": local_pos.get("window_id"),
                "round_label": round_label_et(local_pos["window_id"]) if local_pos.get("window_id") is not None else "",
                "waiting_official": False,
                "asset": local_pos.get("asset") or bot["asset"],
                "order_id": local_pos.get("order_id"),
            }

        equity = round(cash_balance + bot_pos_value, 4)
        if snapshot and snapshot.get("total_equity") is not None and bot["asset"] == COPY_ALL:
            equity = round(float(snapshot["total_equity"]), 4)
        start_bal = bot.get("start_balance") or cash_balance or 1.0
        pnl = bot_cash_pnl if positions else round(equity - start_bal, 4)

        display_trades = self._format_trades_for_ui(trades[:trades_limit])

        view = {
            "id": bot["id"],
            "name": bot["name"],
            "asset": bot["asset"],
            "strategy": bot["strategy"],
            "strategy_name": strat.get("name", bot["strategy"]),
            "params": bot.get("params", {}),
            "status": "running" if running else "stopped",
            "account_id": bot["account_id"],
            "account_label": account["label"] if account else None,
            "account_address": (account.get("proxy_wallet") or account["address"]) if account else None,
            "source_paper_bot_id": bot.get("source_paper_bot_id"),
            "created_ms": bot.get("created_ms"),
            "real": True,
            "portfolio_value": equity,
            "cash_balance": cash_balance,
            "cash_unread": bool(bot.get("balance_unread")),
            "positions_value": round(bot_pos_value, 4),
            "account_cash_pnl": bot_cash_pnl,
            "profile_name": snapshot.get("profile_name") if snapshot else account.get("profile_name") if account else None,
            "positions": positions,
            "positions_count": len(positions),
            "equity": equity,
            "balance": round(cash_balance, 4),
            "pnl": pnl,
            "pnl_pct": round((pnl / start_bal) * 100, 2) if start_bal else 0,
            "start_balance": round(start_bal, 4),
            "position": pos_view,
            "book": None,
            "portfolio": [],
            "stats": self._stats_from_trades(trades, positions),
            "trades": display_trades,
            "orders_placed": bot.get("orders_placed", 0),
            "last_exec_error": bot.get("last_exec_error"),
            "balance_synced_ms": bot.get("balance_synced_ms"),
            "activity_log": list(reversed(bot.get("activity_log") or []))[:50],
        }

        if include_brain:
            view["brain"] = self._bot_manager.brain(self._synthetic_for_brain(bot))
        return view

    async def set_running(self, bot_id: str, running: bool) -> dict | None:
        bot = self.bots.get(bot_id)
        if bot is None:
            return None
        self._ensure_fields(bot)
        account = storage.get_account(bot.get("account_id", ""))
        if running and account:
            self._sync_balance(bot, account)

        bot["status"] = "running" if running else "stopped"
        if not running and not self._op_blocks(bot):
            bot["last_exec_error"] = None
        storage.save_real_bot(bot)
        asset = bot["asset"]
        if asset != COPY_ALL:
            if running:
                await self._hub.retain(asset, bot_id)
            else:
                await self._hub.unretain(asset, bot_id)
        logger.info("real bot %s %s balance=$%.2f", bot_id,
                    "started" if running else "stopped", bot.get("balance", 0))
        return bot

    def update(self, bot_id: str, name: str | None = None,
               params: dict | None = None) -> dict | None:
        bot = self.bots.get(bot_id)
        if bot is None:
            return None
        self._ensure_fields(bot)
        if params:
            bot.setdefault("params", {}).update(
                sanitize_params(bot["strategy"], params))
        if name:
            bot["name"] = str(name)[:40]
        storage.save_real_bot(bot)
        self._dirty.add(bot_id)
        return bot

    def import_from_paper(self, paper_bot: dict, account_id: str, name: str | None = None) -> dict:
        account = storage.get_account(account_id)
        if account is None:
            raise ValueError(f"unknown account {account_id!r}")

        bot_id = f"r_{uuid.uuid4().hex[:8]}"
        bot = {
            "id": bot_id,
            "name": (name or paper_bot["name"]).strip() or paper_bot["name"],
            "asset": paper_bot["asset"],
            "strategy": paper_bot["strategy"],
            "params": dict(paper_bot.get("params") or {}),
            "account_id": account_id,
            "source_paper_bot_id": paper_bot["id"],
            "status": "stopped",
            "created_ms": time.time() * 1000.0,
            "balance": 0.0,
            "start_balance": 0.0,
            "position": None,
            "trades": [],
            "stats": {"rounds": 0, "wins": 0, "losses": 0, "fees": 0.0},
            "orders_placed": 0,
            "last_exec_error": None,
            "balance_unread": False,
        }
        if account:
            try:
                bot["balance"] = sync_clob_balance(account)
                bot["start_balance"] = bot["balance"]
                bot["balance_unread"] = False
            except BalanceReadError as exc:
                bot["balance"] = 0.0
                bot["start_balance"] = 0.0
                bot["balance_unread"] = True
                bot["last_exec_error"] = f"CLOB cash unread: {exc}"
        self.bots[bot_id] = bot
        storage.save_real_bot(bot)
        logger.info("imported real bot %s from paper bot %s balance=$%.2f",
                    bot_id, paper_bot["id"], bot["balance"])
        return bot

    def delete(self, bot_id: str) -> bool:
        if bot_id not in self.bots:
            return False
        del self.bots[bot_id]
        storage.delete_real_bot(bot_id)
        return True
