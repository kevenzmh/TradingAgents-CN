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

    def is_ready(self) -> bool:
        """通道就绪检查：xtquant 可用 + 账号已配置 + 终端可连接。

        不做真实连接探测（几百毫秒级开销），连接失败留给委托时显式报错；
        开机自检（validate_startup）才做全量探测。
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
        return True

    # ---- 连接 ----

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
            trader = self._connect()
            account = self._account()
            seq = trader.order_stock(
                account, xt_code, order_type, volume, price_type,
                price if price_type == PRICE_TYPE_FIX else 0,
                "", remark,
            )
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
            logger.error("[QMT实盘] 委托异常: %s", exc, exc_info=True)
            return {"status": "failed", "error": f"委托异常: {exc}"}
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
    """live 模式启动自检：配置完整性 + 终端连通性一次性探测。

    在 TRADING_MODE=live 且 LIVE_TRADING_ENABLED=true 时由启动流程调用；
    检查不过应拒绝启动交易通道（workflows 继续跑，交易闭环禁用）。
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
        # 全量探测：真实连接一次交易终端
        try:
            trader = adapter._connect()
            trader.stop()
            checks["terminal_connected"] = True
        except Exception as exc:
            problems.append(f"交易终端连接失败: {exc}")
            checks["terminal_connected"] = False
    else:
        checks["terminal_connected"] = False

    checks["ready"] = not problems
    checks["problems"] = problems
    return checks
