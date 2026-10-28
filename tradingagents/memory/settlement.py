"""结算过去的结论：持有窗口过后，对照基准计算收益并写入决策日志的反思。

参考上游 TauricResearch/TradingAgents v0.7.1 的 memory/settlement.py。
价格来源适配本项目：A 股优先 MongoDB 缓存，兜底在线 provider；
基准（沪深300）经 tushare provider 的 get_index_daily 获取。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from tradingagents.utils.stock_utils import StockMarket, StockUtils

logger = logging.getLogger(__name__)

# A 股基准：默认沪深300；创业板/科创板用对应指数
_CN_BENCHMARKS = {
    "300": ("399006.SZ", "创业板指"),
    "301": ("399006.SZ", "创业板指"),
    "688": ("000688.SH", "科创50"),
}
_DEFAULT_BENCHMARK = ("000300.SH", "沪深300")

# 常见宽基 ETF → 跟踪指数（结算用跟踪指数作基准最直观）
_ETF_TRACKING_INDEXES = {
    "510300": ("000300.SH", "沪深300"),
    "510050": ("000016.SH", "上证50"),
    "510500": ("000905.SH", "中证500"),
    "159919": ("000300.SH", "沪深300"),
    "159915": ("399006.SZ", "创业板指"),
    "159949": ("399987.SZ", "中证1000"),
}


def resolve_benchmark(ticker: str, config: dict = None) -> tuple[str, str]:
    """返回结算基准的 (代码, 名称)。A股/ETF 按板块选指数；其他市场暂不支持结算。"""
    cfg = config or {}
    explicit = cfg.get("benchmark_ticker")
    if explicit:
        return explicit, cfg.get("benchmark_name", explicit)
    market = StockUtils.identify_stock_market(ticker)
    if market != StockMarket.CHINA_A:
        # 港股/美股暂无本地基准行情通道，返回空让调用方跳过结算
        return "", ""
    code6 = str(ticker).split(".")[0].zfill(6)
    if _is_cn_etf(code6):
        # 宽基/行业 ETF 用其跟踪指数结算最直观；未映射的按普通规则走
        etf_bench = _ETF_TRACKING_INDEXES.get(code6)
        if etf_bench:
            return etf_bench
    bench = _CN_BENCHMARKS.get(code6[:3], _DEFAULT_BENCHMARK)
    return bench


# ETF/基金代码前缀（上交所 50/51/52/56/58，深交所 15/16/18），
# 与 app/services/task_analysis_service.ETF_CODE_PREFIXES 保持一致
_ETF_CODE_PREFIXES = ("50", "51", "52", "56", "58", "15", "16", "18")


def _is_cn_etf(ticker: str) -> bool:
    code6 = str(ticker).split(".")[0].zfill(6)
    return len(code6) == 6 and code6.isdigit() and code6.startswith(_ETF_CODE_PREFIXES)


def _fetch_stock_closes(ticker: str, start_date: str, end_date: str) -> Optional[pd.Series]:
    """取 A 股日线收盘价（含 ETF）：本地 MongoDB 优先，兜底在线 provider。失败抛异常。

    复权感知：MongoDB 的历史行情来自多条同步通道（tushare/akshare 走前复权，
    baostock/qmt 可能是不复权原始价），close 序列本身无法保证复权口径一致。
    因此结算收益优先用逐日 pct_chg 链乘（涨跌幅与复权方式无关，除权日由
    交易所按前收盘价折算），仅当记录缺失 pct_chg 时才退回 close 比值——
    后者在持有窗口跨除权除息日时会把分红错算成亏损。
    """
    code6 = str(ticker).split(".")[0].zfill(6)

    from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter

    adapter = get_mongodb_cache_adapter()
    df = adapter.get_historical_data(code6, start_date, end_date, period="daily")
    if df is None or df.empty:
        # MongoDB 无数据，走在线 provider（同步包装异步接口）
        df = _fetch_closes_from_provider(code6, start_date, end_date)
    if df is None or df.empty:
        raise RuntimeError(f"无法获取 {ticker} 的日线数据")

    date_col = "trade_date" if "trade_date" in df.columns else "date"
    dates = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")
    closes = pd.Series(
        pd.to_numeric(df["close"], errors="coerce").values,
        index=dates,
    )
    pct_col = None
    for candidate in ("pct_chg", "change_percent", "pctChg"):
        if candidate in df.columns:
            pct_col = candidate
            break
    pcts = None
    if pct_col is not None:
        pcts = pd.to_numeric(df[pct_col], errors="coerce")
        pcts = pd.Series(pcts.values, index=dates)

    closes = closes[closes > 0].sort_index()
    if closes.empty:
        raise RuntimeError(f"{ticker} 无有效收盘价")
    closes = closes[~closes.index.duplicated(keep="last")]
    if pcts is not None:
        pcts = pcts.loc[closes.index]
    return closes, pcts


def _fetch_closes_from_provider(code6: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """在线 provider 兜底（tushare → akshare），同步阻塞调用。"""
    import asyncio

    from tradingagents.dataflows.providers.china.tushare import get_tushare_provider

    provider = get_tushare_provider()
    if provider and provider.is_available():
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    provider.get_historical_data(code6, start_date, end_date, "daily")
                )
            finally:
                loop.close()
        except Exception as exc:
            logger.warning("tushare 获取 %s 日线失败: %s", code6, exc)

    from tradingagents.dataflows.providers.china.akshare import get_akshare_provider

    ak = get_akshare_provider()
    if ak:
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    ak.get_historical_data(code6, start_date, end_date, "daily")
                )
            finally:
                loop.close()
        except Exception as exc:
            logger.warning("akshare 获取 %s 日线失败: %s", code6, exc)
    return None


def _fetch_benchmark_closes(benchmark: str, start_date: str, end_date: str) -> Optional[pd.Series]:
    """取基准指数日线收盘。失败抛异常。"""
    import asyncio

    from tradingagents.dataflows.providers.china.tushare import get_tushare_provider

    provider = get_tushare_provider()
    if not provider or not provider.is_available():
        raise RuntimeError("tushare 不可用，无法获取基准指数行情")

    start_str = start_date.replace("-", "")
    end_str = end_date.replace("-", "")
    loop = asyncio.new_event_loop()
    try:
        df = loop.run_until_complete(
            provider.get_index_daily(ts_code=benchmark, start_date=start_str, end_date=end_str)
        )
    finally:
        loop.close()
    if df is None or df.empty:
        raise RuntimeError(f"基准指数 {benchmark} 无行情数据")

    closes = pd.Series(
        pd.to_numeric(df["close"], errors="coerce").values,
        index=pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d"),
    )
    return closes[closes > 0].sort_index()


def fetch_returns(
    ticker: str,
    trade_date: str,
    holding_days: int = 5,
    benchmark: str = "000300.SH",
) -> tuple[Optional[float], Optional[float], Optional[int], Optional[str]]:
    """取结论日 ``holding_days`` 个交易日后的原始收益与相对基准超额收益。

    建仓时点为 ``trade_date`` 之后第一个收盘（上游 #1518 语义：结论在
    T 日读到的新闻可能发表于 T 日收盘之后）。返回
    ``(raw_return, alpha_return, holding_days, resolution_date)``；
    持有窗口尚未完整走出时返回 ``(None, None, None, None)``，留待下次结算。
    """
    start = datetime.strptime(trade_date, "%Y-%m-%d")
    today = datetime.combine(datetime.now().date(), datetime.min.time())

    # 窗口完整性预检查：入场日 + holding_days 个交易日 + 节假日冗余
    if (today - start).days < holding_days:
        return None, None, None, None
    # holding_days 个交易日约横跨 7/5 个日历日，再加一周节假日冗余
    end = start + timedelta(days=round((holding_days + 1) * 7 / 5) + 7)
    # 从一周前开始取，保证基准在入场日或之前有收盘
    bench_start = (start - timedelta(days=7)).strftime("%Y-%m-%d")
    fetch_end = min(end, today + timedelta(days=1)).strftime("%Y-%m-%d")
    fetch_start = (start + timedelta(days=1)).strftime("%Y-%m-%d")

    # ETF：MongoDB 的 ETF 行情在独立集合（etf_daily_quotes），adapter 内部
    # 已按代码前缀自动分流；跟踪指数作基准（如无映射则与个股同样用沪深300）
    stock, stock_pcts = _fetch_stock_closes(ticker, fetch_start, fetch_end)
    bench = _fetch_benchmark_closes(benchmark, bench_start, fetch_end)

    # 要求完整持有窗口已走出：个股须有入场后 holding_days+1 个收盘
    if len(stock) <= holding_days:
        return None, None, None, None
    entry_date = stock.index[0]
    exit_date = stock.index[holding_days]

    # 基准须覆盖入场与出场两端
    if bench.empty or bench.index[0] > entry_date or bench.index[-1] < exit_date:
        return None, None, None, None

    # 收益计算：持有窗口内逐日 pct_chg 链乘（复权无关，跨除权除息日仍准确）；
    # pct_chg 缺失时退回 close 比值（口径受复权影响，已在取数处说明）
    window_pcts = None
    if stock_pcts is not None:
        window = stock_pcts.iloc[1 : holding_days + 1]
        if window.notna().all() and len(window) == holding_days:
            window_pcts = window
    if window_pcts is not None:
        raw = float((1.0 + window_pcts / 100.0).prod() - 1.0)
    else:
        raw = float((stock.iloc[holding_days] - stock.iloc[0]) / stock.iloc[0])
    # asof 需要时间戳索引；本模块的索引为 "YYYY-MM-DD" 字符串，
    # 排序后按位置查找 <= 目标日期的最后一个收盘即可。
    def _close_asof(series: pd.Series, date: str) -> float:
        pos = series.index.searchsorted(date, side="right") - 1
        if pos < 0:
            raise RuntimeError(f"基准在 {date} 前无收盘")
        return float(series.iloc[pos])

    bench_entry = _close_asof(bench, entry_date)
    bench_exit = _close_asof(bench, exit_date)
    bench_ret = float((bench_exit - bench_entry) / bench_entry)
    alpha = raw - bench_ret
    # 所有用到的收盘在出场日已知：point-in-time 截止
    return raw, alpha, holding_days, exit_date


@dataclass
class Settlement:
    """一次结算流程的结果：已结算与未能结算的结论。"""

    settled: list[tuple[str, str]] = field(default_factory=list)      # (ticker, date)
    failed: list[tuple[str, str, str]] = field(default_factory=list)  # (ticker, date, reason)


def settle_pending(
    ticker: str,
    memory_log,
    reflector,
    config: dict = None,
    wait: bool = True,
) -> Settlement:
    """结算 ``ticker`` 的、持有窗口已完整走出的 pending 结论。

    每份结果在反思生成后立刻写入，中断的流程保留已完成的部分。
    窗口未走出的条目跳过；价格或反思失败的条目保持 pending 并记录原因。
    ``wait=False`` 时若他处已有结算流程在跑则直接返回。
    """
    result = Settlement()
    cfg = config or {}
    with memory_log.settling(wait=wait) as held:
        if not held:
            return result
        pending = [e for e in memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return result
        benchmark, benchmark_name = resolve_benchmark(ticker, cfg)
        if not benchmark:
            for entry in pending:
                result.failed.append((ticker, entry["date"], "该市场暂不支持自动结算"))
            return result
        for entry in pending:
            if entry.get("rating") == "待复核":
                # 读不出倾向的结论无方向可评分：跳过结算，保持待复核状态
                continue
            try:
                raw, alpha, days, resolution_date = fetch_returns(
                    ticker,
                    entry["date"],
                    cfg.get("holding_period_days", 5),
                    benchmark=benchmark,
                )
            except Exception as exc:
                logger.warning("结算 %s %s 时行情不可用: %s", ticker, entry["date"], exc)
                result.failed.append((ticker, entry["date"], f"行情不可用: {exc}"))
                continue
            if raw is None:
                continue  # 持有窗口未完整走出：下次分析时重试
            try:
                if reflector is not None:
                    reflection = reflector.reflect_on_final_decision(
                        final_decision=entry.get("decision", ""),
                        raw_return=raw,
                        alpha_return=alpha,
                        benchmark_name=benchmark_name,
                        holding_days=days,
                    )
                else:
                    # 没有 LLM（如配置缺失）时也要能结算：反思退化为数据摘要
                    reflection = (
                        f"{holding_days_str(days)}内原始收益 {raw:+.1%}，"
                        f"相对{benchmark_name}超额 {alpha:+.1%}（无反思模型，仅记录结果）。"
                    )
            except Exception as exc:
                # 反思调用 LLM：瞬时失败保持条目 pending，供下次流程重试
                logger.warning("%s %s 反思失败: %s", ticker, entry["date"], exc)
                result.failed.append((ticker, entry["date"], f"反思失败: {exc}"))
                continue
            if memory_log.update_with_outcome(
                ticker, entry["date"], raw, alpha, days, reflection,
                resolution_date=resolution_date,
            ):
                result.settled.append((ticker, entry["date"]))
    return result


def holding_days_str(days: Optional[int]) -> str:
    return f"{days} 个交易日" if days else "持有窗口"


def settle_all_pending(memory_log, reflector, config: dict = None, wait: bool = True) -> Settlement:
    """结算所有 ticker 的到期 pending 结论（不再被分析的标的也能出教训）。"""
    result = Settlement()
    for ticker in dict.fromkeys(e["ticker"] for e in memory_log.get_pending_entries()):
        done = settle_pending(ticker, memory_log, reflector, config, wait=wait)
        result.settled.extend(done.settled)
        result.failed.extend(done.failed)
    return result
