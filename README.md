# 诗文树 · Poetry Tree

> **让诗的美更容易被遇见——同时不侵害任何作者与版权所有者的利益。**

---

## 规矩（先读，这是本项目的第一原则）

**只收能够确认授权的材料。授权不明 = 不收。**

这不是一句口号，是**写死在构建流程里的硬约束**——任何一段文本，只要说不出
「来自哪里、什么授权、权利人是谁」，构建就**直接失败**，不会悄悄进库。

具体六条：

1. **逐条标注**：每段文本（原文、译文**分开**）都必须带来源 URL、授权标识（SPDX）、
   权利人/作者。缺任一项 → 拒绝入库。
2. **只收公有领域与开放许可**：公有领域作品、CC0、CC BY、CC BY-SA、MIT 等。
3. **现代译本一律不收**。译者的版权**独立于**原作者——鲁米的原诗是公有领域，
   但 20 世纪的英译本不是。宁缺勿滥。
4. **公有领域的判定标准写死在代码里**，不靠感觉（见 `docs/design.md`）。
5. **译文与原文分表存储**，各自标注授权、译者、来源；译文可随时单独撤下，
   不影响原文。
6. **权利人有异议 → 立即移除**，不问理由。

> 目标是**帮助人们更方便地接触到诗的美**，而不是替作者「传播」他们的作品到
> 损害其利益的地方。宁可少收一千首，不可多收一首侵权的。

---

## 收什么 / 不收什么

| | 例子 | 收吗 |
|---|---|---|
| 古代作品原文 | 唐诗、宋词、《万叶集》、莎士比亚十四行诗 | ✅ 公有领域 |
| 已进入公有领域的**老译本** | Fitzgerald 译《鲁拜集》(1859)、Nicholson 译鲁米 (1925) | ✅ 译者卒年已过保护期 |
| CC BY-SA 整理成果 | Wikisource 校对过的文本 | ✅ 署名 + 同协议共享 |
| MIT 授权数据集 | chinese-poetry、Werneror/Poetry | ✅ 保留版权声明 |
| **现代译本** | 某出版社 2019 年《鲁米诗集》 | ❌ 译者版权在保护期内 |
| 授权不明的网络文本 | 论坛/博客转载、没有出处的「名句」 | ❌ 说不清来源就是风险 |
| 商业诗集扫描件 | 在售电子书 | ❌ |

---

## 与「百川」的分工

本机另一个项目 **百川 Baichuan Poetry** 是**中文诗歌的数据工程**（102 万首，
侧重数据清洗与修正）。**诗文树**是**多语言语料库与开放接口**，侧重：

| | 百川 | 诗文树 |
|---|---|---|
| 定位 | 中文诗歌数据（清洗好的库） | 多语言语料库 + **对外接口** |
| 语言 | 中文（简/繁） | 任意语言 + 三语对照 |
| 核心产出 | `baichuan.db` | **API / SDK / 统一 schema** |
| 授权基础 | 汇集（含一处 GPL 产物待清除） | **只用可确认授权的源** |

百川是诗文树的**一个数据源适配器**，不是重复建设。

## 目录

```
诗文树 (poetry-tree)/
├── docs/design.md        # 授权优先的数据模型 + 公有领域判定规则 ★核心
├── licenses/sources.json # 来源授权登记表（构建的唯一依据）
├── corpus/
│   ├── sources/          # 各来源适配器（只接受已登记的授权）
│   ├── normalize.py      # 统一 schema（原文/译文分离）
│   └── build.py          # 构建 + 授权闸门
├── api/                  # 开放接口 ★
│   ├── storage.py        #   领域层：模型 ↔ 数据库的唯一翻译点（REST/GraphQL 共用）
│   ├── rights.py         #   授权层：与构建闸门读同一份 sources.json
│   ├── rest.py           #   REST（/api/v1）
│   ├── graphql_api.py    #   GraphQL（/graphql，带 GraphiQL 交互界面）
│   └── serve.py          #   启动入口：python -m api.serve
├── sdk/poetry_tree.py    # Python 客户端（零依赖，自动翻页）
├── tests/test_api.py     # 接口测试（含架构不变量，不只是「能通」）
└── cli/
```

## 快速开始

```bash
# ---- 一、构建/校验数据（授权闸门） ----
python corpus/rights.py --selftest                            # 闸门自测 8/8
python corpus/rights.py --check-source chinese_poetry         # 单来源授权核查

# ---- 二、起接口 ----
python -m api.serve --db "E:/AI-ku/项目/baichuan-poetry/data/baichuan.db"

#   REST 文档  http://127.0.0.1:8710/docs
#   GraphiQL   http://127.0.0.1:8710/graphql

# ---- 三、跑接口测试（61 条断言，不需要先起服务）----
python -m tests.test_api
```

```bash
curl "http://127.0.0.1:8710/api/v1/poems?genre=词&dynasty=宋&limit=3"   # 宋词
curl "http://127.0.0.1:8710/api/v1/poems?genre=诗&dynasty=唐&limit=3"   # 唐诗
curl "http://127.0.0.1:8710/api/v1/poems?genre=词&limit=3"              # 所有词，不分朝代
curl "http://127.0.0.1:8710/api/v1/search?q=明月几时有"                  # 全文检索
curl "http://127.0.0.1:8710/api/v1/authors/resolve?name=陶渊明"          # 别名 → 陶潜
```

**接口文档：[`docs/api.md`](docs/api.md)** —— 里面有设计决策备忘（为什么这么设计）。

## 路线图

- [x] 定规矩：授权优先的数据模型（`docs/design.md`）
- [x] 授权闸门 + 来源登记表 —— fail-closed，`rights.py --selftest` 8/8
- [x] 首批干净源：chinese-poetry (MIT)、Werneror (MIT)（经**百川**适配器入库）
- [x] **REST / GraphQL 接口 + Python SDK**（`api/` + `sdk/`，见 `docs/api.md`）
- [ ] 公有领域英诗：Project Gutenberg（莎士比亚、惠特曼、狄金森）
- [ ] 公有领域老译本：Fitzgerald《鲁拜集》、Nicholson 鲁米
- [ ] Wikisource (CC BY-SA) 多语校对文本
- [ ] 三语对照检索
- [ ] 按授权过滤的对外数据导出（供无法履行 ShareAlike 的下游使用）

## 授权

- **本项目的代码与文档**：MIT（Copyright © 2026 诗文树 contributors）
- **收录的文本**：逐条随其来源授权，见 `licenses/sources.json` 与每条记录的 `license` 字段
- 我们不主张任何收录文本的著作权

## 致谢

建成后在此列出全部来源及其权利人。**每个来源都必须被署名**——
这是规矩的一部分，不是礼节。

若您是权利人，认为收录不当，请提 Issue，我们立即移除。
