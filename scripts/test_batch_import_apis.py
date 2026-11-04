#!/usr/bin/env python
"""批量导入接口测试程序：覆盖基本信息 / 行情 / 财务 / 新闻 / 历史K线 / 社媒消息 6 类数据。

默认只打印将要发送的请求（预览），加 `--send` 才真正写入。测试数据特征：
  - 股票代码固定 999999（不是真实股票）
  - 社媒平台固定 test_import，结束后自动清理（可用 --keep 保留）
基本信息 / 行情 / 财务 / 新闻 / K线没有删除接口，写入后会留在库中（999999），
如需清理请手工删除对应集合中 symbol=999999 的文档。

用法：
    python scripts/test_batch_import_apis.py --user admin --password <密码>            # 预览
    python scripts/test_batch_import_apis.py --user admin --password <密码> --send     # 实际发送
    python scripts/test_batch_import_apis.py ... --send --only historical,social       # 只测部分
密码也可通过环境变量 TA_PASSWORD 提供。仅依赖标准库。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TEST_SYMBOL = "999999"
TEST_PLATFORM = "test_import"
ALL_KINDS = ["basic", "quotes", "financial", "news", "historical", "social"]


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def build_requests() -> dict:
    """各类数据的 (方法, 路径, 请求体)。"""
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "basic": ("POST", "/api/stock-data/save-basic-info", {
            "stocks": [{
                "symbol": TEST_SYMBOL, "name": "测试股票", "area": "测试", "industry": "测试行业",
                "list_date": "2020-01-01", "total_mv": 1.0e10, "pe": 15.0, "pb": 1.5,
            }],
            "overwrite": True,
        }),
        "quotes": ("POST", "/api/stock-data/save-quotes", {
            "quotes": [{
                "symbol": TEST_SYMBOL, "close": 10.5, "open": 10.2, "high": 10.8, "low": 10.1,
                "pre_close": 10.2, "volume": 123456, "amount": 1.3e6, "pct_chg": 2.94,
            }],
            "overwrite": True,
        }),
        "financial": ("POST", "/api/financial-data/save", {
            "symbol": TEST_SYMBOL,
            "financial_data": [{"report_period": "20250930", "revenue": 1.0e9, "net_profit": 1.0e8, "roe": 12.5}],
            "overwrite": True,
        }),
        "news": ("POST", "/api/news-data/save", {
            "symbol": TEST_SYMBOL,
            "news_list": [{
                "title": "测试新闻：批量导入接口验证",
                "url": "https://example.com/test-import/1",
                "publish_time": now_iso, "source": "test_import",
            }],
            "overwrite": True,
        }),
        "historical": ("POST", "/api/data-import/historical", {
            "symbol": TEST_SYMBOL, "market": "CN", "period": "daily", "overwrite": True,
            "records": [
                {"date": _days_ago(2), "open": 10.0, "high": 10.6, "low": 9.9, "close": 10.4, "volume": 100000, "amount": 1.0e6},
                {"date": _days_ago(1), "open": 10.4, "high": 10.9, "low": 10.3, "close": 10.8, "volume": 120000, "amount": 1.3e6},
            ],
        }),
        "social": ("POST", "/api/data-import/social-media", {
            "messages": [
                {"platform": TEST_PLATFORM, "symbol": TEST_SYMBOL, "message_id": "test-import-1",
                 "content": "测试股票业绩超预期，看好后市 #测试#", "publish_time": now_iso.replace("T", " ")[:19],
                 "author": {"author_name": "测试用户", "followers_count": 1000},
                 "engagement": {"views": 500, "likes": 20, "shares": 3, "comments": 5}},
                {"platform": TEST_PLATFORM, "symbol": TEST_SYMBOL, "message_id": "test-import-2",
                 "content": "测试股票被立案调查，利空", "publish_time": now_iso.replace("T", " ")[:19]},
            ],
            "overwrite": True,
        }),
    }


def call(base: str, method: str, path: str, token: str = "", body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:300].decode("utf-8", "replace")}
    except urllib.error.URLError as exc:
        return 0, {"error": f"无法连接 {base}: {exc.reason}"}


def summarize(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        keys = ("total", "saved", "updated", "skipped", "failed")
        return " ".join(f"{k}={data[k]}" for k in keys if k in data)
    return json.dumps(payload, ensure_ascii=False)[:200]


def main() -> int:
    ap = argparse.ArgumentParser(description="批量导入接口测试程序")
    ap.add_argument("--base", default="http://localhost:8000", help="后端地址")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default=os.environ.get("TA_PASSWORD", ""))
    ap.add_argument("--only", default="", help=f"只测指定类型，逗号分隔：{','.join(ALL_KINDS)}")
    ap.add_argument("--send", action="store_true", help="真正发送（默认只预览请求）")
    ap.add_argument("--keep", action="store_true", help="保留社媒测试消息（默认测完删除）")
    args = ap.parse_args()

    kinds = [k.strip() for k in args.only.split(",") if k.strip()] or ALL_KINDS
    unknown = [k for k in kinds if k not in ALL_KINDS]
    if unknown:
        print(f"未知类型: {unknown}，可选: {ALL_KINDS}")
        return 2

    requests_map = build_requests()
    if not args.send:
        print("【预览模式】以下请求不会发送，加 --send 才会写入。\n")
        for kind in kinds:
            method, path, body = requests_map[kind]
            print(f"[{kind}] {method} {args.base}{path}")
            print(json.dumps(body, ensure_ascii=False, indent=2))
            print()
        return 0

    if not args.password:
        print("请通过 --password 或环境变量 TA_PASSWORD 提供密码")
        return 2

    status, resp = call(args.base, "POST", "/api/auth/login", body={"username": args.user, "password": args.password})
    token = ((resp.get("data") or {}).get("access_token") or resp.get("access_token") or "") if isinstance(resp, dict) else ""
    if status != 200 or not token:
        print(f"登录失败 (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:200]}")
        return 1
    print("登录成功\n")

    failures = 0
    for kind in kinds:
        method, path, body = requests_map[kind]
        status, payload = call(args.base, method, path, token, body)
        ok = status == 200 and (payload.get("success") is not False)
        detail = summarize(payload)
        if ok and isinstance(payload.get("data"), dict) and payload["data"].get("failed"):
            ok, detail = False, detail + "  （存在校验失败的记录）"
        print(f"{'PASS' if ok else 'FAIL'}  {kind:<10} HTTP {status}  {detail}")
        failures += 0 if ok else 1

    if "social" in kinds:
        status, payload = call(args.base, "GET",
                               f"/api/data-import/social-media?symbol={TEST_SYMBOL}&platform={TEST_PLATFORM}", token)
        items = ((payload.get("data") or {}).get("items") or []) if isinstance(payload, dict) else []
        good = status == 200 and len(items) >= 2 and all(i.get("sentiment") in ("positive", "negative", "neutral") for i in items)
        print(f"{'PASS' if good else 'FAIL'}  social 回读   共 {len(items)} 条，情绪="
              f"{[i.get('sentiment') for i in items]}")
        failures += 0 if good else 1
        if not args.keep:
            removed = 0
            for item in items:
                st, _ = call(args.base, "DELETE",
                             f"/api/data-import/social-media/{item['message_id']}?platform={TEST_PLATFORM}", token)
                removed += 1 if st == 200 else 0
            print(f"INFO  已清理社媒测试消息 {removed} 条")

    print("\n" + ("全部通过" if failures == 0 else f"{failures} 项失败"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
