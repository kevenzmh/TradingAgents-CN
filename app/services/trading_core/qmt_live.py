"""QMT 实盘适配器：真实委托通道（xtquant.xttrader）。

这是 trading_core 中 LiveAdapter 的具体实现。接入前必须理解三条硬约束：

1. **前置条件重**：需要本机运行 QMT 交易终端、 Royalsmith 期户在柜台
   侧开通交易权限、xtquant 包随 QMT 发行（qmt_adapter 的
   `_ensure_qmt_python_path` 自动发现）。三者缺一即不可用——适配器
   通过 `is_ready()` 上报，执行引擎在不可用时直接拒绝而非重试。

2. **异步回报模型**：`order_stock` 返回的是受理序号（-1 表示柜台拒绝），
   真正的成交状态需要轮询 `query_stock_orders`。回报同步由
   OrderSyncService（同包 sync.py）驱动，适配器本身只做单次委托/查询，
   不持有长连接——连接开销约几百毫秒，每次委托建立新会话更符合
   本系统低频（分析驱动）的交易节奏，也避免守护连接的状态管理。

3. **失败必须显式**：柜台拒绝、委托超时、部分成交撤余量等每一种
   未完全成交的形态都要落 status，绝不把"不知道"当"成功"。
"""

import logging
import time
from typing import Any, Dict, Optional

from app.services.data_sources.qmt_adapter import (
    _get_xttrader,
    _normalize_code,
)

logger = logging.getLogger(__name__)

# xttrader 常量（xtquant.xttrader 里的同名枚举，字面量写死避免导入期依赖）
ORDER_TYPE_BUY = 23  # xtconstant.STOCK_BUY
ORDER_TYPE_SELL = 24  # xtconstant.STOCK_SELL

# 价格类型（xtconstant）：
# LATEST 最新价 / FIX 限价 / MARKET_SHKL 上交所市价剩撤 ...
PRICE_TYPE_LATEST = 5
PRICE_TYPE_FIX = 11

_REJECT = -1  # order_stock 柜台拒单返回值


class QMTNotReady(RuntimeError):
    """QMT 实盘通道未就绪（终端未运行/账号未配置/包不可用）。"""


class QMTLiveAdapter:
    """QMT 真实委托适配器。

    与 executor.ExecutionAdapter 协议兼容（execute/cancel/query_orders），
    但**同步委托 + 显式状态查询**的形态决定了使用方式不同：
    execute() 只保证"委托已受理"，成交确认由 sync.py 轮询完成。
    """

    name = "qmt_live"

    def __init__(self):
        self._account_id = ""
        self._account_type = "STOCK"

    # ---- 就绪检查（执行引擎下单前必查）----

    def is_ready(self, *, deep_check: bool = False) -> bool:
        """通道就绪检查：xtquant 可用 + 账号已配置 + 终端可连接（可选）。

        参数：
            deep_check: False（默认）做轻量检查（配置 + 终端可用性），
                       几乎无开销，适合每次委托前调用。
                       True 做深度探测（真实连接终端 + 查持仓），
                       有几百毫秒开销，适合启动自检与健康监控定时任务。

        深度探测失败也不会抛异常，调用方根据返回值决定是否继续。
        """
        from app.core.config import get_settings

        settings = get_settings()
        self._account_id = settings.QMT_ACCOUNT_ID.strip()
        self._account_type = settings.QMT_ACCOUNT_TYPE.strip() or "STOCK"

        if not self._account_id:
            logger.warning("[QMT实盘] QMT_ACCOUNT_ID 未配置，通道不可用")
            return False
        if _get_xttrader() is None:
            logger.warning("[QMT实盘] xtquant 不可用（QMT 终端未安装），通道不可用")
            return False

        # 轻量检查到此为止（配置 + 模块可用性）
        if not deep_check:
            return True

        # 深度探测：真实连接 + 查持仓（验证终端在运行 + 账号可访问）
        try:
            trader = self._connect()
            from xtquant.xttype import StockAccount

            account = StockAccount(self._account_id, self._account_type)
            positions = trader.query_stock_positions(account)
            trader.stop()
            logger.info(
                "[QMT实盘·深度探测] 通道正常，账户 %s 持仓 %d 只",
                self._account_id, len(positions or []),
            )
            return True
        except Exception as exc:
            logger.warning(
                "[QMT实盘·深度探测] 失败: %s（轻量检查仍通过，委托时会再尝试）",
                exc,
            )
            return False

    # ---- 连接 ----

    def _connect_with_retry(self, attempts: int = 3) -> Any:
        """建立连接并带重试（P0-3）。

        **只重试连接建立阶段**。此时尚未向柜台发出任何委托，
        重试是幂等的、安全的。

        绝不对 order_stock() 重试——见 execute() 中的注释。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return self._connect()
            except QMTNotReady as exc:
                last_exc = exc
                msg = str(exc)
                # 配置类错误不可通过重试解决，立即失败
                if "未配置" in msg or "xtquant 不可用" in msg or "未找到 QMT userdata" in msg:
                    raise
                if attempt >= attempts:
                    raise
                backoff = min(2 ** (attempt - 1), 5)
                logger.warning(
                    "[QMT实盘] 连接失败(第 %d/%d 次)，%ds 后重试: %s",
                    attempt, attempts, backoff, exc,
                )
                time.sleep(backoff)
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts:
                    raise
                backoff = min(2 ** (attempt - 1), 5)
                logger.warning(
                    "[QMT实盘] 连接异常(第 %d/%d 次)，%ds 后重试: %s",
                    attempt, attempts, backoff, exc,
                )
                time.sleep(backoff)
        if last_exc:
            raise last_exc
        raise QMTNotReady("连接失败（未知原因）")

    def _connect(self):
        """建立一次性交易会话。调用方负责 finally trader.stop()。"""
        xttrader = _get_xttrader()
        if xttrader is None:
            raise QMTNotReady("xtquant 不可用")

        from app.services.data_sources.qmt_adapter import QMTAdapter

        userdata_dir = QMTAdapter()._resolve_qmt_userdata_dir()
        if not userdata_dir:
            raise QMTNotReady("未找到 QMT userdata 目录（QMT 终端未运行？）")

        from app.core.config import get_settings

        self._account_id = get_settings().QMT_ACCOUNT_ID.strip()
        if not self._account_id:
            raise QMTNotReady("QMT_ACCOUNT_ID 未配置")

        session = int(time.time() * 1000) % 2147483647
        trader = xttrader.XtQuantTrader(userdata_dir, session)
        trader.start()
        connect_result = trader.connect()
        if connect_result != 0:
            trader.stop()
            raise QMTNotReady(f"QMT 交易客户端连接失败，返回码={connect_result}")
        return trader

    def _account(self):
        from xtquant.xttype import StockAccount

        return StockAccount(self._account_id, self._account_type)

    # ---- 委托 ----

    async def execute(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """提交真实委托。返回受理结果（非成交确认）。

        返回:
            {"status": "submitted", "broker_order_id": str}   柜台已受理
            {"status": "failed", "error": str}               柜台拒绝/通道异常
        """
        from app.core.config import get_settings

        if not self.is_ready():
            return {"status": "failed", "error": "QMT 实盘通道未就绪"}

        xt_code = _normalize_code(order["symbol"])
        if not xt_code:
            return {"status": "failed", "error": f"无法规范化代码: {order['symbol']}"}

        settings = get_settings()
        price_type = PRICE_TYPE_LATEST if settings.TRADING_QMT_ORDER_TYPE == "LATEST" else PRICE_TYPE_FIX
        # LATEST 型委托柜台按最新价挂限价；FIX 必须有有效限价
        price = float(order.get("limit_price") or 0)
        if price_type == PRICE_TYPE_FIX and price <= 0:
            return {"status": "failed", "error": "FIX 限价委托缺少限价"}

        order_type = ORDER_TYPE_BUY if order["side"] == "buy" else ORDER_TYPE_SELL
        volume = int(order["quantity"])
        # 委托备注带信号溯源，柜台流水里可反查来源
        remark = f"TA:{order.get('signal_id', '')}"[:24]

        trader = None
        try:
            # P0-3：连接阶段可安全重试（尚未发出委托，重试幂等）
            trader = self._connect_with_retry()
            account = self._account()

            # ⚠️ 关键安全判断：以下是"不可重试区"
            #
            # order_stock() 一旦被调用，柜台**可能已经受理**。此时若抛异常
            # （网络中断、客户端崩溃、超时），我们无法得知委托是否已进入
            # 柜台。盲目重试会导致**重复下单**（双倍持仓/卖出穿仓）。
            #
            # 因此：
            # - 这里绝不包裹 @retry / 循环重试
            # - 异常一律返回 status="unknown"，由对账（sync.reconcile）
            #   向柜台核实真实状态，绝不自行假设成功或失败
            order_submitted_attempted = True
            try:
                seq = trader.order_stock(
                    account, xt_code, order_type, volume, price_type,
                    price if price_type == PRICE_TYPE_FIX else 0,
                    "", remark,
                )
            except Exception as submit_exc:
                logger.error(
                    "[QMT实盘] ⚠️ 委托调用异常，委托状态未知（可能已进柜台）: "
                    "信号=%s 代码=%s 数量=%d 错误=%s。"
                    "标记 unknown，交由对账向柜台核实，绝不自动重试。",
                    order.get("signal_id"), xt_code, volume, submit_exc, exc_info=True,
                )
                return {
                    "status": "unknown",
                    "error": f"委托调用异常，状态未知需对账核实: {submit_exc}",
                    "requires_reconcile": True,
                }

            if seq == _REJECT or seq < 0:
                return {"status": "failed", "error": f"柜台拒单（返回 {seq}）："
                        f"检查资金/持仓/交易权限/委托价"}
            logger.info(
                "[QMT实盘] 委托已受理: %s %s x%d 价型%d 限价%.2f 受理号=%d 信号=%s",
                order["side"], xt_code, volume, price_type, price, seq,
                order.get("signal_id"),
            )
            return {"status": "submitted", "broker_order_id": str(seq)}
        except QMTNotReady as exc:
            return {"status": "failed", "error": str(exc)}
        except Exception as exc:
            logger.error("[QMT实盘] 委托前置异常: %s", exc, exc_info=True)
            return {"status": "failed", "error": f"委托前置异常: {exc}"}
        finally:
            if trader is not None:
                try:
                    trader.stop()
                except Exception:
                    pass

    # ---- 查询（供 sync.py 轮询成交回报）----

    async def query_orders(self, order_ids: Optional[list] = None) -> Dict[str, Any]:
        """查询当日委托列表。

        返回 {"status": "ok", "orders": [{...标准化字段}]} 或
        {"status": "failed", "error": ...}。order_ids 为 None 时返回全部，
        传入时只留匹配的（比对 broker_order_id）。
        """
        if not self.is_ready():
            return {"status": "failed", "error": "QMT 实盘通道未就绪"}

        trader = None
        try:
            trader = self._connect()
            account = self._account()
            raw_orders = trader.query_stock_orders(account) or []
            result = []
            for o in raw_orders:
                broker_id = str(getattr(o, "stock_order_id", "") or getattr(o, "m_strStockOrderID", ""))
                if order_ids is not None and broker_id not in {str(x) for x in order_ids}:
                    continue
                result.append({
                    "broker_order_id": broker_id,
                    "status_code": int(getattr(o, "order_status", -1) or -1),
                    "traded_volume": int(getattr(o, "traded_volume", 0) or 0),
                    "volume": int(getattr(o, "volume", 0) or 0),
                    "price": float(getattr(o, "price", 0) or 0),
                    "traded_price": float(getattr(o, "traded_price", 0) or 0),
                    "xt_code": str(getattr(o, "stock_code", "") or ""),
                    "order_remark": str(getattr(o, "order_remark", "") or ""),
                })
            return {"status": "ok", "orders": result}
        except QMTNotReady as exc:
            return {"status": "failed", "error": str(exc)}
        except Exception as exc:
            logger.error("[QMT实盘] 委托查询异常: %s", exc, exc_info=True)
            return {"status": "failed", "error": f"查询异常: {exc}"}
        finally:
            if trader is not None:
                try:
                    trader.stop()
                except Exception:
                    pass

    # ---- 撤单 ----

    async def cancel(self, broker_order_id: str, symbol: str, side: str) -> Dict[str, Any]:
        """撤销未成交委托。"""
        if not self.is_ready():
            return {"status": "failed", "error": "QMT 实盘通道未就绪"}

        trader = None
        try:
            trader = self._connect()
            account = self._account()
            # cancel_order_stock(account, broker_order_id)——按受理号撤
            ok = trader.cancel_order_stock(account, int(broker_order_id))
            # 返回 0 表示撤单指令已受理（结果仍需查询确认）
            if ok != 0:
                return {"status": "failed", "error": f"撤单指令被拒（返回 {ok}）"}
            return {"status": "submitted", "message": "撤单指令已受理，结果以查询为准"}
        except (QMTNotReady, ValueError) as exc:
            return {"status": "failed", "error": str(exc)}
        except Exception as exc:
            return {"status": "failed", "error": f"撤单异常: {exc}"}
        finally:
            if trader is not None:
                try:
                    trader.stop()
                except Exception:
                    pass


def validate_qmt_live_readiness() -> Dict[str, Any]:
    """live 模式启动自检：P0-5 完整校验。

    在 TRADING_MODE=live 且 LIVE_TRADING_ENABLED=true 时由启动流程调用；
    检查不过应拒绝启动交易通道（workflows 继续跑，交易闭环禁用）。

    P0-5 增强：增加账户查询验证、资金可见性验证，确保接单后立即可用。
    """
    from app.core.config import get_settings

    settings = get_settings()
    checks: Dict[str, Any] = {"mode": settings.TRADING_MODE}
    problems = []

    if not settings.LIVE_TRADING_ENABLED:
        problems.append("LIVE_TRADING_ENABLED=false")
    if settings.TRADING_MODE != "live":
        problems.append(f"TRADING_MODE={settings.TRADING_MODE}（应为 live）")
    if not settings.QMT_ACCOUNT_ID.strip():
        problems.append("QMT_ACCOUNT_ID 未配置")
    if _get_xttrader() is None:
        problems.append("xtquant 不可用（QMT 终端未安装/未运行）")

    adapter = QMTLiveAdapter()
    if not problems:
        # P0-5 增强：全量探测（连接 + 账户查询）
        trader = None
        try:
            trader = adapter._connect()
            checks["terminal_connected"] = True

            # 验证账户可查（资金/持仓）
            account = adapter._account()
            try:
                assets = trader.query_stock_asset(account)
                if assets:
                    cash = float(getattr(assets, "cash", 0) or 0)
                    checks["account_verified"] = True
                    checks["account_cash_sample"] = round(cash, 2)
                else:
                    problems.append("账户资金查询返回空（账户未登录或权限不足）")
                    checks["account_verified"] = False
            except Exception as exc:
                problems.append(f"账户查询失败: {exc}")
                checks["account_verified"] = False

            # 验证持仓可查（确保风控依赖的数据源可用）
            try:
                positions = trader.query_stock_positions(account) or []
                checks["position_query_ok"] = True
                checks["position_count"] = len(positions)
            except Exception as exc:
                problems.append(f"持仓查询失败: {exc}")
                checks["position_query_ok"] = False

        except Exception as exc:
            problems.append(f"交易终端连接失败: {exc}")
            checks["terminal_connected"] = False
            checks["account_verified"] = False
            checks["position_query_ok"] = False
        finally:
            if trader is not None:
                try:
                    trader.stop()
                except Exception:
                    pass
    else:
        checks["terminal_connected"] = False
        checks["account_verified"] = False
        checks["position_query_ok"] = False

    checks["ready"] = not problems
    checks["problems"] = problems
    return checks
