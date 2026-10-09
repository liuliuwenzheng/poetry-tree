#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动诗文树 API 服务。

    python -m api.serve                          # 默认 127.0.0.1:8710
    python -m api.serve --port 9000
    python -m api.serve --db /path/to/other.db   # 换库（别的数据源适配器）

浏览器打开 <http://127.0.0.1:8710/docs> 看 REST 文档，
<http://127.0.0.1:8710/graphql> 是 GraphiQL 交互页面。
"""
import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 允许 `python api/serve.py` 直接跑（不只是 -m api.serve）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.config import DEFAULT_DB, Settings   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="诗文树 Poetry Tree · 开放接口")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite 库路径（只读打开）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只绑本机；要让别人访问用 0.0.0.0")
    ap.add_argument("--port", type=int, default=8710)
    ap.add_argument("--sources", default=None, help="来源登记表路径（默认 licenses/sources.json）")
    ap.add_argument("--page-size", type=int, default=20)
    ap.add_argument("--page-max", type=int, default=200)
    ap.add_argument("--no-strict-rights", action="store_true",
                    help="跳过启动时的授权审计（明知故犯；不建议）")
    ap.add_argument("--debug", action="store_true", help="错误回传堆栈")
    ap.add_argument("--reload", action="store_true", help="改代码自动重启（开发用）")
    a = ap.parse_args()

    kw = {"db": a.db, "host": a.host, "port": a.port,
          "page_default": a.page_size, "page_max": a.page_max, "debug": a.debug}
    if a.sources:
        kw["sources_json"] = a.sources
    settings = Settings(**kw)

    os.chdir(_ROOT)                     # 让 uvicorn --reload 能盯到 api/ 和 corpus/
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

    import uvicorn
    from api.app import create_app

    if a.reload:
        # reload 需要 import 字符串，才能重新导入模块
        os.environ["POETRY_TREE_SETTINGS"] = repr(kw)
        uvicorn.run("api.serve:_app_for_reload", host=settings.host,
                    port=settings.port, reload=True, reload_dirs=[_ROOT])
    else:
        app = create_app(settings, auto_audit=not a.no_strict_rights)
        print(f"\n  REST 文档   http://{settings.host}:{settings.port}/docs\n"
              f"  GraphiQL    http://{settings.host}:{settings.port}/graphql\n"
              f"  接口入口    http://{settings.host}:{settings.port}/api/v1/\n",
              flush=True)
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


def _app_for_reload():
    """给 uvicorn --reload 用的工厂（reload 模式必须用 import 字符串）。"""
    import ast
    from api.app import create_app
    kw = ast.literal_eval(os.environ.get("POETRY_TREE_SETTINGS", "{}"))
    return create_app(Settings(**kw))


if __name__ == "__main__":
    main()
