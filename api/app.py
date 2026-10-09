# -*- coding: utf-8 -*-
"""应用组装 —— 把存储、授权、REST、GraphQL 装到一起。

**启动即审计**：库里的每一个来源都必须在 `licenses/sources.json` 登记且已核实，
否则进程直接退出。这是把「授权不明 = 不收」这条第一原则从构建期延伸到服务期 ——
不能构建时严格、服务时宽松，那等于原则没落地。

也顺手统一了错误结构，REST 和 GraphQL 的报错长得一样：

    {"error": {"code": "bad_request", "message": "...", "hint": "..."}}
"""
import os
import sys

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.config import Settings
from api.graphql_api import make_graphql_router
from api.rest import make_router
from api.rights import RightsRegistry
from api.storage import SqliteStore

DESCRIPTION = """
**诗文树 Poetry Tree** —— 授权优先的诗歌语料库开放接口。

> 让诗的美更容易被遇见——同时不侵害任何作者与版权所有者的利益。

## 第一原则

**只收能够确认授权的材料。授权不明 = 不收。**

这不是文档里的一句口号：库里的每个来源都必须在
[`licenses/sources.json`](https://github.com/liuliuwenzheng/poetry-tree/blob/main/licenses/sources.json)
登记且 `license_checked=true`，否则**服务拒绝启动**。

## 三个正交的轴

| 轴 | 问题 | 例 |
|---|---|---|
| `dynasty` | 何时 | 唐 / 宋 / 清 |
| `genre` | 何种文体 | 诗 / 词 / 曲 |
| `form` | 什么格律 | 五言绝句 / 七言律诗 |

再加上 `tune`（词牌）。**朝代与体裁是正交的** ——
「唐诗」是 唐×诗 的组合，不是一种体裁；「宋词」是 宋×词。
所以「所有词，不分朝代」是一行 `?genre=词`，不需要硬编码任何 id 列表。
"""


def _err(code: str, message: str, hint: str | None = None, status: int = 400):
    body = {"code": code, "message": message}
    if hint:
        body["hint"] = hint
    return JSONResponse(status_code=status, content={"error": body})


def create_app(settings: Settings | None = None, *, auto_audit: bool = True) -> FastAPI:
    settings = settings or Settings()

    if not os.path.exists(settings.db):
        sys.exit(f"找不到数据库：{settings.db}\n"
                 f"用 --db 指定；百川库可从 github.com/liuliuwenzheng/baichuan-poetry "
                 f"的 Releases 下载。")

    registry = RightsRegistry(settings.sources_json)
    store = SqliteStore(settings, registry)

    # ---------------------------------------------------------- 启动审计
    problems = registry.audit(store.db_sources())
    banner = (f"诗文树 API 启动\n"
              f"  库：{settings.db}\n"
              f"  来源：{', '.join(sorted(store.db_sources())) or '（无）'}\n"
              f"  能力：体裁轴={store.caps.genres} 词牌={store.caps.tune} "
              f"全文检索={sorted(store.caps.fts)} 别名={store.caps.aliases}")
    if problems:
        banner += "\n  ⚠️ 授权审计未通过：\n" + "\n".join(f"     - {p}" for p in problems)
        if auto_audit and settings.strict_rights:
            sys.exit(banner + "\n\n说不清授权的数据不予服务。"
                              "请先在 licenses/sources.json 补齐登记（附上真的读到的授权原文），"
                              "或用 --no-strict-rights 明知故犯地跳过（不推荐）。")
    print(banner, flush=True)

    app = FastAPI(
        title="诗文树 Poetry Tree API",
        description=DESCRIPTION,
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "诗文树 Poetry Tree",
                 "url": "https://github.com/liuliuwenzheng/poetry-tree"},
        license_info={"name": "MIT（本项目代码） / 各条文本随其来源许可",
                      "url": "https://github.com/liuliuwenzheng/poetry-tree/blob/main/LICENSE"},
    )
    app.state.settings = settings
    app.state.store = store
    app.state.registry = registry

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,          # 与 allow_origins=["*"] 不能同用
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # ---------------------------------------------------------- 错误结构统一
    @app.exception_handler(StarletteHTTPException)
    async def _http_err(request: Request, exc: StarletteHTTPException):
        d = exc.detail
        if isinstance(d, dict):
            return _err(d.get("code", "error"), d.get("message", str(d)),
                        d.get("hint"), exc.status_code)
        code = {400: "bad_request", 404: "not_found", 405: "method_not_allowed",
                409: "capability_missing", 422: "bad_request"}.get(exc.status_code, "error")
        return _err(code, str(d), None, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_err(request: Request, exc: RequestValidationError):
        first = (exc.errors() or [{}])[0]
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "query")
        return _err("bad_request", f"参数 {loc or '?'} 不对：{first.get('msg', '')}",
                    "看 /docs 里的参数说明")

    @app.exception_handler(ValueError)
    async def _value_err(request: Request, exc: ValueError):
        return _err("bad_request", str(exc))

    @app.exception_handler(Exception)
    async def _any_err(request: Request, exc: Exception):
        if settings.debug:
            raise exc
        return _err("internal_error", "服务端错误", None, 500)

    # ---------------------------------------------------------- 路由
    @app.get("/", include_in_schema=False)
    async def _index():
        return RedirectResponse("/api/v1/")

    app.include_router(make_router(store, settings))
    app.include_router(make_graphql_router(store, settings), prefix="/graphql",
                       tags=["GraphQL"])

    @app.on_event("shutdown")
    def _shutdown():
        store.close()

    return app
