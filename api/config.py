# -*- coding: utf-8 -*-
"""接口配置。

一处定义，命令行/环境变量/代码都能改。默认值面向「本机起一个只读服务」。
"""
import os
from dataclasses import dataclass, field, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 百川库的默认位置（本机）；诗文树自己建库后可用 --db 指向别的
DEFAULT_DB = os.environ.get(
    "POETRY_TREE_DB",
    r"E:\AI-ku\项目\baichuan-poetry\data\baichuan.db")


@dataclass
class Settings:
    """服务配置。

    注意 `db` 是**只读**打开的：接口永远不写库。
    写库是 corpus/ 构建流程的事，两件事分开，接口就不可能在线上改坏数据。
    """

    db: str = DEFAULT_DB
    sources_json: str = field(default_factory=lambda: os.path.join(
        ROOT, "licenses", "sources.json"))
    host: str = "127.0.0.1"
    port: int = 8710
    # 分页
    page_default: int = 20
    page_max: int = 200
    # 全文检索用 offset 翻页（bm25 相关性排序没法做 keyset），所以给个上限，
    # 防止有人 page=99999 把库拖垮
    search_page_max: int = 50
    search_offset_max: int = 1000
    # CORS：默认放开，这是要「让自己和全世界人们使用」的开放接口
    cors_origins: list = field(default_factory=lambda: ["*"])
    # 启动时的授权审计：库里的来源必须都在 sources.json 登记过
    strict_rights: bool = True
    debug: bool = False

    @property
    def db_uri(self) -> str:
        """只读连接串。`mode=ro` 让 SQLite 在存储层就拒绝写入 —— 比靠自觉可靠。"""
        p = self.db.replace("\\", "/")
        return f"file:{p}?mode=ro"

    def as_dict(self):
        d = asdict(self)
        d.pop("db", None)          # 不外泄本机路径
        return d
