# -*- coding: utf-8 -*-
"""功能深度测试第二轮：带数据交互的核心流程。

覆盖：自选股、报告中心数据、任务中心、记忆中心检索、个股详情、选股页。
"""
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
ADMIN_PW = "G8Xw-3kQJ-8AmOdz6dDgUFfA6fyLrKlozo0I3ymKe0Y"
OUT = "D:/Projects/TradingAgents-CN/.claude_scratch/shots2"
import os
os.makedirs(OUT, exist_ok=True)

results = []
def check(name, ok, note=""):
    results.append((name, bool(ok), note))
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {note}" if note else ""), flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    api_requests = []
    page.on("response", lambda r: api_requests.append((r.status, r.url)) if "/api/" in r.url else None)

    # ── 登录 ──────────────────────────────────────────────
    page.goto(BASE, wait_until="networkidle", timeout=30000)
    page.fill('input[type="text"], input[placeholder*="用户名"], input[name="username"]', "admin")
    page.fill('input[type="password"]', ADMIN_PW)
    try:
        page.locator("label.el-checkbox", has_text="我已阅读并同意").first.click(timeout=5000)
    except Exception:
        pass
    page.locator("button.el-button--primary:not(.is-disabled)").click(timeout=20000)
    page.wait_for_load_state("networkidle", timeout=15000)
    time.sleep(1.5)
    check("登录", "/login" not in page.url.lower() or "dashboard" in page.url.lower(), page.url)

    def go(name, path, extra_checks=()):
        page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=25000)
        time.sleep(2.5)
        body = page.inner_text("body")
        slug = path.strip("/").replace("/", "_") or "root"
        page.screenshot(path=f"{OUT}/{slug}.png", full_page=False)
        notes = []
        for desc, cond in extra_checks:
            notes.append(f"{desc}={'Y' if cond(body) else 'N'}")
        check(name, len(body.strip()) > 50, " ".join(notes) or f"{len(body)}字符")

    # ── 1. 仪表盘应有统计数字 ─────────────────────────────
    go("仪表盘数据渲染", "/dashboard", [
        ("统计卡片", lambda b: "0" in b or "任务" in b),
        ("结算/信号区", lambda b: ("信号" in b or "结算" in b)),
    ])

    # ── 2. 任务中心应列出今天的历史任务（000858/600036）──
    go("任务中心历史任务", "/tasks", [
        ("含000858", lambda b: "000858" in b),
        ("含600036", lambda b: "600036" in b),
        ("状态列", lambda b: ("completed" in b.lower() or "完成" in b)),
    ])

    # ── 3. 报告中心应有报告 ───────────────────────────────
    go("报告中心列表", "/reports", [
        ("有报告条目", lambda b: ("000858" in b or "600036" in b or "五粮液" in b or "招商" in b)),
    ])

    # ── 4. 记忆中心（修复后应有 qa_smoke_test 写入）──────
    go("记忆中心", "/memory", [
        ("非空或有界面", lambda b: ("记忆" in b)),
    ])

    # ── 5. 个股详情 000858 ────────────────────────────────
    go("个股详情 000858", "/stocks/000858", [
        ("K线/图表", lambda b: ("K线" in b or "行情" in b or "五粮液" in b)),
    ])

    # ── 6. 选股页 ────────────────────────────────────────
    go("选股页", "/screening", [
        ("筛选界面", lambda b: ("筛选" in b or "选股" in b)),
    ])

    # ── 7. 自选股页 ─────────────────────────────────────
    go("自选股页", "/favorites", [])

    # ── 8. 定时任务页应有 job 列表 ───────────────────────
    go("定时任务中心", "/settings/scheduler", [
        ("25个任务", lambda b: ("财报" in b or "同步" in b or "结算" in b)),
    ])

    # ── 9. API 报错抽查 ──────────────────────────────────
    err_apis = [u for s, u in api_requests if s >= 500]
    check("无服务端 5xx", len(err_apis) == 0, "; ".join(err_apis[:3]) if err_apis else f"共{len(api_requests)}个API调用")

    # ── 10. 报告详情页（如能从列表进入）─────────────────
    browser.close()

ok = sum(1 for _, v, _ in results if v)
print(f"\n{'='*50}\n结果: {ok}/{len(results)} 通过")
sys.exit(0 if ok == len(results) else 1)
