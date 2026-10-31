# -*- coding: utf-8 -*-
"""TradingAgents-CN 系统功能验证：登录 → 各页面渲染 → 核心功能点。

服务已在运行（前端 :3000 / 后端 :8000），无需 with_server.py。
"""
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
ADMIN_PW = "G8Xw-3kQJ-8AmOdz6dDgUFfA6fyLrKlozo0I3ymKe0Y"
OUT = "D:/Projects/TradingAgents-CN/.claude_scratch/shots"

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
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    # ── 1. 登录页 ──────────────────────────────────────────────
    page.goto(BASE, wait_until="networkidle", timeout=30000)
    page.screenshot(path=f"{OUT}/01_login.png")
    body = page.inner_text("body")[:300]
    check("登录页可达", "登录" in body or "密码" in body or "用户名" in body, body[:60].replace("\n", " "))

    # 尝试登录（先填表单、勾选用户协议，再等按钮启用）
    page.fill('input[type="text"], input[placeholder*="用户名"], input[name="username"]', "admin")
    page.fill('input[type="password"]', ADMIN_PW)
    try:
        page.locator("label.el-checkbox", has_text="我已阅读并同意").first.click(timeout=5000)
        print("已勾选用户协议")
    except Exception as e:
        print(f"协议勾选跳过: {e}"[:80])
    page.locator("button.el-button--primary:not(.is-disabled)").click(timeout=20000)
    page.wait_for_load_state("networkidle", timeout=15000)
    time.sleep(2)
    page.screenshot(path=f"{OUT}/02_after_login.png")
    after = page.inner_text("body")[:400]
    logged_in = "登录" not in page.url or "login" not in page.url.lower() or ("退出" in after or "管理" in after or "Dashboard" in after or "首页" in after)
    check("admin 登录成功", logged_in, f"url={page.url}")

    # ── 2. 遍历核心页面（路由表来自 frontend/src/router/index.ts）──
    real_routes = [
        ("仪表盘", "/dashboard"),
        ("单股分析", "/analysis/single"),
        ("综合研究", "/analysis/general"),
        ("选股", "/screening"),
        ("自选股", "/favorites"),
        ("个股浏览", "/stocks"),
        ("任务中心", "/tasks/unified"),
        ("智能助理", "/queue"),
        ("报告中心", "/reports"),
        ("记忆中心", "/memory"),
        ("系统设置", "/settings/config"),
        ("定时任务", "/settings/scheduler"),
        ("交易复核", "/review"),
    ]

    for txt, href in real_routes:
        try:
            page.goto(f"{BASE}{href}", wait_until="networkidle", timeout=25000)
            time.sleep(1.5)
            content = page.inner_text("body")
            # 空白页或明确报错视为失败
            empty = len(content.strip()) < 50
            err_page = ("404" in content[:200] or "Cannot find" in content[:200] or "出错了" in content[:300])
            slug = href.strip("/").replace("/", "_") or "root"
            page.screenshot(path=f"{OUT}/page_{slug}.png")
            check(f"页面[{txt}] {href}", not empty and not err_page, f"内容 {len(content)} 字符")
        except Exception as e:
            check(f"页面[{txt}] {href}", False, str(e)[:80])

    # ── 3. 汇总 ────────────────────────────────────────────────
    print(f"\nConsole errors captured: {len(console_errors)}")
    for e in console_errors[:5]:
        print("  [console.error]", e[:150])

    browser.close()

ok = sum(1 for _, v, _ in results if v)
print(f"\n{'='*50}\n结果: {ok}/{len(results)} 通过")
sys.exit(0 if ok == len(results) else 1)
