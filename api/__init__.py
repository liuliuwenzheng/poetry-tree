# -*- coding: utf-8 -*-
"""诗文树 · 开放接口层。

分三层，**依赖单向**：

    rest.py / graphql_api.py      ← 表现层（只做协议转换）
            ↓
    storage.py                    ← 领域层（领域模型 + 查询语义）
            ↓
    SQLite（百川库 / 未来其他库）  ← 存储层（可替换）

这么分的原因：**接口层不认识表名**。今天读百川的 `poems_zh_hans`，
明天读诗文树自己的多语言语料，表现层一行都不用改。
换数据库（SQLite→Postgres）也只动 storage.py 里的一个实现。

`corpus/rights.py` 的授权闸门不在依赖链上，而是被 `rights.py` **复用** ——
接口的授权信息和构建闸门读同一份 `licenses/sources.json`，永远一致。
"""

__all__ = ["config", "models", "storage", "rights", "app"]
