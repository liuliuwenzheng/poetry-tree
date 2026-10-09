# -*- coding: utf-8 -*-
"""REST 表现层。

**这一层只做协议转换**：解析参数 → 调 storage → 返回模型。
一行 SQL 都不写（写了就说明抽象漏了）。

几个刻意的决定：

1. **`/api/v1` 前缀**：接口一旦放出去就不能随便改，版本号是从第一天就该有的。
2. **文字版本是一个参数，不是两套端点**：`?script=zh-Hant`。
   因为服务端保证「同一 id 在任何 script 下都是同一首诗」（百川 v0.3.0 的不变量），
   所以换文字版本只是换个渲染，不是换个资源。
3. **筛选参数用人类可读的名字**，不是 id：`?genre=词&dynasty=唐`。
   名字过滤认得简繁两种写法，调用方不必知道库用哪种文字。
4. **授权可筛可看**：`?license=MIT`、`?share_alike=false`，
   响应里每条都带 `rights` 块。这是诗文树第一原则在接口上的落地。
"""
from typing import NoReturn, Optional

from fastapi import APIRouter, HTTPException, Path, Query

from api.models import (Author, Page, Poem, SearchHit, SourceInfo, Stats, Translation)
from api.storage import AuthorQuery, PoemQuery, dec_cursor, parse_script

ERR = {
    400: {"description": "参数不对（游标格式、script 取值、越界的分页等）"},
    404: {"description": "没有这个资源"},
    500: {"description": "服务端错误"},
}


def _bad(msg: str) -> NoReturn:
    raise HTTPException(status_code=400, detail={"code": "bad_request", "message": msg})


def make_router(store, settings) -> APIRouter:
    r = APIRouter(prefix="/api/v1")

    # ------------------------------------------------------------ 通用依赖
    def _script(script: str) -> str:
        try:
            return parse_script(script)
        except ValueError as e:
            _bad(str(e))

    def _cursor(after: Optional[str]) -> Optional[int]:
        try:
            return dec_cursor(after)
        except ValueError as e:
            _bad(str(e))

    def _limit(limit: Optional[int], default: Optional[int] = None) -> int:
        n = limit or default or settings.page_default
        if n < 1:
            _bad("limit 至少为 1")
        if n > settings.page_max:
            _bad(f"limit 最大 {settings.page_max}（深翻页请用 next_cursor）")
        return n

    def _script_q(script: str = Query(
            "zh-Hans",
            description="文字版本：`zh-Hans`（简体）或 `zh-Hant`（繁体）。"
                        "同一 id 在两种版本下是**同一首诗**，换版本不换资源。")):
        return _script(script)

    # ============================================================ 服务信息
    @r.get("/", summary="服务信息与端点索引", tags=["元信息"])
    def root():
        return {
            "name": "诗文树 Poetry Tree",
            "tagline": "让诗的美更容易被遇见——同时不侵害任何作者与版权所有者的利益。",
            "policy": {
                "first_rule": "只收能够确认授权的材料。授权不明 = 不收。",
                "enforcement": "库里的每一个来源都必须在 licenses/sources.json 登记且已核实，"
                               "否则服务拒绝启动。",
                "translations": "译文与原文同权但独立：译者版权独立于原作者，译文可单独撤下。",
                "license_filter": "接口支持按 license / share_alike / commercial_ok 过滤，"
                                  "让无法履行 ShareAlike 的下游用户能排除相应记录。",
            },
            "version": "v1",
            # 限额也放进服务信息：客户端不该硬编码服务端的策略，
            # 读一次就知道每页最多能要多少条。
            "limits": {
                "page_default": settings.page_default,
                "page_max": settings.page_max,
                "search_page_max": settings.search_page_max,
                "search_offset_max": settings.search_offset_max,
            },
            "capabilities": {
                "genres": store.caps.genres,
                "tune": store.caps.tune,
                "full_text_search": sorted(store.caps.fts),
                "author_aliases": store.caps.aliases,
                "world_poetry": store.caps.world,
                "translations": store.caps.texts,
            },
            "notes": store.caps.note,
            "endpoints": [
                {"method": "GET", "path": "/api/v1/poems",
                 "desc": "作品列表；可按 dynasty / genre / form / tune / author 组合过滤"},
                {"method": "GET", "path": "/api/v1/poems/{id}", "desc": "按 id 取一首"},
                {"method": "GET", "path": "/api/v1/poems/random", "desc": "随机一首（支持同样的过滤）"},
                {"method": "GET", "path": "/api/v1/poems/{id}/translations", "desc": "该作品的译文"},
                {"method": "GET", "path": "/api/v1/search", "desc": "全文检索（bm25 相关性）"},
                {"method": "GET", "path": "/api/v1/authors", "desc": "作者列表"},
                {"method": "GET", "path": "/api/v1/authors/{id}", "desc": "作者详情"},
                {"method": "GET", "path": "/api/v1/authors/resolve", "desc": "别名解析（陶渊明→陶潜）"},
                {"method": "GET", "path": "/api/v1/meta/dynasties", "desc": "朝代轴"},
                {"method": "GET", "path": "/api/v1/meta/genres", "desc": "体裁轴（与朝代正交）"},
                {"method": "GET", "path": "/api/v1/meta/forms", "desc": "形式轴（格律）"},
                {"method": "GET", "path": "/api/v1/meta/tunes", "desc": "词牌/曲牌"},
                {"method": "GET", "path": "/api/v1/meta/stats", "desc": "统计"},
                {"method": "GET", "path": "/api/v1/sources", "desc": "来源与授权登记"},
                {"method": "GET", "path": "/api/v1/world", "desc": "非中文原文作品"},
                {"method": "GET", "path": "/api/v1/healthz", "desc": "健康检查"},
            ],
            "graphql": "/graphql",
        }

    @r.get("/healthz", summary="健康检查", tags=["元信息"])
    def healthz():
        problems = store.reg.audit(store.db_sources())
        return {"status": "ok" if not problems else "degraded",
                "rights_ok": not problems,
                "rights_problems": problems,
                "capabilities": {"genres": store.caps.genres,
                                 "fts": sorted(store.caps.fts),
                                 "aliases": store.caps.aliases}}

    # ============================================================ 三轴 + 统计
    @r.get("/meta/dynasties", summary="朝代列表（轴一）", tags=["元信息"])
    def dynasties(script: str = Query("zh-Hans"), with_counts: bool = Query(True)):
        return {"script": script,
                "items": store.dynasties(_script(script), with_counts)}

    @r.get("/meta/genres", summary="体裁列表（轴二，与朝代正交）", tags=["元信息"])
    def genres(script: str = Query("zh-Hans"), with_counts: bool = Query(True)):
        s = _script(script)
        items = store.genres(s, with_counts)
        if not store.caps.genres:
            raise HTTPException(409, detail={
                "code": "capability_missing",
                "message": "这个库没有体裁轴（旧模型把朝代写进了体裁名）",
                "hint": "用百川 v0.3.0+ 的库，或直接按 form 过滤"})
        return {"script": script, "items": items,
                "usage": {"all_ci": "?genre=词", "tang_shi": "?genre=诗&dynasty=唐",
                          "song_ci": "?genre=词&dynasty=宋", "qing_ci": "?genre=词&dynasty=清"}}

    @r.get("/meta/forms", summary="形式列表（轴三，格律）", tags=["元信息"])
    def forms(script: str = Query("zh-Hans")):
        return {"script": script, "items": store.forms(_script(script))}

    @r.get("/meta/tunes", summary="词牌 / 曲牌", tags=["元信息"])
    def tunes(script: str = Query("zh-Hans"), genre: Optional[str] = Query(None),
              q: Optional[str] = Query(None), limit: int = Query(200, le=1000),
              min_count: int = Query(1, ge=1)):
        s = _script(script)
        if not store.caps.tune:
            raise HTTPException(409, detail={"code": "capability_missing",
                                             "message": "这个库没有 tune 列（词牌/曲牌）",
                                             "hint": "用百川 v0.3.0+ 的库"})
        return {"script": script, "items": store.tunes(s, genre, q, limit, min_count),
                "usage": {"all_shuidiaogetou": "?q=水调歌头&min_count=1"}}

    @r.get("/meta/stats", summary="统计", tags=["元信息"])
    def stats(script: str = Query("zh-Hans")):
        return store.stats(_script(script))

    # ============================================================ 来源与授权
    @r.get("/sources", summary="来源与授权登记", tags=["授权"])
    def sources(script: str = Query("zh-Hans")):
        _script(script)
        # `in_db` 同时给两种形式：
        #   * 顶层 `in_db`   —— 库里 `source` 列的原值（`chinese-poetry`），
        #                       跟 /poems?source= 能用的取值一致；
        #   * 每条 `in_db`   —— 这个来源当前是否真有数据入库。
        # 两者都要：登记了但还没入库的来源（如 wikisource）必须能被区分出来，
        # 否则使用者无法判断「过滤后 0 条」是没数据还是查错了。
        keys = set(store.source_keys_in_db())
        return {
            "policy": store.reg.policy,
            "schema_version": store.reg.schema_version,
            "license_filters": store.reg.license_filters(),
            "items": [{**s.model_dump(), "in_db": s.key in keys}
                      for s in store.reg.all_sources()],
            "in_db": sorted(store.db_sources()),
            "in_db_keys": sorted(keys),
        }

    @r.get("/sources/{key}", summary="单个来源", tags=["授权"])
    def source_one(key: str, script: str = Query("zh-Hans")):
        _script(script)
        try:
            return store.reg.to_source_info(key).model_dump()
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found",
                                             "message": f"没有这个来源：{key}",
                                             "hint": "列出全部见 /api/v1/sources"})

    # ============================================================ 作品
    def _poem_query(dynasty, genre, form, tune, author, q, source, license_,
                    share_alike, commercial_ok, after, limit, order) -> PoemQuery:
        return PoemQuery(
            dynasty=dynasty, genre=genre, form=form, tune=tune, author=author, q=q,
            source=source, license=license_, share_alike=share_alike,
            commercial_ok=commercial_ok, after=_cursor(after), limit=limit, order=order)

    # 注意：/poems/random 必须注册在 /poems/{pid} 之前，否则会被当成 pid 匹配
    @r.get("/poems/random", summary="随机一首", tags=["作品"])
    def poem_random(
            script: str = Query("zh-Hans"),
            dynasty: Optional[str] = Query(None, description="朝代名，如 唐 / 宋 / 清"),
            genre: Optional[str] = Query(None, description="体裁名，如 诗 / 词 / 曲（简繁都认）"),
            form: Optional[str] = Query(None, description="形式名，如 五言绝句"),
            tune: Optional[str] = Query(None, description="词牌，如 水调歌头"),
            author: Optional[str] = Query(None, description="作者名或别名，如 陶渊明"),
            q: Optional[str] = Query(None, description="标题/正文包含该串"),
            source: Optional[str] = Query(None),
            license: Optional[str] = Query(None, description="按授权过滤，如 MIT / PD-US"),
            share_alike: Optional[bool] = Query(None, description="false = 排除 CC-BY-SA 类"),
            commercial_ok: Optional[bool] = Query(None)):
        s = _script(script)
        pq = PoemQuery(dynasty=dynasty, genre=genre, form=form, tune=tune, author=author,
                       q=q, source=source, license=license, share_alike=share_alike,
                       commercial_ok=commercial_ok, limit=1, order="random")
        p = store.random_poem(pq, s)
        if not p:
            raise HTTPException(404, detail={
                "code": "no_match", "message": "没有符合条件的作品",
                "hint": "检查 dynasty/genre 的名字拼写；genre 与 dynasty 是两个独立维度，"
                        "可以只给一个"})
        return p

    @r.get("/poems", summary="作品列表", tags=["作品"],
           response_model=Page[Poem])
    def poems(
            script: str = Query("zh-Hans"),
            dynasty: Optional[str] = Query(None, description="朝代名，如 唐"),
            genre: Optional[str] = Query(None, description="体裁名，如 词（与 dynasty 正交，可单用）"),
            form: Optional[str] = Query(None, description="形式名，如 七言律诗"),
            tune: Optional[str] = Query(None, description="词牌/曲牌，如 念奴娇"),
            author: Optional[str] = Query(None, description="作者名或别名"),
            q: Optional[str] = Query(None, description="标题/正文包含（想按相关性排序用 /search）"),
            source: Optional[str] = Query(None),
            license: Optional[str] = Query(None, description="按授权过滤，如 MIT / PD-US / CC-BY-SA-4.0"),
            share_alike: Optional[bool] = Query(None, description="false = 只要非 ShareAlike 的"),
            commercial_ok: Optional[bool] = Query(None, description="true = 只要可商用的"),
            after: Optional[str] = Query(None, description="游标：上一次返回的 next_cursor"),
            limit: Optional[int] = Query(None, description="每页条数，最大 200"),
            order: str = Query("id", pattern="^(id|random)$")):
        s = _script(script)
        pq = _poem_query(dynasty, genre, form, tune, author, q, source, license,
                         share_alike, commercial_ok, after, _limit(limit), order)
        return store.poems(pq, s)

    @r.get("/poems/{pid}", summary="按 id 取作品", tags=["作品"])
    def poem_one(pid: int = Path(..., ge=1), script: str = Query("zh-Hans"),
                 translations: bool = Query(True, description="是否带上译文")):
        s = _script(script)
        p = store.poem(pid, s, with_translations=translations)
        if not p:
            raise HTTPException(404, detail={"code": "not_found",
                                             "message": f"没有 id={pid} 的作品"})
        return p

    @r.get("/poems/{pid}/translations", summary="作品的译文", tags=["作品"])
    def poem_translations(pid: int = Path(..., ge=1), script: str = Query("zh-Hans")):
        s = _script(script)
        if not store.poem(pid, s, with_translations=False):
            raise HTTPException(404, detail={"code": "not_found",
                                             "message": f"没有 id={pid} 的作品"})
        return {"poem_id": pid, "items": store.translations(pid)}

    # ============================================================ 检索
    @r.get("/search", summary="全文检索（bm25 相关性）", tags=["检索"])
    def search(
            q: str = Query(..., min_length=1,
                           description="检索词。3 个字以上走全文索引（按相关性排序、带高亮片段）；"
                                       "1–2 个字自动改用子串匹配（trigram 索引按 3 字切分，"
                                       "短词匹配不到，接口会替你换路，不会给你一个假的「0 结果」）"),
            script: str = Query("zh-Hans"),
            dynasty: Optional[str] = Query(None), genre: Optional[str] = Query(None),
            form: Optional[str] = Query(None), tune: Optional[str] = Query(None),
            author: Optional[str] = Query(None),
            license: Optional[str] = Query(None), share_alike: Optional[bool] = Query(None),
            limit: int = Query(20, ge=1), page: int = Query(1, ge=1)):
        s = _script(script)
        if limit > settings.search_page_max:
            _bad(f"检索每页最多 {settings.search_page_max} 条")
        if (page - 1) * limit > settings.search_offset_max:
            _bad(f"检索最多翻到第 {settings.search_offset_max // limit + 1} 页 —— "
                 "更深的翻页请改用 dynasty/genre 等条件缩小范围（bm25 排序没有稳定游标）")
        hits, total, used_fts, note = store.search(q, s, PoemQuery(
            dynasty=dynasty, genre=genre, form=form, tune=tune, author=author,
            license=license, share_alike=share_alike), limit, page)
        return {"q": q, "script": script, "page": page, "limit": limit,
                "total": total, "count": len(hits),
                "engine": "fts5-bm25" if used_fts else "like-fallback",
                "note": note or None,
                "items": [h.model_dump() for h in hits]}

    # ============================================================ 作者
    @r.get("/authors/resolve", summary="别名解析（陶渊明 → 陶潜）", tags=["作者"])
    def authors_resolve(name: str = Query(..., description="想查的名字，可以是本名/字/号/别称"),
                        script: str = Query("zh-Hans")):
        s = _script(script)
        ids = store.author_ids(name, s)
        items = [store.author(i, s) for i in ids]
        items = [a for a in items if a]
        return {"query": name, "count": len(items), "items": items,
                "note": None if items else
                        "查不到 —— 注意「搜不到」和「不存在」是两件事，"
                        "可以试字/号（杜甫 → 杜工部）"}

    @r.get("/authors", summary="作者列表", tags=["作者"], response_model=Page[Author])
    def authors(
            script: str = Query("zh-Hans"),
            q: Optional[str] = Query(None, description="名字或别名包含（陶渊明 能命中 陶潜）"),
            dynasty: Optional[str] = Query(None),
            after: Optional[str] = Query(None),
            limit: Optional[int] = Query(None)):
        s = _script(script)
        return store.authors(AuthorQuery(q=q, dynasty=dynasty, after=_cursor(after),
                                         limit=_limit(limit)), s)

    @r.get("/authors/{aid}", summary="作者详情", tags=["作者"])
    def author_one(aid: int = Path(..., ge=1), script: str = Query("zh-Hans")):
        s = _script(script)
        a = store.author(aid, s)
        if not a:
            raise HTTPException(404, detail={"code": "not_found",
                                             "message": f"没有 id={aid} 的作者"})
        return a

    # ============================================================ 世界诗歌
    @r.get("/world", summary="非中文原文作品", tags=["世界诗歌"])
    def world(lang: Optional[str] = Query(None, description="原文语言，如 en / fa / ja"),
              after: Optional[str] = Query(None), limit: Optional[int] = Query(None)):
        if not store.caps.world:
            raise HTTPException(409, detail={
                "code": "capability_missing",
                "message": "这个库里还没有世界诗歌（poems_world 为空）",
                "hint": "schema 已就位（poems_world + poem_texts），等收录数据"})
        return store.world_poems(lang, _limit(limit), _cursor(after))

    return r
