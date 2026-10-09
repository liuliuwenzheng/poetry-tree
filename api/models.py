# -*- coding: utf-8 -*-
"""领域模型 —— **存储无关**。

命名跟 `docs/design.md` 的三轴模型走，不跟数据库表名走：

| 领域概念 | 说明 | 百川库里的表 |
|---|---|---|
| `Dynasty` 朝代 | 轴一：何时 | `dynasties_*` |
| `Genre` 体裁 | 轴二：何种文体（诗/词/曲…），**与朝代正交** | `genres_*` |
| `Form` 形式 | 轴三：格律形式（五言绝句…），属于某个体裁 | `poetry_types_*` |
| `Tune` 词牌 | 词的词牌、曲的曲牌 | `poems.tune` |
| `Poem` 作品 | 三轴独立引用 | `poems_*` |

**为什么不在模型里出现 `poetry_types` / `poems_zh_hans` 这种名字**：
表名是存储细节。哪天把库换成 Postgres、或把多语言语料并进来，
只要换 `storage.py` 的一个实现，接口和 SDK 一个字都不用改。
"""
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

# ------------------------------------------------------------------ 授权

class Rights(BaseModel):
    """一条文本的权利块。

    诗文树的第一原则是「授权不明 = 不收」，所以每一条记录都必须能说出自己的权利状况。
    这些字段**直接来自 `licenses/sources.json`**（构建闸门读的同一份文件），
    不另建一套 —— 否则接口和构建就会各说各话。
    """

    source: str = Field(description="来源登记键，如 chinese_poetry")
    source_name: Optional[str] = Field(None, description="人读的来源名")
    homepage: Optional[str] = None
    license: Optional[str] = Field(None, description="SPDX 标识：MIT / PD-US / CC-BY-SA-4.0")
    license_url: Optional[str] = None
    rights_holder: Optional[str] = None
    attribution_required: bool = False
    commercial_ok: bool = True
    share_alike: bool = Field(False, description="为 true 时下游须以兼容许可共享衍生材料")
    per_item_check_required: bool = Field(
        False, description="该来源声明的是「几乎所有作品 PD」，须逐条核对版权页")
    license_checked: bool = False
    checked_on: Optional[str] = None
    roles: list[str] = Field(default_factory=list, description="original_text / translation")
    note: Optional[str] = None


class SourceInfo(Rights):
    """来源登记项（`/sources` 用），比权利块多几个字段。"""

    key: str
    language: list[str] = Field(default_factory=list)
    blocked: bool = False
    blocked_reason: Optional[str] = None
    checked_evidence: Optional[str] = None


# ------------------------------------------------------------------ 三轴

class Dynasty(BaseModel):
    id: int
    name: str
    name_en: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    poem_count: Optional[int] = Field(None, description="该朝代作品数（简体口径）")


class Genre(BaseModel):
    """体裁。**与朝代正交** —— 「唐诗」是 唐×诗 的组合，不是一种体裁。"""

    id: int
    name: str
    name_en: Optional[str] = None
    description: Optional[str] = None
    poem_count: Optional[int] = None


class Form(BaseModel):
    """格律形式（五言绝句 / 七言律诗 / 古体…）。属于某个体裁。"""

    id: int
    name: str
    genre_id: Optional[int] = None
    genre: Optional[str] = Field(None, description="所属体裁名")
    lines: Optional[int] = Field(None, description="句数")
    chars_per_line: Optional[int] = Field(None, description="每句字数")
    description: Optional[str] = None


class Tune(BaseModel):
    """词牌 / 曲牌。跨朝代存在：查「所有《水调歌头》」靠它。"""

    name: str
    genre: Optional[str] = None
    count: int = 0


# ------------------------------------------------------------------ 作者 / 作品

class AuthorRef(BaseModel):
    id: int
    name: str


class Author(BaseModel):
    id: int
    name: str
    name_en: Optional[str] = None
    name_orig: Optional[str] = None
    dynasty: Optional[Dynasty] = None
    description: Optional[str] = None
    source: Optional[str] = None
    poem_count: Optional[int] = None
    aliases: list[str] = Field(
        default_factory=list, description="习惯叫法：陶渊明 ↔ 陶潜、苏东坡 ↔ 苏轼")


class Translation(BaseModel):
    """译文 —— 与原文**同权但独立**。译者版权独立于原作者，所以能单独撤下。"""

    lang: str
    title: Optional[str] = None
    content: list[str] = Field(default_factory=list)
    translator: Optional[str] = None
    source: Optional[str] = None
    rights: Optional[Rights] = None


class Poem(BaseModel):
    """一首作品。三轴（朝代/体裁/形式）各自独立引用。"""

    id: int
    title: str
    content: list[str] = Field(default_factory=list, description="段落数组，一段一联")
    text: str = Field("", description="拼接后的整首，省得每个调用方自己拼")
    script: str = Field("zh-Hans", description="zh-Hans | zh-Hant")
    dynasty: Optional[Dynasty] = None
    genre: Optional[Genre] = None
    form: Optional[Form] = None
    tune: Optional[str] = Field(None, description="词牌 / 曲牌")
    author: Optional[AuthorRef] = None
    source: Optional[str] = None
    period_orig: Optional[str] = Field(
        None, description="上游原始朝代标签（『元末明初』这类细标签，保留不丢）")
    rights: Optional[Rights] = None
    translations: list[Translation] = Field(default_factory=list)


# ------------------------------------------------------------------ 分页

class Page(BaseModel, Generic[T]):
    """一页结果。

    **游标分页（keyset）而非 offset**：这个库有 99 万首，`LIMIT 20 OFFSET 500000`
    要扫 50 万行；游标是 `WHERE id > ?`，走索引，翻到第 5 万页也是常数时间。

    `next_cursor` 直接回传即可，客户端不需要理解它的内容。
    """

    items: list[T] = Field(default_factory=list)
    total: Optional[int] = Field(None, description="命中总数（可能为 null 表示不统计）")
    next_cursor: Optional[str] = Field(None, description="下一页游标；null 表示没有下一页")
    has_more: bool = False


class SearchHit(BaseModel):
    """检索命中项：作品 + 高亮片段 + 相关性得分。"""

    poem: Poem
    snippet: Optional[str] = Field(None, description="命中片段，命中词用 [] 标出")
    score: Optional[float] = Field(None, description="bm25 相关性（越小越相关）")


class Stats(BaseModel):
    """全局统计。"""

    script: str
    poems: int
    authors: int
    dynasties: int
    genres: int
    tunes: int
    by_genre: dict[str, int] = Field(default_factory=dict)
    by_dynasty: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)


class FilterError(ValueError):
    """筛选用了一个**库里根本不存在的值** —— 这不等于「这个条件下没有作品」。

    两者在接口上都是「0 条结果」，但原因完全不同：

    | 情形 | 正确答案 |
    |---|---|
    | 值不存在（拼错 / 中文参数没做 URL 编码） | **报错并给出可选值** —— 调用方写错了 |
    | 值存在，但这个组合没有作品（明 × 诗经） | **返回空页** —— 合法查询，空就是答案 |

    混为一谈的后果是实测出来的：Windows 上 `curl --data-urlencode 'genre=词'`
    会把中文按本地代码页送出去，服务端收到乱码，于是**安静地返回 0 条**。
    用户看到的是「一首宋词都没有」—— 一个假结论，而且他没有任何线索知道
    是自己参数传错了。**这跟「搜不到 vs 不存在」是同一个病。**

    继承 `ValueError`，所以即使没被专门处理，也会落到「400 + 说明」的兜底路径上。
    """

    def __init__(self, message: str, *, axis: Optional[str] = None,
                 value: Optional[str] = None, valid: Optional[list[str]] = None,
                 hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.axis = axis
        self.value = value
        self.valid = valid
        self.hint = hint
