# -*- coding: utf-8 -*-
"""GraphQL 表现层。

**关键约束：它和 REST 共用同一个 `storage`**，不另写一套查询。
否则同一天查询在 /api/v1 和 /graphql 下答得不一样 —— 那是接口里最难查的一类 bug。

命名保持 `snake_case`（跟 REST 的 JSON 一致），不跟 GraphQL 的 camelCase 惯例走：
**两个协议字段名一致**，比各自符合惯例更有价值 —— 用户可以在两者之间无痛切换。

GraphiQL 交互页面挂在 `/graphql`，这是 GraphQL 相对 REST 最大的好处：
调用方能自己看见 schema、勾选字段，不用读文档猜。
"""
from typing import Optional

import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.schema.config import StrawberryConfig

from api.storage import AuthorQuery, PoemQuery, dec_cursor, parse_script

# ---------------------------------------------------------------- 输出类型

@strawberry.type(description="一条文本的权利块（来自 licenses/sources.json，与构建闸门同源）")
class GqlRights:
    source: str
    source_name: Optional[str] = None
    homepage: Optional[str] = None
    license: Optional[str] = strawberry.field(
        default=None, description="SPDX 标识：MIT / PD-US / CC-BY-SA-4.0")
    license_url: Optional[str] = None
    rights_holder: Optional[str] = None
    attribution_required: bool = False
    commercial_ok: bool = True
    share_alike: bool = strawberry.field(
        default=False, description="true = 下游须以兼容许可共享衍生材料")
    per_item_check_required: bool = False
    license_checked: bool = False
    checked_on: Optional[str] = None
    roles: list[str] = strawberry.field(default_factory=list)
    note: Optional[str] = None


@strawberry.type(description="朝代（轴一：何时）")
class GqlDynasty:
    id: int
    name: str
    name_en: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    poem_count: Optional[int] = None


@strawberry.type(description="体裁（轴二：何种文体，与朝代正交）")
class GqlGenre:
    id: int
    name: str
    name_en: Optional[str] = None
    description: Optional[str] = None
    poem_count: Optional[int] = None


@strawberry.type(description="形式（轴三：格律）")
class GqlForm:
    id: int
    name: str
    genre_id: Optional[int] = None
    genre: Optional[str] = None
    lines: Optional[int] = None
    chars_per_line: Optional[int] = None
    description: Optional[str] = None


@strawberry.type(description="词牌 / 曲牌")
class GqlTune:
    name: str
    genre: Optional[str] = None
    count: int = 0


@strawberry.type
class GqlAuthorRef:
    id: int
    name: str


@strawberry.type(description="作者（别名与描述按所查文字版本给出）")
class GqlAuthor:
    id: int
    name: str
    name_en: Optional[str] = None
    name_orig: Optional[str] = None
    dynasty: Optional[GqlDynasty] = None
    description: Optional[str] = None
    source: Optional[str] = None
    poem_count: Optional[int] = None
    aliases: list[str] = strawberry.field(default_factory=list)
    rights: Optional[GqlRights] = None


@strawberry.type(description="译文（与原文同权但独立，可单独撤下）")
class GqlTranslation:
    lang: str
    title: Optional[str] = None
    content: list[str] = strawberry.field(default_factory=list)
    translator: Optional[str] = None
    source: Optional[str] = None
    rights: Optional[GqlRights] = None


@strawberry.type(description="一首作品")
class GqlPoem:
    id: int
    title: str
    content: list[str] = strawberry.field(default_factory=list)
    text: str = ""
    script: str = "zh-Hans"
    dynasty: Optional[GqlDynasty] = None
    genre: Optional[GqlGenre] = None
    form: Optional[GqlForm] = None
    tune: Optional[str] = None
    author: Optional[GqlAuthorRef] = None
    source: Optional[str] = None
    period_orig: Optional[str] = None
    rights: Optional[GqlRights] = None
    translations: list[GqlTranslation] = strawberry.field(default_factory=list)


@strawberry.type
class GqlPoemPage:
    items: list[GqlPoem] = strawberry.field(default_factory=list)
    total: Optional[int] = None
    next_cursor: Optional[str] = None
    has_more: bool = False


@strawberry.type
class GqlAuthorPage:
    items: list[GqlAuthor] = strawberry.field(default_factory=list)
    total: Optional[int] = None
    next_cursor: Optional[str] = None
    has_more: bool = False


@strawberry.type
class GqlSearchHit:
    poem: GqlPoem
    snippet: Optional[str] = strawberry.field(
        default=None, description="命中片段，命中词用 [] 标出")
    score: Optional[float] = strawberry.field(
        default=None, description="bm25 相关性（越小越相关）")


@strawberry.type
class GqlSearchResult:
    q: str
    total: int
    count: int
    page: int
    limit: int
    engine: str = strawberry.field(
        description="`fts5-bm25`（全文索引）或 `like-fallback`（子串扫描）")
    note: Optional[str] = strawberry.field(
        default=None, description="降级时说明为什么走了这条路")
    items: list[GqlSearchHit] = strawberry.field(default_factory=list)


@strawberry.type
class GqlSource:
    key: str
    name: Optional[str] = None
    homepage: Optional[str] = None
    language: list[str] = strawberry.field(default_factory=list)
    license: Optional[str] = None
    license_url: Optional[str] = None
    rights_holder: Optional[str] = None
    attribution_required: bool = False
    commercial_ok: bool = True
    share_alike: bool = False
    per_item_check_required: bool = False
    license_checked: bool = False
    checked_on: Optional[str] = None
    checked_evidence: Optional[str] = None
    roles: list[str] = strawberry.field(default_factory=list)
    blocked: bool = False
    blocked_reason: Optional[str] = None
    note: Optional[str] = None
    in_db: bool = strawberry.field(
        default=False, description="这个来源的数据当前是否真的在库里")


@strawberry.type
class GqlCapabilities:
    genres: bool
    tune: bool
    full_text_search: list[str] = strawberry.field(default_factory=list)
    author_aliases: bool = False
    world_poetry: bool = False
    translations: bool = False


@strawberry.type
class GqlInfo:
    name: str
    policy_first_rule: str
    capabilities: GqlCapabilities
    notes: list[str] = strawberry.field(default_factory=list)


@strawberry.type
class GqlStats:
    script: str
    poems: int
    authors: int
    dynasties: int
    genres: int
    tunes: int
    by_genre: strawberry.scalars.JSON = strawberry.field(
        default_factory=dict, description="体裁 → 作品数")
    by_dynasty: strawberry.scalars.JSON = strawberry.field(
        default_factory=dict, description="朝代 → 作品数")
    by_source: strawberry.scalars.JSON = strawberry.field(
        default_factory=dict, description="来源 → 作品数")


# ---------------------------------------------------------------- 转换器
# pydantic 模型 → GraphQL 类型。显式写出来而不是反射：
# 字段增删时编译期就能发现，不会悄悄漏掉一个字段。
#
# 每个 `_x()` 只接受**非 None**，Optional 场景在调用处写 `_x(v) if v else None`。
# 这样类型检查能真正帮上忙，而不是到处都是 `| None`。

def _rights(r) -> GqlRights:
    return GqlRights(**r.model_dump())


def _dynasty(d) -> GqlDynasty:
    return GqlDynasty(**d.model_dump())


def _genre(g) -> GqlGenre:
    return GqlGenre(**g.model_dump())


def _form(f) -> GqlForm:
    return GqlForm(**f.model_dump())


def _author_ref(a) -> GqlAuthorRef:
    return GqlAuthorRef(id=a.id, name=a.name)


def _translation(t) -> GqlTranslation:
    return GqlTranslation(lang=t.lang, title=t.title, content=t.content,
                          translator=t.translator, source=t.source,
                          rights=_rights(t.rights) if t.rights else None)


def _poem(p) -> GqlPoem:
    return GqlPoem(
        id=p.id, title=p.title, content=p.content, text=p.text, script=p.script,
        dynasty=_dynasty(p.dynasty) if p.dynasty else None,
        genre=_genre(p.genre) if p.genre else None,
        form=_form(p.form) if p.form else None,
        tune=p.tune, author=_author_ref(p.author) if p.author else None,
        source=p.source, period_orig=p.period_orig,
        rights=_rights(p.rights) if p.rights else None,
        translations=[_translation(t) for t in p.translations])


def _author(a, store=None) -> GqlAuthor:
    return GqlAuthor(
        id=a.id, name=a.name, name_en=a.name_en, name_orig=a.name_orig,
        dynasty=_dynasty(a.dynasty) if a.dynasty else None,
        description=a.description, source=a.source, poem_count=a.poem_count,
        aliases=a.aliases,
        rights=(store.rights_of(a.source) if store and a.source else None))


def _page_model(src, fn) -> dict:
    return {"items": [fn(x) for x in src.items], "total": src.total,
            "next_cursor": src.next_cursor, "has_more": src.has_more}


def _script(script: str) -> str:
    try:
        return parse_script(script)
    except ValueError as e:
        raise ValueError(str(e)) from e


# ---------------------------------------------------------------- Query

@strawberry.type
class Query:
    @strawberry.field(description="服务信息（含第一原则与能力清单）")
    def info(self, info: strawberry.Info) -> GqlInfo:
        store = info.context["store"]
        return GqlInfo(
            name="诗文树 Poetry Tree",
            policy_first_rule="只收能够确认授权的材料。授权不明 = 不收。",
            capabilities=GqlCapabilities(
                genres=store.caps.genres, tune=store.caps.tune,
                full_text_search=sorted(store.caps.fts),
                author_aliases=store.caps.aliases,
                world_poetry=store.caps.world, translations=store.caps.texts),
            notes=store.caps.note)

    @strawberry.field(description="朝代轴")
    def dynasties(self, info: strawberry.Info, script: str = "zh-Hans",
                  with_counts: bool = True) -> list[GqlDynasty]:
        store = info.context["store"]
        return [_dynasty(d) for d in store.dynasties(_script(script), with_counts)]

    @strawberry.field(description="体裁轴（与朝代正交：唐诗 = 唐×诗）")
    def genres(self, info: strawberry.Info, script: str = "zh-Hans",
               with_counts: bool = True) -> list[GqlGenre]:
        store = info.context["store"]
        return [_genre(g) for g in store.genres(_script(script), with_counts)]

    @strawberry.field(description="形式轴（格律）")
    def forms(self, info: strawberry.Info, script: str = "zh-Hans") -> list[GqlForm]:
        store = info.context["store"]
        return [_form(f) for f in store.forms(_script(script))]

    @strawberry.field(description="词牌 / 曲牌（跨朝代查「所有《水调歌头》」）")
    def tunes(self, info: strawberry.Info, script: str = "zh-Hans",
              genre: Optional[str] = None, q: Optional[str] = None,
              limit: int = 200, min_count: int = 1) -> list[GqlTune]:
        store = info.context["store"]
        return [GqlTune(name=t.name, genre=t.genre, count=t.count)
                for t in store.tunes(_script(script), genre, q, limit, min_count)]

    @strawberry.field(description="统计")
    def stats(self, info: strawberry.Info, script: str = "zh-Hans") -> GqlStats:
        store = info.context["store"]
        st = store.stats(_script(script))
        return GqlStats(script=st.script, poems=st.poems, authors=st.authors,
                        dynasties=st.dynasties, genres=st.genres, tunes=st.tunes,
                        by_genre=st.by_genre, by_dynasty=st.by_dynasty,
                        by_source=st.by_source)

    @strawberry.field(description="按 id 取一首（同一 id 在任何 script 下都是同一首诗）")
    def poem(self, info: strawberry.Info, id: int, script: str = "zh-Hans",
             translations: bool = True) -> Optional[GqlPoem]:
        store = info.context["store"]
        p = store.poem(id, _script(script), with_translations=translations)
        return _poem(p) if p else None

    @strawberry.field(description="作品列表（游标分页）")
    def poems(self, info: strawberry.Info,
              script: str = "zh-Hans",
              dynasty: Optional[str] = None, genre: Optional[str] = None,
              form: Optional[str] = None, tune: Optional[str] = None,
              author: Optional[str] = None, q: Optional[str] = None,
              source: Optional[str] = None, license: Optional[str] = None,
              share_alike: Optional[bool] = None, commercial_ok: Optional[bool] = None,
              after: Optional[str] = None, limit: int = 20) -> GqlPoemPage:
        store = info.context["store"]
        n = max(1, min(limit, info.context["settings"].page_max))
        pq = PoemQuery(dynasty=dynasty, genre=genre, form=form, tune=tune, author=author,
                       q=q, source=source, license=license, share_alike=share_alike,
                       commercial_ok=commercial_ok, after=dec_cursor(after), limit=n)
        return GqlPoemPage(**_page_model(store.poems(pq, _script(script)), _poem))

    @strawberry.field(description="随机一首（筛选条件同 poems）")
    def random_poem(self, info: strawberry.Info,
                    script: str = "zh-Hans",
                    dynasty: Optional[str] = None, genre: Optional[str] = None,
                    form: Optional[str] = None, tune: Optional[str] = None,
                    author: Optional[str] = None, q: Optional[str] = None,
                    license: Optional[str] = None,
                    share_alike: Optional[bool] = None) -> Optional[GqlPoem]:
        store = info.context["store"]
        pq = PoemQuery(dynasty=dynasty, genre=genre, form=form, tune=tune, author=author,
                       q=q, license=license, share_alike=share_alike,
                       limit=1, order="random")
        p = store.random_poem(pq, _script(script))
        return _poem(p) if p else None

    @strawberry.field(description="全文检索（bm25；trigram 分词，检索词至少 3 个字）")
    def search(self, info: strawberry.Info, q: str, script: str = "zh-Hans",
               dynasty: Optional[str] = None, genre: Optional[str] = None,
               form: Optional[str] = None, tune: Optional[str] = None,
               author: Optional[str] = None,
               license: Optional[str] = None,
               share_alike: Optional[bool] = None,
               limit: int = 20, page: int = 1) -> GqlSearchResult:
        store = info.context["store"]
        hits, total, used, note = store.search(q, _script(script), PoemQuery(
            dynasty=dynasty, genre=genre, form=form, tune=tune, author=author,
            license=license, share_alike=share_alike), limit, page)
        return GqlSearchResult(
            q=q, total=total, count=len(hits), page=page, limit=limit,
            engine="fts5-bm25" if used else "like-fallback", note=note or None,
            items=[GqlSearchHit(poem=_poem(h.poem), snippet=h.snippet, score=h.score)
                   for h in hits])

    @strawberry.field(description="作者列表")
    def authors(self, info: strawberry.Info, script: str = "zh-Hans",
                q: Optional[str] = None, dynasty: Optional[str] = None,
                after: Optional[str] = None, limit: int = 20) -> GqlAuthorPage:
        store = info.context["store"]
        n = max(1, min(limit, info.context["settings"].page_max))
        page = store.authors(AuthorQuery(q=q, dynasty=dynasty, after=dec_cursor(after),
                                         limit=n), _script(script))
        return GqlAuthorPage(**_page_model(page, lambda a: _author(a, store)))

    @strawberry.field(description="作者详情")
    def author(self, info: strawberry.Info, id: int,
               script: str = "zh-Hans") -> Optional[GqlAuthor]:
        store = info.context["store"]
        a = store.author(id, _script(script))
        return _author(a, store) if a else None

    @strawberry.field(description="别名解析：陶渊明 → 陶潜（「搜不到」和「不存在」是两件事）")
    def resolve_author(self, info: strawberry.Info, name: str,
                       script: str = "zh-Hans") -> list[GqlAuthor]:
        store = info.context["store"]
        s = _script(script)
        out = []
        for sid in store.author_ids(name, s):
            a = store.author(sid, s)
            if a:
                out.append(_author(a, store))
        return out

    @strawberry.field(description="来源与授权登记（与构建闸门同一份文件）")
    def sources(self, info: strawberry.Info,
                include_blocked: bool = True) -> list[GqlSource]:
        store = info.context["store"]
        in_db = set(store.source_keys_in_db())
        out = []
        for s in store.reg.all_sources(include_blocked):
            d = s.model_dump()
            d["in_db"] = d["key"] in in_db
            out.append(GqlSource(**d))
        return out


# `auto_camel_case=False` 是**刻意的**：默认会转成 camelCase（`poemCount`），
# 而我们要的是与 REST 的 JSON 完全同名（`poem_count`）。
# 字段名一致比各自符合惯例更有价值 —— 调用方能在两个协议之间无痛切换，
# 同一份代码、同一套字段名，只换个传输方式。
schema = strawberry.Schema(
    query=Query, config=StrawberryConfig(auto_camel_case=False))


def make_graphql_router(store, settings):
    """`/graphql` 路由。带 GraphiQL 交互页面（浏览器打开就能探索 schema）。"""
    async def _ctx():
        return {"store": store, "settings": settings}
    return GraphQLRouter(schema, context_getter=_ctx, graphql_ide="graphiql")
