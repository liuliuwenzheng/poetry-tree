#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诗文树 Python SDK —— 零第三方依赖（只用标准库）。

```python
from poetry_tree import PoetryTree

pt = PoetryTree("http://127.0.0.1:8710")

pt.random(genre="词", dynasty="清")           # 清词
pt.poems(genre="诗", dynasty="唐", limit=5)   # 唐诗
pt.search("明月几时有")                        # 全文检索（≥3 字）

for p in pt.iter_poems(author="陶渊明", limit=100):
    print(p["title"])                          # 自动翻页，不用管游标
```

设计要点：
* **只用标准库**（urllib）—— 这个 SDK 应该能在任何环境里直接跑，不要求先装东西；
* `iter_poems()` / `iter_authors()` **自动翻页**：游标是服务端的实现细节，
  调用方不该看到它；
* 失败时抛 `PoetryTreeError`，带服务端返回的 `code` / `message` / `hint`，
  而不是丢一个裸 HTTP 状态码给调用方。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["PoetryTree", "PoetryTreeError", "__version__"]
__version__ = "1.0.0"

DEFAULT_BASE = "http://127.0.0.1:8710"


class PoetryTreeError(RuntimeError):
    """服务端返回的结构化错误。`code` / `hint` 来自接口，可直接展示给用户。"""

    def __init__(self, code: str, message: str, hint: str | None = None, status: int = 0):
        self.code, self.message, self.hint, self.status = code, message, hint, status
        parts = [f"[{code}] {message}"]
        if hint:
            parts.append(f"提示：{hint}")
        super().__init__("  ".join(parts))


class PoetryTree:
    """一个只读的诗歌语料库客户端。"""

    def __init__(self, base_url: str = DEFAULT_BASE, timeout: float = 20.0,
                 script: str = "zh-Hans"):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.script = script
        self._limits: dict | None = None

    # ------------------------------------------------------------ 限额
    def limits(self) -> dict:
        """服务端的限额（读一次后缓存）。

        **刻意从服务端读，不在客户端硬编码**：服务端把每页上限调小或调大，
        SDK 跟上就行，用户的脚本不用改。读不到就退回保守默认值。
        """
        if self._limits is None:
            try:
                self._limits = (self.info() or {}).get("limits") or {}
            except PoetryTreeError:
                self._limits = {}
        return self._limits

    @property
    def max_page(self) -> int:
        try:
            return int(self.limits().get("page_max") or 200)
        except (TypeError, ValueError):
            return 200

    # ------------------------------------------------------------ 传输
    def _get(self, path: str, **params):
        q = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}{path}"
        if q:
            url += "?" + urllib.parse.urlencode(q, doseq=True)
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": f"poetry-tree-sdk/{__version__}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail: dict = {}
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {}
            cand = body.get("error") if isinstance(body, dict) else None
            if cand is None and isinstance(body, dict):
                cand = body.get("detail")          # FastAPI 校验错误是 {"detail": ...}
            if isinstance(cand, str):
                detail = {"message": cand}
            elif isinstance(cand, dict):
                detail = cand
            raise PoetryTreeError(detail.get("code", "http_error"),
                                  detail.get("message", f"HTTP {e.code}"),
                                  detail.get("hint"), e.code) from None
        except urllib.error.URLError as e:
            raise PoetryTreeError(
                "connection_error", f"连不上 {self.base}：{e.reason}",
                "服务起了吗？`python -m api.serve`") from None

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise PoetryTreeError("graphql_error", f"HTTP {e.code}", None, e.code) from None

    # ------------------------------------------------------------ 元信息
    def info(self) -> dict:
        """服务信息：第一原则、能力清单、端点索引。"""
        return self._get("/api/v1/")

    def health(self) -> dict:
        return self._get("/api/v1/healthz")

    def dynasties(self, script: str | None = None, with_counts: bool = True) -> list[dict]:
        return self._get("/api/v1/meta/dynasties",
                         script=script or self.script,
                         with_counts=with_counts)["items"]

    def genres(self, script: str | None = None, with_counts: bool = True) -> list[dict]:
        """体裁轴。与朝代正交，所以这里没有「唐诗」这种条目，只有「诗」。"""
        return self._get("/api/v1/meta/genres", script=script or self.script,
                         with_counts=with_counts)["items"]

    def forms(self, script: str | None = None) -> list[dict]:
        return self._get("/api/v1/meta/forms", script=script or self.script)["items"]

    def tunes(self, genre: str | None = None, q: str | None = None, limit: int = 200,
              min_count: int = 1, script: str | None = None) -> list[dict]:
        return self._get("/api/v1/meta/tunes", script=script or self.script,
                         genre=genre, q=q, limit=limit, min_count=min_count)["items"]

    def stats(self, script: str | None = None) -> dict:
        return self._get("/api/v1/meta/stats", script=script or self.script)

    def sources(self) -> dict:
        """来源与授权登记 —— 与构建闸门读的是同一份文件。"""
        return self._get("/api/v1/sources")

    # ------------------------------------------------------------ 作品
    def poems(self, limit: int = 20, after: str | None = None, order: str = "id",
              script: str | None = None, **filters) -> dict:
        """作品列表。返回原始分页对象（items/total/next_cursor/has_more）。

        过滤参数全用名字：`dynasty="唐"`、`genre="词"`、`form="五言绝句"`、
        `tune="水调歌头"`、`author="陶渊明"`、`q="明月"`、
        `license="MIT"`、`share_alike=False`、`commercial_ok=True`。
        """
        return self._get("/api/v1/poems", script=script or self.script, limit=limit,
                         after=after, order=order, **filters)

    def poem(self, poem_id: int, script: str | None = None,
             translations: bool = True) -> dict:
        return self._get(f"/api/v1/poems/{poem_id}", script=script or self.script,
                         translations=translations)

    def random(self, script: str | None = None, **filters) -> dict:
        """随机一首。**没有符合条件的就抛错**，不会悄悄返回个不相干的。"""
        return self._get("/api/v1/poems/random", script=script or self.script, **filters)

    def translations(self, poem_id: int, script: str | None = None) -> list[dict]:
        return self._get(f"/api/v1/poems/{poem_id}/translations",
                         script=script or self.script)["items"]

    # ------------------------------------------------------------ 检索
    def search(self, q: str, limit: int = 20, page: int = 1,
               script: str | None = None, **filters) -> dict:
        """全文检索。

        * **3 个字以上** → 走全文索引，按 bm25 相关性排序、带 `snippet` 高亮片段；
        * **1–2 个字** → trigram 索引按 3 字切分、短词匹配不到，接口**自动改用
          子串匹配**，并在 `note` 里说明原因。所以「明月」查得到（2 万多首），
          不会给你一个骗人的「0 结果」；
        * 检索词里有引号/星号等 FTS 不接受的符号 → 也自动换路，不会 500。

        返回里的 `engine` 字段（`fts5-bm25` / `like-fallback`）告诉你这次走了哪条。
        """
        return self._get("/api/v1/search", q=q, limit=limit, page=page,
                         script=script or self.script, **filters)

    # ------------------------------------------------------------ 作者
    def authors(self, limit: int = 20, after: str | None = None,
                script: str | None = None, **filters) -> dict:
        return self._get("/api/v1/authors", script=script or self.script,
                         limit=limit, after=after, **filters)

    def author(self, author_id: int, script: str | None = None) -> dict:
        return self._get(f"/api/v1/authors/{author_id}", script=script or self.script)

    def resolve_author(self, name: str, script: str | None = None) -> list[dict]:
        """别名解析。**「搜不到」和「不存在」是两件事** —— 陶渊明在库里叫陶潜。"""
        return self._get("/api/v1/authors/resolve", name=name,
                         script=script or self.script)["items"]

    # ------------------------------------------------------------ 自动翻页
    def iter_poems(self, page_size: int = 200, max_items: int | None = None,
                   script: str | None = None, **filters):
        """**自动翻页**生成器 —— 游标是服务端的实现细节，调用方不该看到它。

        `page_size` 会**自动夹到服务端允许的上限**（从 `/api/v1/` 读出来，
        不在客户端硬编码），所以写 500 不会报错，只是每页按上限取。
        这是内部分页细节，悄悄按上限取是对的；显式调 `poems(limit=500)`
        则仍会拿到服务端的报错 —— 那是调用方明确要的东西，不该替他改。

        ```python
        for p in pt.iter_poems(genre="词", dynasty="清", page_size=500):
            ...
        ```
        """
        page_size = max(1, min(int(page_size), self.max_page))
        got, after = 0, None
        while True:
            page = self.poems(limit=page_size, after=after, script=script, **filters)
            for item in page["items"]:
                yield item
                got += 1
                if max_items and got >= max_items:
                    return
            if not page.get("has_more") or not page.get("next_cursor"):
                return
            after = page["next_cursor"]

    def iter_authors(self, page_size: int = 200, max_items: int | None = None,
                     script: str | None = None, **filters):
        """作者版自动翻页。`page_size` 同样自动夹到服务端上限。"""
        page_size = max(1, min(int(page_size), self.max_page))
        got, after = 0, None
        while True:
            page = self.authors(limit=page_size, after=after, script=script, **filters)
            for item in page["items"]:
                yield item
                got += 1
                if max_items and got >= max_items:
                    return
            if not page.get("has_more") or not page.get("next_cursor"):
                return
            after = page["next_cursor"]

    # ------------------------------------------------------------ GraphQL
    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """直接打 GraphQL。字段名与 REST 的 JSON 一致（都是 snake_case）。"""
        out = self._post("/graphql", {"query": query, "variables": variables or {}})
        if out.get("errors"):
            msgs = "; ".join(e.get("message", "?") for e in out["errors"])
            raise PoetryTreeError("graphql_error", msgs)
        return out.get("data") or {}

    def schema_types(self) -> list[str]:
        """用自省列出 GraphQL 类型名。想快速看清接口全貌时用。

        要看完整 schema，浏览器打开 `/graphql`（GraphiQL 里能直接导出 SDL）。
        """
        data = self.graphql("{ __schema { types { name kind } } }")
        types = (data.get("__schema") or {}).get("types") or []
        return sorted(t["name"] for t in types if not t["name"].startswith("__"))

    def fields_of(self, type_name: str) -> list[dict]:
        """某个 GraphQL 类型的字段清单（名字 + 类型）。"""
        data = self.graphql(
            "query($n:String!){ __type(name:$n){ fields { name description type { name kind ofType { name kind } } } } }",
            {"n": type_name})
        t = data.get("__type") or {}
        return t.get("fields") or []


def _demo():
    pt = PoetryTree()
    print("服务：", pt.info()["name"])
    print("体裁：", [g["name"] for g in pt.genres()])
    print("\n清词随机一首：")
    p = pt.random(genre="词", dynasty="清")
    print(f"  《{p['title']}》 {p['author']['name']} [{p['dynasty']['name']}·{p['genre']['name']}]")
    print("  " + "".join(p["content"]))
    print(f"  授权：{p['rights']['license']} · {p['rights']['source_name']}")
    print("\n检索「明月几时有」：")
    for h in pt.search("明月几时有", limit=3)["items"]:
        print(f"  《{h['poem']['title']}》 {h['poem']['author']['name']}  score={h['score']}")


if __name__ == "__main__":
    _demo()
