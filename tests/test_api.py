#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接口测试。

跑法（不需要先起服务，用 TestClient 直接打 app）：

    python -m tests.test_api          # 直接跑
    pytest tests/test_api.py -q       # 或用 pytest

测的东西分两类：

**A. 接口能用**（返回码、结构、错误形状）
**B. 架构不变量**（这才是重点）：
   - 朝代与体裁正交：`genre=词` 必须能跨朝代取到，`genre=诗&dynasty=唐` 必须是唐诗
   - 简繁同 id：同一个 id 在 zh-Hans / zh-Hant 下必须是**同一首诗**（作者/朝代/体裁一致）
   - 授权可见可筛：每条记录带 rights，`license` / `share_alike` 过滤生效
   - REST 与 GraphQL **答得一样**（两个协议共用 storage，不能各说各话）
   - 别名：搜「陶渊明」要能命中「陶潜」
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient      # noqa: E402

from api.app import create_app                 # noqa: E402
from api.config import Settings                # noqa: E402

PASS, FAIL = [], []


def check(cond, label, detail=""):
    (PASS if cond else FAIL).append(label)
    mark = "✅" if cond else "❌"
    print(f"  {mark} {label}" + (f"  —— {detail}" if detail else ""))
    return cond


def main():
    settings = Settings()
    if not os.path.exists(settings.db):
        sys.exit(f"找不到库：{settings.db}")
    app = create_app(settings, auto_audit=False)
    c = TestClient(app)

    # ============================================================ A 能用
    print("A. 基本可用性")
    r = c.get("/api/v1/")
    check(r.status_code == 200, "GET /api/v1/ 返回 200")
    info = r.json()
    check("capabilities" in info and "endpoints" in info, "服务信息含 capabilities/endpoints")
    check("授权不明" in info["policy"]["first_rule"], "服务信息里写着第一原则")
    caps = info["capabilities"]

    r = c.get("/api/v1/healthz")
    check(r.status_code == 200, "健康检查 200", r.json().get("status"))

    r = c.get("/api/v1/meta/dynasties")
    check(r.status_code == 200 and r.json()["items"], "朝代列表非空")
    r = c.get("/api/v1/meta/forms")
    check(r.status_code == 200 and r.json()["items"], "形式列表非空")
    r = c.get("/api/v1/meta/stats")
    st = r.json()
    check(r.status_code == 200 and st["poems"] > 0, "统计", f"{st['poems']:,} 首")

    # ---- 架构不变量：正交 ----
    print("\nB. 架构不变量")
    tang_shi = {"total": 0}          # 供后面 GraphQL 一致性用例使用
    if caps["genres"]:
        r = c.get("/api/v1/meta/genres")
        names = [g["name"] for g in r.json()["items"]]
        check(r.status_code == 200 and "词" in names, "体裁轴含「词」", "、".join(names))
        check(not any(("唐" in n or "宋" in n) for n in names),
              "体裁名里不含朝代（否则就是把两个维度压扁了）")

        # 唐诗 vs 宋词 —— 这才是「唐诗和宋词分开」的接口级证明
        tang_shi = c.get("/api/v1/poems", params={"genre": "诗", "dynasty": "唐", "limit": 1}).json()
        song_shi = c.get("/api/v1/poems", params={"genre": "诗", "dynasty": "宋", "limit": 1}).json()
        song_ci = c.get("/api/v1/poems", params={"genre": "词", "dynasty": "宋", "limit": 1}).json()
        qing_ci = c.get("/api/v1/poems", params={"genre": "词", "dynasty": "清", "limit": 1}).json()
        all_ci = c.get("/api/v1/poems", params={"genre": "词", "limit": 1}).json()
        check(tang_shi["total"] > 0 and song_shi["total"] > 0, "唐诗 / 宋诗 都能取到",
              f"唐 {tang_shi['total']:,} / 宋 {song_shi['total']:,}")
        check(song_ci["total"] > 0 and qing_ci["total"] > 0, "宋词 / 清词 都能取到",
              f"宋 {song_ci['total']:,} / 清 {qing_ci['total']:,}")
        # 「所有词」= 宋词 + 清词 + 其他朝代，必然大于任一单朝代
        check(all_ci["total"] > song_ci["total"] and all_ci["total"] > qing_ci["total"],
              "「所有词，不分朝代」比任一单朝代都多（正交生效）",
              f"全部 {all_ci['total']:,} > 宋 {song_ci['total']:,}")
        check(song_ci["total"] + qing_ci["total"] <= all_ci["total"],
              "宋词 + 清词 ≤ 所有词")
        # 取回的样本本身要自洽
        if tang_shi["items"]:
            p = tang_shi["items"][0]
            check(p["dynasty"]["name"] == "唐" and p["genre"]["name"] == "诗",
                  "唐+诗 过滤取回的样本自洽",
                  f"《{p['title']}》{p['dynasty']['name']}·{p['genre']['name']}")
        # 简繁两种写法的体裁名都能用
        r1 = c.get("/api/v1/poems", params={"genre": "词", "limit": 1}).json()
        r2 = c.get("/api/v1/poems", params={"genre": "詞", "limit": 1}).json()
        check(r1["total"] == r2["total"], "体裁名简繁两种写法等价（词 / 詞）",
              f"{r1['total']:,}")

    if caps["tune"]:
        r = c.get("/api/v1/meta/tunes", params={"limit": 5})
        check(r.status_code == 200 and r.json()["items"], "词牌列表非空",
              "、".join(t["name"] for t in r.json()["items"][:3]))
        sdg = c.get("/api/v1/poems", params={"tune": "水调歌头", "limit": 1}).json()
        check(sdg["total"] > 0, "按词牌过滤（跨朝代查《水调歌头》）", f"{sdg['total']:,} 首")

    # ---- 简繁同 id：接口层的核心保证 ----
    # 注意断言的是**身份**（id），不是**名字**：「诗」与「詩」不一样才是对的，
    # 拿整个嵌套对象比会把正确行为误报成 bug。
    probe = c.get("/api/v1/poems", params={"limit": 1}).json()["items"][0]
    pid = probe["id"]
    hans = c.get(f"/api/v1/poems/{pid}", params={"script": "zh-Hans"}).json()
    hant = c.get(f"/api/v1/poems/{pid}", params={"script": "zh-Hant"}).json()
    check(hans["id"] == hant["id"] == pid, f"同一 id ({pid}) 两种文字版本都能取到")
    same_identity = (hans["dynasty"]["id"] == hant["dynasty"]["id"]
                     and hans["genre"]["id"] == hant["genre"]["id"]
                     and hans["author"]["id"] == hant["author"]["id"]
                     and hans["source"] == hant["source"]
                     and hans.get("tune") == hant.get("tune"))
    check(same_identity, "同一 id 在简繁下指同一首诗（朝代/体裁/作者/来源的身份一致）")
    check(hans["script"] == "zh-Hans" and hant["script"] == "zh-Hant", "script 字段正确回填")

    # 名字必须跟着文字版本走 —— 这才是 script 参数存在的意义
    gh = {g["id"]: g["name"] for g in
          c.get("/api/v1/meta/genres", params={"script": "zh-Hans"}).json()["items"]}
    gt = {g["id"]: g["name"] for g in
          c.get("/api/v1/meta/genres", params={"script": "zh-Hant"}).json()["items"]}
    check(set(gh) == set(gt), "两文字版本的体裁 id 集合一致")
    diff = sorted((gh[i], gt[i]) for i in gh if gh[i] != gt[i])
    check(any(a == "诗" and b == "詩" for a, b in diff), "体裁名跟着文字版本变（诗 / 詩）",
          "；".join(f"{a}→{b}" for a, b in diff[:4]))

    # 等距抽 40 个 id 逐一对齐身份。
    # v0.2.0 就是在这里错开了 25,294 个 id —— 同一个 id 在两本字下不是同一首诗。
    import random as _rnd
    _rnd.seed(7)
    n_total = st["poems"]
    picks = sorted(_rnd.sample(range(1, n_total + 1), min(40, n_total)))
    mism = []
    for k in picks:
        a = c.get(f"/api/v1/poems/{k}", params={"script": "zh-Hans"}).json()
        b = c.get(f"/api/v1/poems/{k}", params={"script": "zh-Hant"}).json()
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        if (a["dynasty"]["id"] != b["dynasty"]["id"]
                or a["genre"]["id"] != b["genre"]["id"]
                or a["author"]["id"] != b["author"]["id"]
                or a["source"] != b["source"]):
            mism.append(k)
    check(not mism, f"随机抽 {len(picks)} 个 id：简繁身份逐一对齐（错位就报红）",
          f"不符 {len(mism)} 个" if mism else "0 个不符")

    # ---- 授权 ----
    check(bool(hans.get("rights")) and bool(hans["rights"].get("license")),
          "作品自带 rights 与 license",
          (hans.get("rights") or {}).get("license") or "（无）")
    check(hans["rights"].get("source_name") and hans["rights"].get("license_url"),
          "rights 含来源名与许可链接")
    so = c.get("/api/v1/sources").json()
    check(so["items"] and "in_db" in so, "来源登记表可查", f"{len(so['items'])} 个来源")
    check(any(s.get("blocked") for s in so["items"]),
          "被禁用的来源（GPL 那条）也在登记表里留档")
    # 用**库里真的有数据的来源**的许可来试。
    # 拿登记表里第一个许可去试会误报：wikisource 的 CC-BY-SA 登记了但还没入库，
    # 过滤出来 0 条是**对的** —— 断言写错了，不是数据错了。
    in_db = sorted({s["key"] for s in so["items"] if s.get("in_db")})
    lic_in_db = sorted({s["license"] for s in so["items"]
                        if s.get("in_db") and s.get("license")})
    check(bool(in_db), "登记表能区分「库里真有」和「只是登记了」", "、".join(in_db))
    for L in lic_in_db[:3]:
        f1 = c.get("/api/v1/poems", params={"license": L, "limit": 1}).json()
        check(f1["total"] > 0, f"按 license={L} 过滤有结果（该许可在库里有数据）",
              f"{f1['total']:,} 首")
    # 登记了但没入库的许可 → 过滤为 0，不是报错
    for s in so["items"]:
        if not s.get("in_db") and s.get("license") and not s.get("blocked"):
            f0 = c.get("/api/v1/poems", params={"license": s["license"], "limit": 1}).json()
            check(f0["total"] == 0, f"只登记未入库的许可 license={s['license']} → 0 条",
                  f"{f0['total']}")
            break
    # 被禁用的来源（GPL）绝不能出现在结果里
    blocked_lic = {s["license"] for s in so["items"] if s.get("blocked") and s.get("license")}
    for L in sorted(blocked_lic):
        fb = c.get("/api/v1/poems", params={"license": L, "limit": 1}).json()
        check(fb["total"] == 0, f"被禁用的来源许可 {L} 一条都不服务", f"{fb['total']} 条")
    # 政策要求的能力：排除 ShareAlike
    sa = c.get("/api/v1/poems", params={"share_alike": "false", "limit": 1}).json()
    allp = c.get("/api/v1/poems", params={"limit": 1}).json()
    check(sa["total"] <= allp["total"], "share_alike=false 能排除 ShareAlike 记录",
          f"{sa['total']:,} ≤ {allp['total']:,}")

    # ---- 别名 ----
    if caps["author_aliases"]:
        res = c.get("/api/v1/authors/resolve", params={"name": "陶渊明"}).json()
        check(res["count"] > 0 and res["items"][0]["name"] == "陶潜",
              "别名解析：陶渊明 → 陶潜", f"{res['count']} 个")
        byalias = c.get("/api/v1/poems", params={"author": "陶渊明", "limit": 1}).json()
        byname = c.get("/api/v1/poems", params={"author": "陶潜", "limit": 1}).json()
        check(byalias["total"] == byname["total"] and byalias["total"] > 0,
              "按别名查诗 == 按本名查诗", f"{byalias['total']:,} 首")
    else:
        print("     · 这个库没有别名表，跳过别名用例")

    # ---- 检索 ----
    # 2 字：trigram 索引按 3 字切分，走不通。接口必须**自动换路**并如实说明，
    # 而不是丢一个骗人的「0 结果」给用户。（这正是修过的坑，所以留成断言。）
    sr2 = c.get("/api/v1/search", params={"q": "明月", "limit": 3}).json()
    check(sr2["total"] > 0, "检索「明月」（2 字）有结果", f"{sr2['total']:,} 命中")
    check(sr2["engine"] == "like-fallback" and bool(sr2.get("note")),
          "2 字自动降级为子串匹配，并在 note 里说明为什么",
          (sr2.get("note") or "")[:40])
    # 3 字以上：走全文索引，带 bm25 与高亮片段
    sr3 = c.get("/api/v1/search", params={"q": "明月几时有", "limit": 3}).json()
    check(sr3["total"] > 0, "3 字以上检索命中", f"{sr3['total']:,} 命中")
    if caps["full_text_search"]:
        check(sr3["engine"] == "fts5-bm25", "库里有索引时走 fts5-bm25", sr3["engine"])
        check(bool(sr3["items"][0].get("snippet")), "返回高亮片段",
              (sr3["items"][0].get("snippet") or "")[:36])
        check(sr3["items"][0].get("score") is not None, "返回 bm25 相关性分数",
              str(sr3["items"][0].get("score")))
        # 名字出现在标题里的应该排在前面（title 权重 8.0）
        t0 = sr3["items"][0]["poem"]["title"]
        check("明月几时有" in t0 or "水调歌头" in t0, "相关性排序把最匹配的排在第一",
              f"《{t0[:26]}》")
    # 特殊符号不能让接口 500 —— 换条路把结果给出来
    scary = c.get("/api/v1/search", params={"q": '"明月*', "limit": 3})
    check(scary.status_code == 200, "检索词带引号/星号不 500（自动换路）",
          f"HTTP {scary.status_code} engine={(scary.json().get('engine') if scary.status_code == 200 else '-')}")
    # 检索也要能被过滤条件收窄
    scoped = c.get("/api/v1/search", params={"q": "明月几时有", "genre": "词", "limit": 3})
    check(scoped.status_code == 200 and scoped.json()["total"] <= sr3["total"],
          "检索可叠加 genre 等过滤", f"{scoped.json()['total']} ≤ {sr3['total']}")

    # ---- 分页 ----
    p1 = c.get("/api/v1/poems", params={"limit": 5}).json()
    check(len(p1["items"]) == 5 and p1["has_more"] and p1["next_cursor"],
          "游标分页：第一页 5 条 + next_cursor")
    p2 = c.get("/api/v1/poems", params={"limit": 5, "after": p1["next_cursor"]}).json()
    ids1 = {x["id"] for x in p1["items"]}
    ids2 = {x["id"] for x in p2["items"]}
    check(not (ids1 & ids2), "第二页与第一页无重叠")

    # ---- 错误形状 ----
    r = c.get("/api/v1/poems", params={"after": "!!!bad"})
    check(r.status_code == 400 and "error" in r.json(), "坏游标 → 400 且错误结构统一",
          r.json().get("error", {}).get("code"))
    r = c.get("/api/v1/poems", params={"script": "klingon"})
    check(r.status_code == 400, "坏 script → 400")
    r = c.get("/api/v1/poems", params={"limit": 99999})
    check(r.status_code == 400, "超大 limit → 400（防拖库）")
    r = c.get("/api/v1/poems/999999999")
    check(r.status_code == 404, "不存在的 id → 404")
    r = c.get("/api/v1/poems/random", params={"genre": "不存在的体裁"})
    check(r.status_code == 404 and r.json()["error"].get("hint"),
          "随机无结果 → 404 且给 hint（不悄悄返回别的诗）")

    # ============================================================ C GraphQL
    print("\nC. GraphQL（必须和 REST 答得一样）")

    def gql(query, variables=None):
        """跑一次 GraphQL，返回 (data, 错误信息)。有错时 data 为 None，
        不让下面直接下标崩掉 —— 测试本身也得报得出人话。"""
        r = c.post("/graphql", json={"query": query, "variables": variables or {}})
        j = r.json()
        if j.get("errors"):
            return None, "; ".join(e.get("message", "?") for e in j["errors"])
        return (j.get("data") or {}), None

    gd, err = gql("{ stats { poems authors genres } }")
    check(gd is not None, "GraphQL 查询可用", err or "")
    if gd:
        check(gd["stats"]["poems"] == st["poems"], "GraphQL 统计 == REST 统计",
              f"{gd['stats']['poems']:,}")

    gd, err = gql('{ genres(script:"zh-Hans"){ name poem_count } }')
    check(gd is not None and gd.get("genres"), "GraphQL 体裁轴可用", err or "")
    if gd and gd.get("genres"):
        check("poem_count" in gd["genres"][0],
              "GraphQL 字段名与 REST 一致（snake_case，不是 poemCount）",
              "、".join(f"{g['name']}({g['poem_count']:,})" for g in gd["genres"][:4]))
    # 驼峰名必须**不**存在，否则两个协议就分家了
    _, err_camel = gql('{ genres { poemCount } }')
    check(err_camel is not None, "驼峰字段名不存在（证明 snake_case 生效）")

    # 同一个过滤在两个协议下必须同数 —— 两个协议共用 storage 的意义就在这里
    if caps["genres"]:
        rq = c.get("/api/v1/poems", params={"genre": "词", "limit": 1}).json()["total"]
        gd, err = gql('{ poems(genre:"词", limit:1){ total } }')
        check(gd is not None and gd["poems"]["total"] == rq,
              "同一过滤 REST 与 GraphQL 命中数一致", f"{rq:,}")
        gd, err = gql('{ poems(genre:"诗", dynasty:"唐", limit:1){ total } }')
        check(gd is not None and gd["poems"]["total"] == tang_shi["total"],
              "唐诗：GraphQL 与 REST 一致", f"{tang_shi['total']:,}")
    # 简繁同 id 在 GraphQL 侧也要成立
    _q = ('{ h: poem(id:%d, script:"zh-Hans"){ dynasty{id} genre{id} author{id} title } '
          '  t: poem(id:%d, script:"zh-Hant"){ dynasty{id} genre{id} author{id} title } }'
          % (pid, pid))
    gd, err = gql(_q)
    check(gd is not None and gd["h"]["dynasty"]["id"] == gd["t"]["dynasty"]["id"]
          and gd["h"]["genre"]["id"] == gd["t"]["genre"]["id"],
          "GraphQL 侧简繁同 id 也是同一首诗", err or "")
    gd, err = gql("{ poem(id: %d){ id title dynasty{name} genre{name} rights{license} } }" % pid)
    check(gd is not None and gd["poem"]["id"] == pid, "GraphQL 取单首可用", err or "")
    gd, err = gql("{ __schema { types { name } } }")
    tnames = {t["name"] for t in (gd or {}).get("__schema", {}).get("types", [])}
    check({"GqlPoem", "GqlDynasty", "GqlGenre", "GqlRights", "GqlSource"} <= tnames,
          "GraphQL 自省包含核心类型")
    # GraphiQL 交互页面在
    rr = c.get("/graphql", headers={"Accept": "text/html"})
    check(rr.status_code == 200 and "graphiql" in rr.text.lower(),
          "GraphiQL 交互页面可用（浏览器能探索 schema）")

    # ============================================================ 结论
    print("\n" + "─" * 62)
    print(f"通过 {len(PASS)} ／ 失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  ❌ {f}")
        sys.exit(1)
    print("✅ 全部通过")
    sys.exit(0)


def test_api():
    """给 pytest 用的入口。"""
    try:
        main()
    except SystemExit as e:
        assert e.code == 0, "接口测试有失败项"


if __name__ == "__main__":
    main()
