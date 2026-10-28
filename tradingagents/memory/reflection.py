"""反思：把已结算结论的实际结果转成后续分析可复用的简短教训。

参考上游 TradingAgents v0.7 的 memory/reflection.py（精简为 2-4 句散文）。
"""

from typing import Any

from tradingagents.utils.logging_init import get_logger

logger = get_logger("memory.reflection")


class Reflector:
    """对已结算的决策做反思。"""

    def __init__(self, llm: Any):
        """llm 为可调用 ``invoke([("system",...),("human",...)]) -> .content`` 的实例。"""
        self.llm = llm

    def _system_prompt(self, holding_days: int) -> str:
        return (
            "你是一位复盘自己此前研究结论的分析师，现在该结论的实际结果已经出来。\n"
            f"结果覆盖的是从分析日期后第一个收盘算起的 {holding_days} 个交易日，"
            "可能短于结论原本针对的时间跨度。\n"
            "输出恰好 2-4 句纯文本散文（不要列表、不要标题、不要 markdown）。\n\n"
            "按顺序覆盖：\n"
            f"1. {holding_days} 日的超额收益（引用具体数字）说明了方向判断的对错；"
            "若窗口太短不足以检验该结论，请直说。\n"
            "2. 该窗口支持或削弱了原论证中的哪一部分。\n"
            "3. 一条可应用到下次同类分析的具体教训。\n\n"
            "具体、简洁。你的输出会被原样存入决策日志并被后续分析读取，"
            "每个字都要有必要性。不要给出任何买卖建议。"
        )

    def reflect_on_final_decision(
        self,
        final_decision: str,
        raw_return: float,
        alpha_return: float,
        benchmark_name: str = "沪深300",
        holding_days: int = 5,
    ) -> str:
        """结算 pending 结论时的一次反思调用。

        ``final_trade_decision`` 已综合了全部分析师洞察，无需额外市场上下文。
        """
        messages = [
            ("system", self._system_prompt(holding_days)),
            (
                "human",
                (
                    f"{holding_days} 个交易日的原始收益率: {raw_return:+.1%}\n"
                    f"相对 {benchmark_name} 的超额收益: {alpha_return:+.1%}\n\n"
                    f"研究结论原文:\n{final_decision}"
                ),
            ),
        ]
        response = self.llm.invoke(messages)
        content = getattr(response, "content", None)
        if content is None:
            content = str(response)
        return str(content).strip()
