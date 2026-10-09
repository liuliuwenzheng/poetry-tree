# -*- coding: utf-8 -*-
"""存储层 —— 领域模型 ↔ 数据库之间的**唯一**翻译点。

这一层存在的理由：**表现层（REST/GraphQL）一行 SQL 都不该写**。
两个协议必须走同一套查询语义，否则 REST 和 GraphQL 迟早答得不一样 ——
那是接口设计里最难查的一类 bug。

约定：
* 连接是**只读**的（`mode=ro`），接口不可能写坏数据；
* 每个线程一个连接（FastAPI 的同步端点跑在线程池里）；
* 能力探测（`Capabilities`）：老库没有 `genre_id` / FTS / 别名表也能跑，
  只是对应功能降级 —— 而不是直接报错。
"""
import base64
import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from api.models import (Author, AuthorRef, Dynasty, FilterError, Form, Genre,
                        Page, Poem, Rights, SearchHit, Stats, Translation, Tune)

try:
    import zhconv
except ImportError:                    # 没装也能跑，只是不认跨文字的名字
    zhconv = None


def _to_hans(s: str) -> str:
    """归一化文字，用于「用户写简体、库是繁体」这类查找。"""
    if not s:
        return ""
    return zhconv.convert(s, "zh-hans") if zhconv else s


@dataclass
class PoemQuery:
    """作品查询条件。**全部用人类可读的名字**，不是 id。

    名字过滤在 storage 里解析（且认简繁两种写法），
    这样 `?genre=词` 和 `?genre=詞` 都能用 —— 接口不该逼调用方记住库用哪个文字版本。
    """

    dynasty: Optional[str] = None
    genre: Optional[str] = None
    form: Optional[str] = None
    tune: Optional[str] = None
    author: Optional[str] = None
    q: Optional[str] = None                    # 标题/正文包含
    source: Optional[str] = None
    license: Optional[str] = None              # 按授权过滤（政策明确要求的能力）
    share_alike: Optional[bool] = None         # false = 排除 CC-BY-SA 类
    commercial_ok: Optional[bool] = None
    after: Optional[int] = None                # 游标
    limit: int = 20
    order: str = "id"                          # id | random


@dataclass
class AuthorQuery:
    q: Optional[str] = None
    dynasty: Optional[str] = None
    after: Optional[int] = None
    limit: int = 20


@dataclass
class Capabilities:
    """这个库支持哪些能力。用来在旧库上优雅降级。"""

    genres: bool = False            # genres_* 表 + poems.genre_id
    tune: bool = False              # poems.tune
    fts: set = field(default_factory=set)      # 有 FTS 的语言
    aliases: bool = False           # author_aliases 表
    world: bool = False             # poems_world 有数据
    texts: bool = False             # poem_texts 有数据
    note: list = field(default_factory=list)


def enc_cursor(pid: int) -> str:
    return base64.urlsafe_b64encode(str(pid).encode()).decode().rstrip("=")


def dec_cursor(cur: Optional[str]) -> Optional[int]:
    if not cur:
        return None
    try:
        pad = "=" * (-len(cur) % 4)
        return int(base64.urlsafe_b64decode(cur + pad).decode())
    except Exception:
        raise ValueError(f"游标格式不对：{cur!r}（用上一次返回的 next_cursor，别自己拼）")


class SqliteStore:
    """SQLite 实现。将来要换 Postgres，照这个接口再写一个类即可。"""

    def __init__(self, settings, registry):
        self.s = settings
        self.reg = registry
        self._local = threading.local()
        self.caps = self._detect()
        # 库里的 source 值 → 权利块（启动时算一次，之后零成本）
        self._src_rights: dict[str, Optional[Rights]] = {
            src: registry.get(src) for src in self.db_sources()
        }

    # ------------------------------------------------------------ 连接
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "con", None)
        if c is None:
            c = sqlite3.connect(self.s.db_uri, uri=True, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=1")
            self._local.con = c
        return c

    def close(self):
        c = getattr(self._local, "con", None)
        if c:
            c.close()
            self._local.con = None

    def _rows(self, sql, args=()):
        return self._conn().execute(sql, args).fetchall()

    def _one(self, sql, args=()):
        return self._conn().execute(sql, args).fetchone()

    def _scalar(self, sql, args=(), default=0):
        r = self._one(sql, args)
        return (r[0] if r and r[0] is not None else default)

    # ------------------------------------------------------------ 能力探测
    def _objects(self) -> set:
        return {r[0] for r in self._rows("SELECT name FROM sqlite_master")}

    def _cols(self, table: str) -> set:
        try:
            return {r[1] for r in self._rows(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            return set()

    def _detect(self) -> Capabilities:
        objs = self._objects()
        caps = Capabilities()
        has_genres = "genres_zh_hans" in objs
        cols = self._cols("poems_zh_hans")
        caps.genres = has_genres and "genre_id" in cols
        caps.tune = "tune" in cols
        caps.fts = {l for l in ("zh_hans", "zh_hant") if f"poems_fts_{l}" in objs}
        caps.aliases = "author_aliases" in objs
        try:
            caps.world = self._scalar("SELECT count(*) FROM poems_world") > 0
        except sqlite3.OperationalError:
            caps.world = False
        try:
            caps.texts = self._scalar("SELECT count(*) FROM poem_texts") > 0
        except sqlite3.OperationalError:
            caps.texts = False
        if not caps.genres:
            caps.note.append("这个库没有体裁轴（旧模型）—— `genre` 过滤不可用")
        if not caps.fts:
            caps.note.append("这个库没有全文索引 —— 先跑 tools/build_search.py")
        if not caps.aliases:
            caps.note.append("这个库没有别名表 —— 「陶渊明」查不到「陶潜」")
        return caps

    def db_sources(self) -> list[str]:
        """库里实际出现的 `source` 原值（如 `chinese-poetry`、`werneror`）。"""
        try:
            return [r[0] for r in self._rows(
                "SELECT DISTINCT source FROM poems_zh_hans WHERE source IS NOT NULL")]
        except sqlite3.OperationalError:
            return []

    def source_keys_in_db(self) -> list[str]:
        """库里实际出现的来源，**解析成登记表键**（`chinese-poetry` → `chinese_poetry`）。

        别直接用 `db_sources()` 去比登记表的键 —— 那是两个不同的命名空间
        （库值带连字符、登记键带下划线），比出来永远是「一个都没有」，
        于是 `in_db` 全为 false、「哪些来源真有数据」这件事就再也说不清了。
        """
        return sorted({k for k in (self.reg.resolve_key(s) for s in self.db_sources()) if k})

    def rights_of(self, source: str) -> Optional[Rights]:
        return self._src_rights.get(source)

    # ------------------------------------------------------------ 维度：三轴
    def _map_rows(self, table: str, script: str):
        """把 `{概念}_{script}` 表读成 (id, name) 列表。"""
        t = f"{table}_{script}"
        try:
            return self._rows(f"SELECT id, name FROM {t} ORDER BY id")
        except sqlite3.OperationalError:
            return []

    def dynasties(self, script: str, with_counts: bool = False) -> list[Dynasty]:
        out = []
        for r in self._rows(f"""SELECT id,name,name_en,start_year,end_year
                                FROM dynasties_{script} ORDER BY id"""):
            d = Dynasty(id=r["id"], name=r["name"], name_en=r["name_en"],
                        start_year=r["start_year"], end_year=r["end_year"])
            out.append(d)
        if with_counts:
            cnt = dict(self._rows(
                f"SELECT dynasty_id, count(*) FROM poems_{script} GROUP BY dynasty_id"))
            for d in out:
                d.poem_count = cnt.get(d.id, 0)
        return out

    def genres(self, script: str, with_counts: bool = False) -> list[Genre]:
        if not self.caps.genres:
            return []
        out = []
        for r in self._rows(f"""SELECT id,name,name_en,description
                                FROM genres_{script} ORDER BY id"""):
            out.append(Genre(id=r["id"], name=r["name"], name_en=r["name_en"],
                             description=r["description"]))
        if with_counts:
            cnt = dict(self._rows(
                f"SELECT genre_id, count(*) FROM poems_{script} GROUP BY genre_id"))
            for g in out:
                g.poem_count = cnt.get(g.id, 0)
        return out

    def forms(self, script: str) -> list[Form]:
        gname = {g.id: g.name for g in self.genres(script)} if self.caps.genres else {}
        out = []
        for r in self._rows(f"""SELECT id,name,category,lines,chars_per_line,description
                                FROM poetry_types_{script} ORDER BY id"""):
            gid = None
            if self.caps.genres:
                # poetry_types.category 在 v0.3.0 起已不是可信体裁，
                # 靠「形式名 → 体裁」的反查表（下面 _form_genre_map）来定
                gid = self._form_genre_map(script).get(r["id"])
            out.append(Form(id=r["id"], name=r["name"], genre_id=gid,
                            genre=gname.get(gid), lines=r["lines"],
                            chars_per_line=r["chars_per_line"],
                            description=r["description"]))
        return out

    def _form_genre_map(self, script: str) -> dict:
        """形式 id → 体裁 id。**从数据里反查**（哪个形式实际挂在哪个体裁下），
        比读 `category` 可靠 —— `category` 在旧模型里写着「唐诗」这种被压扁的维度。
        """
        if not self.caps.genres:
            return {}
        cache = getattr(self, "_fgcache", None)
        if cache is None:
            cache = self._fgcache = {}
        if script not in cache:
            best: dict[int, tuple[int, int]] = {}
            for r in self._rows(f"""SELECT type_id, genre_id, count(*) n FROM poems_{script}
                                    WHERE type_id IS NOT NULL AND genre_id IS NOT NULL
                                    GROUP BY type_id, genre_id"""):
                tid, gid, n = r["type_id"], r["genre_id"], r["n"]
                if tid not in best or n > best[tid][1]:
                    best[tid] = (gid, n)
            cache[script] = {k: v[0] for k, v in best.items()}
        return cache[script]

    def tunes(self, script: str, genre: Optional[str] = None, q: Optional[str] = None,
              limit: int = 200, min_count: int = 1) -> list[Tune]:
        if not self.caps.tune:
            return []
        where, args = ["p.tune IS NOT NULL", "p.tune <> ''"], []
        if genre:
            gid = self._dim_id("genres", script, genre)
            if gid is None:
                return []
            where.append("p.genre_id = ?")
            args.append(gid)
        if q:
            where.append("p.tune LIKE ?")
            args.append(f"%{q}%")
        gsel = "g.name AS gname" if self.caps.genres else "NULL AS gname"
        gjoin = (f"LEFT JOIN genres_{script} g ON g.id = p.genre_id"
                 if self.caps.genres else "")
        rows = self._rows(f"""
            SELECT p.tune AS tune, {gsel}, count(*) AS n
            FROM poems_{script} p {gjoin}
            WHERE {' AND '.join(where)}
            GROUP BY p.tune, gname HAVING n >= ?
            ORDER BY n DESC, p.tune LIMIT ?""", tuple(args) + (min_count, limit))
        return [Tune(name=r["tune"], genre=r["gname"], count=r["n"]) for r in rows]

    # ------------------------------------------------------------ 名字 → id
    def _dim_cache(self, table: str, script: str) -> dict:
        key = (table, script)
        cache = getattr(self, "_dimcache", None)
        if cache is None:
            cache = self._dimcache = {}
        if key not in cache:
            by_name, by_norm = {}, {}
            for r in self._rows(f"SELECT id,name FROM {table}_{script}"):
                by_name[r["name"]] = r["id"]
                by_norm.setdefault(_to_hans(r["name"]), r["id"])
            cache[key] = (by_name, by_norm)
        return cache[key]

    def _dim_id(self, table: str, script: str, name: str) -> Optional[int]:
        """名字 → id。认简繁两种写法（库是繁体时也能用简体查）。"""
        if not name:
            return None
        by_name, by_norm = self._dim_cache(table, script)
        if name in by_name:
            return by_name[name]
        return by_norm.get(_to_hans(name))

    # ------------------------------------------------------------ 作者
    def author_ids(self, name: str, script: str) -> list[int]:
        """作者名 → id 列表。**先查别名** —— 「陶渊明」在库里叫「陶潜」。"""
        if not name:
            return []
        ids = [r["id"] for r in self._rows(
            f"SELECT id FROM authors_{script} WHERE name = ?", (name,))]
        if not ids:
            ids = [r["id"] for r in self._rows(
                f"SELECT id FROM authors_{script} WHERE name LIKE ? LIMIT 50",
                (f"%{name}%",))]
        if self.caps.aliases:
            # 别名表里可能同时有简繁两条，lang 不用限定
            for r in self._rows("SELECT DISTINCT author_id FROM author_aliases "
                                "WHERE alias = ?", (name,)):
                if r["author_id"] not in ids:
                    ids.append(r["author_id"])
        return ids

    def _aliases_of(self, author_id: int) -> list[str]:
        if not self.caps.aliases:
            return []
        return [r[0] for r in self._rows(
            "SELECT DISTINCT alias FROM author_aliases WHERE author_id = ? "
            "ORDER BY alias", (author_id,))]

    def author(self, aid: int, script: str) -> Optional[Author]:
        r = self._one(f"""SELECT a.*, d.name AS dyn_name, d.name_en AS dyn_en,
                                 d.start_year, d.end_year
                          FROM authors_{script} a
                          LEFT JOIN dynasties_{script} d ON d.id = a.dynasty_id
                          WHERE a.id = ?""", (aid,))
        if not r:
            return None
        return self._author_from_row(r, script)

    def _author_from_row(self, r, script) -> Author:
        dyn = None
        if r["dyn_name"]:
            dyn = Dynasty(id=r["dynasty_id"], name=r["dyn_name"],
                          name_en=r["dyn_en"] if "dyn_en" in r.keys() else None,
                          start_year=r["start_year"] if "start_year" in r.keys() else None,
                          end_year=r["end_year"] if "end_year" in r.keys() else None)
        n = self._scalar(f"SELECT count(*) FROM poems_{script} WHERE author_id = ?",
                         (r["id"],))
        return Author(id=r["id"], name=r["name"],
                      name_en=r["name_en"], name_orig=r["name_orig"],
                      dynasty=dyn, description=r["description"], source=r["source"],
                      poem_count=n, aliases=self._aliases_of(r["id"]))

    def authors(self, q: AuthorQuery, script: str) -> Page[Author]:
        where, args = [], []
        if q.q:
            ids = None
            if self.caps.aliases:
                rows = self._rows("SELECT DISTINCT author_id FROM author_aliases "
                                  "WHERE alias LIKE ? LIMIT 200", (f"%{q.q}%",))
                ids = [r[0] for r in rows]
            cond = f"(a.name LIKE ?)"
            args.append(f"%{q.q}%")
            if ids:
                cond = f"(a.name LIKE ? OR a.id IN ({','.join('?' * len(ids))}))"
                args += ids
            where.append(cond)
        if q.dynasty:
            did = self._dim_id("dynasties", script, q.dynasty)
            if did is None:
                return Page(items=[], total=0, has_more=False)
            where.append("a.dynasty_id = ?")
            args.append(did)
        if q.after:
            where.append("a.id > ?")
            args.append(q.after)
        w = " AND ".join(where) if where else "1=1"
        total = self._scalar(f"SELECT count(*) FROM authors_{script} a WHERE {w}", args)
        rows = self._rows(f"""
            SELECT a.*, d.name AS dyn_name, d.name_en AS dyn_en,
                   d.start_year, d.end_year
            FROM authors_{script} a
            LEFT JOIN dynasties_{script} d ON d.id = a.dynasty_id
            WHERE {w} ORDER BY a.id LIMIT ?""", tuple(args) + (q.limit + 1,))
        has_more = len(rows) > q.limit
        items = [self._author_from_row(r, script) for r in rows[:q.limit]]
        nxt = enc_cursor(items[-1].id) if has_more and items else None
        return Page(items=items, total=total, next_cursor=nxt, has_more=has_more)

    # ------------------------------------------------------------ 作品
    def _poem_select(self, script: str, extra: str = "") -> str:
        """作品表格的 SELECT。

        `extra` 会追加到列清单末尾 —— 检索要加 `snippet()` / `bm25()` 就是靠它。
        **刻意让检索复用这一段**，而不是自己再写一份列清单：
        重复的列清单一定会漂移（早先检索那份就漏了 genre_id，把体裁身份变成了常量 0）。
        """
        g = (", g.name AS genre_name, g.name_en AS genre_en, g.description AS genre_desc"
             if self.caps.genres else ", NULL AS genre_name, NULL AS genre_en, NULL AS genre_desc")
        t = ", p.tune AS tune" if self.caps.tune else ", NULL AS tune"
        gid = ", p.genre_id AS genre_id" if self.caps.genres else ", NULL AS genre_id"
        return f"""
            SELECT p.id, p.title, p.content, p.type_id, p.dynasty_id,
                   p.author_id, p.source, p.period_orig{t}{gid}
                   {g},
                   d.name AS dyn_name, d.name_en AS dyn_en, d.start_year, d.end_year,
                   f.name AS form_name, f.lines AS form_lines,
                   f.chars_per_line AS form_cpl, f.description AS form_desc,
                   a.name AS author_name{extra}
            FROM poems_{script} p
            LEFT JOIN dynasties_{script} d ON d.id = p.dynasty_id
            LEFT JOIN poetry_types_{script} f ON f.id = p.type_id
            LEFT JOIN authors_{script} a ON a.id = p.author_id
            {'LEFT JOIN genres_' + script + ' g ON g.id = p.genre_id' if self.caps.genres else ''}
        """

    def _poem_from_row(self, r, script: str, with_translations: bool = False) -> Poem:
        content = json.loads(r["content"]) if r["content"] else []
        poem = Poem(
            id=r["id"], title=r["title"] or "", content=content,
            text="".join(str(x) for x in content), script=_script_tag(script),
            dynasty=(Dynasty(id=r["dynasty_id"], name=r["dyn_name"], name_en=r["dyn_en"],
                             start_year=r["start_year"], end_year=r["end_year"])
                     if r["dyn_name"] else None),
            genre=(Genre(id=r["genre_id"] or 0, name=r["genre_name"], name_en=r["genre_en"],
                         description=r["genre_desc"]) if r["genre_name"] else None),
            form=(Form(id=r["type_id"], name=r["form_name"], lines=r["form_lines"],
                       chars_per_line=r["form_cpl"], description=r["form_desc"])
                  if r["form_name"] else None),
            tune=r["tune"],
            author=(AuthorRef(id=r["author_id"], name=r["author_name"])
                    if r["author_name"] else None),
            source=r["source"], period_orig=r["period_orig"],
            rights=self.rights_of(r["source"]),
        )
        if with_translations:
            poem.translations = self.translations(poem.id)
        return poem

    def poem(self, pid: int, script: str, with_translations: bool = True) -> Optional[Poem]:
        r = self._one(self._poem_select(script) + " WHERE p.id = ?", (pid,))
        return self._poem_from_row(r, script, with_translations) if r else None

    def translations(self, pid: int) -> list[Translation]:
        if not self.caps.texts:
            return []
        out = []
        for r in self._rows("""SELECT lang,title,content,translator,source
                               FROM poem_texts WHERE poem_kind IN ('zh','zh_hans','zh_hant')
                               AND poem_id = ? ORDER BY lang""", (pid,)):
            out.append(Translation(
                lang=r["lang"], title=r["title"],
                content=json.loads(r["content"]) if r["content"] else [],
                translator=r["translator"], source=r["source"],
                rights=self.rights_of(r["source"]) if r["source"] else None))
        return out

    def _license_source_filter(self, q: PoemQuery) -> Optional[list[str]]:
        """按授权过滤 → 命中的库内 source 值列表。None = 不加这个条件。"""
        if q.license is None and q.share_alike is None and q.commercial_ok is None:
            return None
        hit = []
        for src, r in self._src_rights.items():
            if r is None:
                continue
            if q.license is not None and (r.license or "").upper() != q.license.upper():
                continue
            if q.share_alike is not None and r.share_alike != q.share_alike:
                continue
            if q.commercial_ok is not None and r.commercial_ok != q.commercial_ok:
                continue
            hit.append(src)
        return hit

    def _dim_names(self, table: str, script: str) -> list[str]:
        by_name, _ = self._dim_cache(table, script)
        return sorted(by_name)

    def _unknown_axis(self, axis: str, value: str, table: str,
                      script: str) -> FilterError:
        """轴上的名字认不出来 —— 抛错，绝不给一个骗人的空结果。

        `_dim_id` 认不出时返回 None，上层原本当成「没有匹配的作品」返回空页。
        于是**「名字打错了」和「这个组合真的没有作品」在接口看来一模一样**。
        实测踩到过：Windows 上的 curl 把中文按本地代码页发出去，服务端收到乱码，
        `genre=词` 返回 **0 条** —— 用户看到的是「一首宋词都没有」。

        所以这里必须分清：**值不存在 → 报错；值存在但组合为空 → 空页。**
        """
        names = self._dim_names(table, script)
        shown = names[:40]
        if "\ufffd" in value:
            # 参数里已经出现替换字符（U+FFFD）＝ 送进来的根本不是合法 UTF-8。
            # 这时候谈「拼写」是答错方向：100% 是编码问题。
            return FilterError(
                f"{axis} 参数没能正确解码 —— 送进来的是非法 UTF-8 字节",
                axis=axis, value=value, valid=shown,
                hint="这不是拼写问题，是编码问题。请以 UTF-8 发送中文并做 URL 编码"
                     "（如 genre=%E8%AF%8D）。Windows 上的 curl 会按本地代码页（GBK）"
                     "编码参数，这是最常见的成因 —— 换 Python/JS 客户端，"
                     "或显式写 %-编码。")
        tail = (f"（共 {len(names)} 个，此处列前 {len(shown)} 个）"
                if len(names) > len(shown) else "")
        return FilterError(
            f"{axis}「{value}」不在这份数据里",
            axis=axis, value=value, valid=shown,
            hint=f"可选值{tail}：{'、'.join(shown)}。"
                 f"另外注意中文参数需要 URL 编码 —— curl 在 Windows 上会按本地代码页"
                 f"发送中文，导致参数变成乱码、结果为空；"
                 f"请用显式 %-编码（如 genre=%E8%AF%8D）或 Python/JS 客户端。",
        )

    def _tune_exists(self, tune: str, script: str) -> bool:
        # tune 上有索引，这条很便宜（不用全表扫）
        return bool(self._rows(f"SELECT 1 FROM poems_{script} WHERE tune = ? LIMIT 1",
                              (tune,)))

    def _poem_where(self, q: PoemQuery, script: str):
        where, args = [], []
        if q.dynasty:
            did = self._dim_id("dynasties", script, q.dynasty)
            if did is None:
                raise self._unknown_axis("dynasty", q.dynasty, "dynasties", script)
            where.append("p.dynasty_id = ?")
            args.append(did)
        if q.genre:
            if not self.caps.genres:
                raise FilterError("这个库没有体裁轴，无法按 genre 过滤",
                                  axis="genre", value=q.genre,
                                  hint="用百川 v0.3.0+ 的库（含 genres_* 表）")
            gid = self._dim_id("genres", script, q.genre)
            if gid is None:
                raise self._unknown_axis("genre", q.genre, "genres", script)
            where.append("p.genre_id = ?")
            args.append(gid)
        if q.form:
            fid = self._dim_id("poetry_types", script, q.form)
            if fid is None:
                raise self._unknown_axis("form", q.form, "poetry_types", script)
            where.append("p.type_id = ?")
            args.append(fid)
        if q.tune:
            if not self.caps.tune:
                raise FilterError("这个库没有词牌列，无法按 tune 过滤",
                                  axis="tune", value=q.tune,
                                  hint="用百川 v0.3.0+ 的库（含 tune 列）")
            if not self._tune_exists(q.tune, script):
                raise FilterError(
                    f"词牌「{q.tune}」在这份数据里没有出现过",
                    axis="tune", value=q.tune,
                    hint="可用词牌见 /api/v1/meta/tunes —— 词牌有上千个，"
                         "这里不列了；注意中文参数需要 URL 编码")
            where.append("p.tune = ?")
            args.append(q.tune)
        if q.author:
            ids = self.author_ids(q.author, script)
            if not ids:
                raise FilterError(
                    f"没有这个作者：{q.author}", axis="author", value=q.author,
                    hint="库里按**本名**存储（陶渊明→陶潜、李后主→李煜、"
                         "苏东坡→苏轼、唐伯虎→唐寅、郑板桥→郑燮）。"
                         "用 /api/v1/authors/resolve?name=… 解析别名，"
                         "或 /api/v1/authors 看库里怎么写的。"
                         "另注意中文参数需要 URL 编码")
            where.append(f"p.author_id IN ({','.join('?' * len(ids))})")
            args += ids
        if q.q:
            where.append("(p.title LIKE ? OR p.content LIKE ?)")
            args += [f"%{q.q}%", f"%{q.q}%"]
        if q.source:
            where.append("p.source = ?")
            args.append(q.source)
        lf = self._license_source_filter(q)
        if lf is not None:
            if not lf:
                return None, []
            where.append(f"p.source IN ({','.join('?' * len(lf))})")
            args += lf
        if q.after:
            where.append("p.id > ?")
            args.append(q.after)
        return (" AND ".join(where) if where else "1=1"), args

    def poems(self, q: PoemQuery, script: str) -> Page[Poem]:
        w, args = self._poem_where(q, script)
        if w is None:
            return Page(items=[], total=0, has_more=False)
        total = self._scalar(f"SELECT count(*) FROM poems_{script} p WHERE {w}", args)
        order = "p.id" if q.order != "random" else "RANDOM()"
        rows = self._rows(self._poem_select(script) +
                          f" WHERE {w} ORDER BY {order} LIMIT ?",
                          tuple(args) + (q.limit + 1,))
        has_more = len(rows) > q.limit
        items = [self._poem_from_row(r, script) for r in rows[:q.limit]]
        if q.order == "random":
            has_more, nxt = False, None
        else:
            nxt = enc_cursor(items[-1].id) if has_more and items else None
        return Page(items=items, total=total, next_cursor=nxt, has_more=has_more)

    def random_poem(self, q: PoemQuery, script: str) -> Optional[Poem]:
        q2 = PoemQuery(**{**q.__dict__, "order": "random", "after": None, "limit": 1})
        page = self.poems(q2, script)
        return page.items[0] if page.items else None

    # ------------------------------------------------------------ 全文检索
    # trigram 分词的最小匹配长度：少于 3 个字，FTS **一定**匹配不到。
    # 那不是「没有结果」，是「这条路根本走不通」——必须换 LIKE 再查，
    # 否则用户搜「明月」会看到 0 结果，而库里其实有几千首。这是骗人的。
    TRIGRAM_MIN = 3

    def search(self, term: str, script: str, q: Optional[PoemQuery] = None,
               limit: int = 20, page: int = 1,
               ) -> tuple[list[SearchHit], int, bool, str]:
        """检索。返回 `(命中, 总数, 是否用了 FTS, 说明)`。

        **自动选路**：

        * 词长 ≥ 3 且库里有索引 → FTS5 + bm25（快，带高亮片段，按相关性排序）
        * 词长 < 3（trigram 走不通）→ LIKE 子串扫描（慢一点，但结果完整）
        * FTS 查询串语法错误（用户输了引号/星号/冒号）→ 也退回 LIKE，
          不把 500 丢给用户
        * 库里没索引 → LIKE

        `说明` 会原样出现在响应里，调用方有权知道这次走的是哪条路。
        """
        q = q or PoemQuery()
        term = (term or "").strip()
        if not term:
            return [], 0, False, "检索词为空"

        if script in self.caps.fts and len(term) >= self.TRIGRAM_MIN:
            try:
                hits, total = self._search_fts(term, script, q, limit, page)
                return hits, total, True, ""
            except sqlite3.OperationalError as e:
                # 例：q='"明月' 或 q='明月*' 会让 FTS 的查询语法解析失败。
                # 这不是用户的错，我们换条路把结果给他。
                hits, total = self._search_like(term, script, q, limit, page)
                return hits, total, False, (
                    f"检索词里有全文索引不接受的符号（{e}），已改用子串匹配；"
                    f"结果同样完整，只是**按 id 排序、没有相关性排序**（score 为空）")

        reason = ("本库没有全文索引" if script not in self.caps.fts
                  else f"检索词只有 {len(term)} 个字，而全文索引按 3 字切分"
                       f"（trigram），少于 3 字匹配不到")
        hits, total = self._search_like(term, script, q, limit, page)
        # 降级路径用 LIKE，**没有相关性排序**（SQLite 的 LIKE 无评分函数），
        # 结果按 id 排、score 为 None。这句必须写在 note 里：
        # 调用方看到「第一条不是最相关的」时，得能立刻分清是「库/接口坏了」
        # 还是「你走的是降级路径」。**降级可以，但不能降级得不知不觉。**
        return hits, total, False, (
            f"{reason}，已改用子串匹配；结果完整，只是慢一些。"
            f"注意：此路径**按 id 排序、没有相关性排序**（score 为空），"
            f"想要相关性排序请建全文索引（tools/build_search.py）")

    def _search_fts(self, term: str, script: str, q: PoemQuery,
                    limit: int, page: int) -> tuple[list[SearchHit], int]:
        """FTS5 bm25 相关性检索。

        两条 SQLite 的硬规矩（都是踩过才知道的）：

        1. `bm25()` / `snippet()` 只能作用于**本查询 FROM 里**的 FTS 表 ——
           把 FTS 表塞进子查询、外层再调 `bm25()`，会报
           `no such column: poems_fts_xxx`。所以这里用 `JOIN`。
        2. `MATCH` 左边只能用 FTS 表的**全名**，给它起别名会报
           `no such column: <别名>`。所以 `{f}` 一律写全名，不起别名。

        **用 offset 翻页**而不是游标：bm25 排序下没有稳定的游标可传。
        """
        f = f"poems_fts_{script}"
        w, args = self._poem_where(q, script)
        if w is None:
            return [], 0
        join = f"JOIN {f} ON {f}.rowid = p.id"
        where = f"{f} MATCH ? AND ({w})"
        total = self._scalar(
            f"SELECT count(*) FROM poems_{script} p {join} WHERE {where}",
            [term] + list(args))
        offset = (page - 1) * limit
        rows = self._rows(
            self._poem_select(
                script,
                f", snippet({f}, 1, '[', ']', '…', 14) AS snip,"
                f" bm25({f}, 8.0, 1.0) AS score")
            + f" {join} WHERE {where} ORDER BY score LIMIT ? OFFSET ?",
            tuple([term] + list(args) + [limit, offset]))
        hits = [SearchHit(poem=self._poem_from_row(r, script),
                          snippet=r["snip"], score=round(r["score"], 3)) for r in rows]
        return hits, total

    def _search_like(self, term, script, q, limit, page) -> tuple[list[SearchHit], int]:
        q2 = PoemQuery(**{**q.__dict__, "q": term})
        w, args = self._poem_where(q2, script)
        if w is None:
            return [], 0
        total = self._scalar(f"SELECT count(*) FROM poems_{script} p WHERE {w}", args)
        offset = (page - 1) * limit
        rows = self._rows(self._poem_select(script) + f" WHERE {w} ORDER BY p.id LIMIT ? OFFSET ?",
                          tuple(args) + (limit, offset))
        return [SearchHit(poem=self._poem_from_row(r, script), snippet=None, score=None)
                for r in rows], total

    # ------------------------------------------------------------ 统计
    def stats(self, script: str) -> Stats:
        by_genre, by_dyn, by_src = {}, {}, {}
        if self.caps.genres:
            by_genre = {r[0]: r[1] for r in self._rows(
                f"""SELECT g.name, count(*) FROM poems_{script} p
                    JOIN genres_{script} g ON g.id=p.genre_id GROUP BY g.id""")}
        by_dyn = {r[0]: r[1] for r in self._rows(
            f"""SELECT d.name, count(*) FROM poems_{script} p
                JOIN dynasties_{script} d ON d.id=p.dynasty_id GROUP BY d.id""")}
        by_src = {r[0]: r[1] for r in self._rows(
            f"SELECT source, count(*) FROM poems_{script} GROUP BY source")}
        return Stats(
            script=_script_tag(script),
            poems=self._scalar(f"SELECT count(*) FROM poems_{script}"),
            authors=self._scalar(f"SELECT count(*) FROM authors_{script}"),
            dynasties=len(by_dyn),
            genres=len(by_genre),
            tunes=(self._scalar(
                f"SELECT count(DISTINCT tune) FROM poems_{script} "
                f"WHERE tune IS NOT NULL AND tune<>''") if self.caps.tune else 0),
            by_genre=by_genre, by_dynasty=by_dyn, by_source=by_src)

    # ------------------------------------------------------------ 世界诗歌
    def world_poems(self, lang: Optional[str] = None, limit: int = 20,
                    after: Optional[int] = None) -> Page[dict]:
        if not self.caps.world:
            return Page(items=[], total=0, has_more=False)
        where, args = [], []
        if lang:
            where.append("p.lang_original = ?")
            args.append(lang)
        if after:
            where.append("p.id > ?")
            args.append(after)
        w = " AND ".join(where) if where else "1=1"
        total = self._scalar(f"SELECT count(*) FROM poems_world p WHERE {w}", args)
        rows = self._rows(f"""SELECT p.*, a.name_orig, a.name_zh, a.country
                              FROM poems_world p
                              LEFT JOIN authors_world a ON a.id = p.author_id
                              WHERE {w} ORDER BY p.id LIMIT ?""", tuple(args) + (limit + 1,))
        items = []
        for r in rows[:limit]:
            d = dict(r)
            d["content_orig"] = json.loads(d.get("content_orig") or "[]")
            d["rights"] = self.rights_of(d.get("source") or "")
            items.append(d)
        has_more = len(rows) > limit
        return Page(items=items, total=total,
                    next_cursor=enc_cursor(items[-1]["id"]) if has_more and items else None,
                    has_more=has_more)


def _script_tag(script: str) -> str:
    return "zh-Hant" if script.endswith("hant") else "zh-Hans"


SCRIPT_FROM_TAG = {"zh-hans": "zh_hans", "zh-hant": "zh_hant",
                   "hans": "zh_hans", "hant": "zh_hant", "simplified": "zh_hans",
                   "traditional": "zh_hant"}


def parse_script(v: Optional[str]) -> str:
    """`zh-Hans` / `hans` / `简体` … → 内部表后缀。`None`（就是没传）→ 默认简体。

    注意：空字符串**不是**「没传」。`?script=` 是客户端确实写错了，
    悄悄当默认用会把 bug 咽下去，所以这里报错。
    """
    if v is None:
        return "zh_hans"
    s = str(v).strip().lower()
    if not s:
        raise ValueError("script 不能是空字符串：要么不传，要么传 zh-Hans / zh-Hant")
    if s in SCRIPT_FROM_TAG:
        return SCRIPT_FROM_TAG[s]
    if "繁体" in s or "繁體" in s:
        return "zh_hant"
    if "简" in s or "簡" in s:
        return "zh_hans"
    raise ValueError("script 只能是 zh-Hans / zh-Hant")
