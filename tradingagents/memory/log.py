"""决策日志：每次研究结论按原样追加记录，结算后写入结果与反思。

参考上游 TauricResearch/TradingAgents v0.7.1 的 tradingagents/memory/log.py，
标签中的结论倾向使用本项目合规词（乐观/审慎/中性）。
"""

import re
from pathlib import Path

from tradingagents.memory.file_locks import locked, replace_file

# HTML 注释不可能出现在 LLM 的散文输出中，可安全作为硬分隔符
_SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"

_DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
_REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)

# 结论倾向 → 结算评分时的方向声明（乐观=看多，审慎=看空，中性=无方向）
RATING_DIRECTION = {"乐观": 1, "审慎": -1, "中性": 0}
# 读不出倾向的结论记为待复核：不参与评分，但保留解析失败的可视 traces
RATING_REVIEW = "待复核"


def parse_rating(final_trade_decision) -> str:
    """从研究结论文本中解析结论倾向；读不出时返回空字符串（调用方应跳过记录）。"""
    if not isinstance(final_trade_decision, str) or not final_trade_decision.strip():
        return ""
    text = final_trade_decision[:4000]
    if "乐观" in text:
        return "乐观"
    if "审慎" in text:
        return "审慎"
    if "中性" in text:
        return "中性"
    # 兼容英文/交易术语（旧数据或 LLM 偶发输出）
    lowered = text.lower()
    if "buy" in lowered or "bullish" in lowered or "买入" in text:
        return "乐观"
    if "sell" in lowered or "bearish" in lowered or "卖出" in text:
        return "审慎"
    return "中性" if ("hold" in lowered or "持有" in text or "观望" in text) else ""


class TradingMemoryLog:
    """追加式 Markdown 决策与反思日志。"""

    _SEPARATOR = _SEPARATOR
    _DECISION_RE = _DECISION_RE
    _REFLECTION_RE = _REFLECTION_RE

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._log_path = None
        path = cfg.get("decision_log_path") or cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # 已结算条数的可选上限；None 表示不轮转
        self._max_entries = cfg.get("decision_log_max_entries") or cfg.get("memory_log_max_entries")

    # --- 写入：一次分析记录其结论 ---

    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
        rating: str | None = None,
    ) -> None:
        """在分析结束时追加 pending 条目。不做任何 LLM 调用。

        ``rating`` 为调用方已解析出的结论倾向；没有时从结论文本解析。
        同一 (日期, 代码) 已有条目则跳过（重跑不重复计数）。
        """
        if not self._log_path:
            return
        rating = rating or parse_rating(final_trade_decision)
        if not rating:
            # 读不出倾向的结论记为待复核（上游 REVIEW 模式）：不参与评分，
            # 但保留在日志里——解析失败率是提示词质量的可观测指标
            rating = RATING_REVIEW
        with locked(self._log_path):
            if self._log_path.exists():
                raw = self._log_path.read_text(encoding="utf-8")
                for line in raw.splitlines():
                    if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("]"):
                        return
            tag = f"[{trade_date} | {ticker} | {rating} | pending]"
            entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(entry)

    def settling(self, wait: bool = True):
        """一次只允许一个结算流程持有日志（跨线程与跨进程）。

        两个流程同时结算会为同一条反思各付一次 LLM 费用。与写入用的
        是不同的锁。yield 是否拿到锁；``wait=False`` 时不等待他人。
        """
        if not self._log_path:
            import contextlib

            return contextlib.nullcontext(True)
        return locked(f"{self._log_path}.settle", wait=wait)

    # --- 读取 ---

    def load_entries(self) -> list[dict]:
        """解析日志中的全部条目。"""
        if not self._log_path or not self._log_path.exists():
            return []
        text = self._log_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
        entries = []
        for raw in raw_entries:
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def get_pending_entries(self) -> list[dict]:
        """返回所有 outcome 为 pending 的条目，供结算。"""
        return [e for e in self.load_entries() if e.get("pending")]

    def get_past_context(
        self, ticker: str, n_same: int = 5, n_cross: int = 3, as_of: str | None = None
    ) -> str:
        """格式化过去已结算条目，供注入后续分析提示词。

        ``as_of``（yyyy-mm-dd）给定时，只包含 outcome 在该日期前已知的
        教训（条目须带 ``resolved:`` 日期）——历史/回测运行不能学习还没
        发生的结果。``as_of=None`` 关闭过滤。
        """
        entries = [
            e for e in self.load_entries()
            if not e.get("pending") and e.get("rating") != RATING_REVIEW
        ]
        if as_of is not None:
            entries = [e for e in entries if e.get("resolved") and e["resolved"] <= as_of]
        if not entries:
            return ""

        same, cross = [], []
        # 按决策日期最新优先；同日期时按后写入者优先（回填的旧决策写在后面）
        newest = sorted(range(len(entries)), key=lambda i: (entries[i]["date"], i), reverse=True)
        for e in (entries[i] for i in newest):
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross:
            return ""

        parts = []
        if same:
            parts.append(f"{ticker} 的历史研究结论（最新在前，含结算后的实际表现与教训）：")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("其他标的的近期教训：")
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)

    # --- 结算：记录结论的实际结果与反思 ---

    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
        resolution_date: str | None = None,
    ) -> bool:
        """把 pending 标签替换为结果标签并追加 REFLECTION 段落（原子写）。

        返回是否更新了条目：False 表示该结论已不再是 pending
        （另一个结算流程先处理了它）。
        """
        if not self._log_path or not self._log_path.exists():
            return False
        with locked(self._log_path):
            text = self._log_path.read_text(encoding="utf-8")
            blocks = text.split(self._SEPARATOR)

            pending_prefix = f"[{trade_date} | {ticker} |"
            raw_pct = f"{raw_return:+.1%}"
            alpha_pct = f"{alpha_return:+.1%}"

            updated = False
            new_blocks = []
            for block in blocks:
                stripped = block.strip()
                if not stripped:
                    new_blocks.append(block)
                    continue

                lines = stripped.splitlines()
                tag_line = lines[0].strip()

                if (
                    not updated
                    and tag_line.startswith(pending_prefix)
                    and tag_line.endswith("| pending]")
                ):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    new_tag = self._resolved_tag(
                        trade_date, ticker, rating, raw_pct, alpha_pct, holding_days, resolution_date
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}"
                    )
                    updated = True
                else:
                    new_blocks.append(block)

            if not updated:
                return False

            new_blocks = self._apply_rotation(new_blocks)
            new_text = self._SEPARATOR.join(new_blocks)
            replace_file(self._log_path, lambda p: Path(p).write_text(new_text, encoding="utf-8"))
            return True

    # --- 内部辅助 ---

    @staticmethod
    def _resolved_tag(
        trade_date, ticker, rating, raw_pct, alpha_pct, holding_days, resolution_date
    ) -> str:
        """已结算条目标签，记录结果变为已知的日期（point-in-time 截止点）。"""
        tag = f"[{trade_date} | {ticker} | {rating} | {raw_pct} | {alpha_pct} | {holding_days}d"
        if resolution_date:
            tag += f" | resolved:{resolution_date}"
        return tag + "]"

    def _apply_rotation(self, blocks: list[str]) -> list[str]:
        """已结算条数超过上限时丢弃最旧的已结算条目；pending 永远保留。"""
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False, ""))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved, tag_line[1:].split("|")[0].strip()))

        resolved_count = sum(1 for _, r, _ in decisions if r)
        if resolved_count <= self._max_entries:
            return blocks

        resolved = [i for i, (_, is_resolved, _) in enumerate(decisions) if is_resolved]
        dated = sorted(resolved, key=lambda i: (decisions[i][2], i))
        dropped = set(dated[: resolved_count - self._max_entries])
        return [block for i, (block, _, _) in enumerate(decisions) if i not in dropped]

    def _parse_entry(self, raw: str) -> dict | None:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None
        resolved = None
        for f in fields[6:]:
            if f.startswith("resolved:"):
                resolved = f[len("resolved:"):].strip()
        entry = {
            "date": fields[0],
            "ticker": fields[1],
            "rating": fields[2],
            "pending": fields[3] == "pending",
            "raw": fields[3] if fields[3] != "pending" else None,
            "alpha": fields[4] if len(fields) > 4 else None,
            "holding": fields[5] if len(fields) > 5 else None,
            "resolved": resolved,
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""
        return entry

    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"
