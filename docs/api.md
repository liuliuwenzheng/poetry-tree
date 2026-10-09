# 诗文树开放接口

> 让诗的美更容易被遇见 —— 同时不侵害任何作者与版权所有者的利益。

---

## 一、先讲原则，再讲接口

### 1.1 第一原则

**只收能够确认授权的材料。授权不明 = 不收。**

这条原则在代码里有两处硬约束，不是文档里的口号：

1. **构建期**：`corpus/rights.py` 对每个来源做闸门检查，未登记 / 未核实 / 标 PD 却
   拿不出依据的，一律拒绝入库（fail-closed）。
2. **服务期**：`api/app.py` 启动时审计库里的每一个来源。只要有一个没在
   `licenses/sources.json` 登记且 `license_checked=true`，**服务直接拒绝启动**。

为什么服务期也要查？因为「构建时严格、服务时宽松」等于原则没落地 ——
一旦有人手工往库里塞了东西，接口就成了绕过闸门的后门。

### 1.2 三个正交的轴 —— 这个接口最该讲清楚的一件事

诗歌的分类天然是**多维**的，把维度压扁是这类项目最常见的架构病：

| 轴 | 回答的问题 | 取值 | 例 |
|---|---|---|---|
| `dynasty` | **何时** | 唐 / 宋 / 清 / 元 / 先秦… | 唐 |
| `genre` | **何种文体** | 诗 / 词 / 曲 / 赋 / 诗经 / 楚辞… | 词 |
| `form` | **什么格律** | 五言绝句 / 七言律诗… | 五言绝句 |

再加一个 `tune`（词牌 / 曲牌）。

**「唐诗」不是一个体裁，是 唐 × 诗 的组合；「宋词」是 宋 × 词。**

把「唐诗」当成体裁名写进数据，会造成三种必然后果（这些都是真实踩过的）：
1. 体裁表里出现「唐诗」「宋词」「五代词」「清词」并列的怪象；
2. 「五代词」的上级分类写「词」，而「宋词」的上级写「诗歌」—— 同类不同构；
3. 纳兰性德身为清人，他的词被塞进「宋词」；要单列「清词」又得再加一行，
   而「所有词，不分朝代」这件事**根本表达不出来**。

所以本接口把轴拆开。直接的好处：

```bash
# 唐诗（唐 × 诗）
curl "http://127.0.0.1:8710/api/v1/poems?genre=诗&dynasty=唐&limit=3"

# 宋词（宋 × 词）
curl "http://127.0.0.1:8710/api/v1/poems?genre=词&dynasty=宋&limit=3"

# 清词（清 × 词）
curl "http://127.0.0.1:8710/api/v1/poems?genre=词&dynasty=清&limit=3"

# 所有词，不分朝代 —— 一行搞定，不需要任何硬编码的 id 列表
curl "http://127.0.0.1:8710/api/v1/poems?genre=词&limit=3"
```

### 1.3 每条记录都带授权

接口返回的每一首诗、每一个作者、每一条译文，都带一个 `rights` 块：

```json
"rights": {
  "source": "chinese_poetry",
  "source_name": "chinese-poetry 中文诗歌古典资料",
  "homepage": "https://github.com/chinese-poetry/chinese-poetry",
  "license": "MIT",
  "license_url": "https://github.com/chinese-poetry/chinese-poetry/blob/master/LICENSE",
  "rights_holder": "chinese-poetry 项目贡献者",
  "attribution_required": true,
  "commercial_ok": true,
  "share_alike": false,
  "per_item_check_required": false,
  "license_checked": true,
  "checked_on": "2026-10-09",
  "roles": ["original_text"],
  "note": "……"
}
```

**这么做的理由**：使用者在拿到一条诗的时候，必须能立刻知道「这条能不能商用、
要不要署名、要不要以同样许可共享」。要求他们自己去翻项目的 README 才能确定，
等于把合规风险转嫁给了下游。

### 1.4 按授权过滤 —— 库政策要求的能力

`licenses/sources.json` 里明确写着：

> 接口必须能按 license 过滤，让无法履行 ShareAlike 的下游用户能排除这些记录。

接口实现了：

```bash
# 只要公有领域的
curl ".../poems?license=PD-US"

# 排除所有 ShareAlike 的（下游无法以相同方式共享时的正确做法）
curl ".../poems?share_alike=false"

# 只要可商用的
curl ".../poems?commercial_ok=true"
```

---

## 二、跑起来

```bash
cd 诗文树

# 默认绑 127.0.0.1:8710
python -m api.serve

# 换库 / 换端口 / 让别人也能访问
python -m api.serve --db "E:/AI-ku/项目/baichuan-poetry/data/baichuan.db" \
                    --host 0.0.0.0 --port 8710
```

| 地址 | 用途 |
|---|---|
| `http://127.0.0.1:8710/docs` | **REST 交互文档**（Swagger UI，能直接点着试） |
| `http://127.0.0.1:8710/graphql` | **GraphiQL**（能看见 schema、勾字段、导 SDL） |
| `http://127.0.0.1:8710/api/v1/` | 服务信息（第一原则、能力清单、端点索引） |
| `http://127.0.0.1:8710/api/v1/healthz` | 健康检查 |

数据库从
[baichuan-poetry Releases](https://github.com/liuliuwenzheng/baichuan-poetry/releases)
下载，解压后在 `--db` 指过去即可。**接口是只读的**（连接以 `mode=ro` 打开），
不可能写坏数据。

---

## 三、REST 接口

所有列表型接口用**游标分页**，返回同一个信封：

```json
{
  "items": [ ... ],
  "total": 993753,
  "count": 20,
  "next_cursor": "NDAwMDIw",
  "has_more": true
}
```

翻页就是把 `next_cursor` 原样传给下一页的 `after`：

```bash
curl ".../poems?limit=100"
curl ".../poems?limit=100&after=NDAwMDIw"     # next_cursor 原样带回来
```

> 为什么不用 `?page=2` / `?offset=100`？因为 offset 分页在数据变动的库上会
> 漏行或重行，而且深翻页（`OFFSET 900000`）要扫过前面所有行。游标分页两样都没有。

### 3.1 端点一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/` | 服务信息 |
| GET | `/api/v1/healthz` | 健康检查 |
| GET | `/api/v1/meta/dynasties` | 朝代轴 |
| GET | `/api/v1/meta/genres` | 体裁轴 |
| GET | `/api/v1/meta/forms` | 形式轴（格律） |
| GET | `/api/v1/meta/tunes` | 词牌 / 曲牌 |
| GET | `/api/v1/meta/stats` | 统计（按体裁 / 朝代 / 来源） |
| GET | `/api/v1/poems` | 作品列表（可筛选 + 分页） |
| GET | `/api/v1/poems/random` | 随机一首 |
| GET | `/api/v1/poems/{id}` | 单首详情 |
| GET | `/api/v1/poems/{id}/translations` | 某首的译文 |
| GET | `/api/v1/search` | 全文检索 |
| GET | `/api/v1/authors` | 作者列表 |
| GET | `/api/v1/authors/resolve` | **别名解析** |
| GET | `/api/v1/authors/{id}` | 作者详情 |
| GET | `/api/v1/sources` | 来源与授权登记 |

### 3.2 通用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `script` | `zh-Hans` | `zh-Hans` 简体 / `zh-Hant` 繁体 |

**文字版本是一个参数，不是两套端点。** 同一首诗的简体与繁体是同一行数据的两种
呈现，`id` 完全相同。所以：

```bash
curl ".../poems/400000?script=zh-Hans"
curl ".../poems/400000?script=zh-Hant"     # 同一个 id，同一首诗
```

这样设计的好处是**不会有两个漂移的数据集**。早先的版本把简繁做成两张独立去重的
表，结果繁体侧每丢一行重复就错一位，两表 id 整体错开 25,294 个 —— 同一个 id
在两边的诗根本不是同一首。现在繁体表是简体表的**逐条镜像**，去重只在简体侧做，
`id` 是诗的唯一身份。

### 3.3 `/poems` 的筛选参数

筛选值一律用**人能读懂的名字**，不是 id —— 调用方不该先查一遍 id 表。

| 参数 | 例 | 说明 |
|---|---|---|
| `dynasty` | `唐` | 朝代 |
| `genre` | `词` | 体裁（简繁写法都认） |
| `form` | `五言绝句` | 格律 |
| `tune` | `水调歌头` | 词牌 / 曲牌 |
| `author` | `陶渊明` | 作者（**别名也能查到**） |
| `q` | `明月` | 标题 / 正文子串匹配 |
| `source` | `chinese-poetry` | 来源 |
| `license` | `MIT` | 只要这个许可 |
| `share_alike` | `false` | 排除须相同方式共享的材料 |
| `commercial_ok` | `true` | 只要可商用的 |
| `order` | `id` / `random` | 排序 |
| `limit` | `20` | 每页条数（上限 200） |
| `after` | 游标 | 从上一页的 `next_cursor` 原样带回来 |

### 3.4 `/search` 全文检索

```bash
curl ".../search?q=明月几时有&limit=5"
curl ".../search?q=明月&genre=词&dynasty=宋"
```

返回每条带 `snippet`（命中片段，命中词用 `[]` 标出）和 `score`
（bm25 相关性，**越小越相关**）。

**接口会自己选路，绝不给你一个假的「0 结果」**：

| 检索词 | 走哪条路 | 说明 |
|---|---|---|
| ≥ 3 个字 | `fts5-bm25` | 按相关性排序、带高亮片段 |
| 1–2 个字 | `like-fallback` | trigram 索引按 3 字切分，短词匹配不到 → 自动改子串扫描 |
| 含引号 / 星号等 | `like-fallback` | FTS 查询语法不接受这些符号 → 自动换路，不报 500 |
| 库里没建索引 | `like-fallback` | 一律子串扫描 |

`engine` 字段如实告诉你这次走了哪条路，`note` 说明降级原因。

所以**「明月」这种 2 字查询照样查得到**（2 万多首）——只是走子串扫描，约 0.3 秒，
结果完整。**这点很重要**：如果接口在 2 字查询上直接丢一个「0 结果」，
用户会以为库里没有，而那是骗人的。

想让所有查询都走快路，给库建全文索引：

```bash
cd baichuan-poetry && python tools/build_search.py    # 约 15 分钟
```

### 3.5 别名解析 —— 「搜不到」和「不存在」是两件事

陶渊明在库里叫**陶潜**。如果直接把用户输入当主键查，用户会以为「库里没有陶渊明」，
而实际上有 151 首。

```bash
curl ".../authors/resolve?name=陶渊明"
# → {"items": [{"id": 9133, "name": "陶潜", ...}], "count": 1}
```

`author=` 筛选也走同一套别名逻辑，所以：

```bash
curl ".../poems?author=陶渊明"    # 151 首
curl ".../poems?author=陶潜"      # 151 首 —— 完全一致
```

### 3.6 错误结构

所有错误长一个样，REST 和 GraphQL 都是：

```json
{"error": {"code": "bad_request", "message": "游标格式不对……", "hint": "……"}}
```

| 状态码 | `code` | 什么时候 |
|---|---|---|
| 400 | `bad_request` | 参数错了（游标、script、limit 超限…） |
| 400 | `unknown_filter_value` | **筛选值在这份数据里根本不存在**（见下） |
| 404 | `not_found` | id 不存在，或筛选条件下**确实没有**结果 |
| 409 | `capability_missing` | 这个库没有该能力（比如没建词牌列却按 `tune` 查） |
| 500 | `internal_error` | 服务端错误 |

> `random` 在筛选无结果时返回 **404 + hint**，不返回别的诗。
> 「悄悄地给你一首不相干的」比报错糟糕得多。

#### 「值不存在」和「组合为空」是两件事

两者都是「0 条结果」，但必须分得开 —— **否则调用方会得出一个假结论**：

| 情形 | 返回 | 为什么 |
|---|---|---|
| `?genre=词词`（库里没这个体裁） | **400 `unknown_filter_value`** + `valid` 可选值 | 是**调用方写错了**。静静返回空，他会以为「一首宋词都没有」 |
| `?dynasty=明&genre=诗经`（值都对，但没这个组合） | **200 空页** | 合法查询，空就是正确答案 |

```json
{"error": {
  "code": "unknown_filter_value",
  "axis": "genre", "value": "词词",
  "valid": ["诗", "词", "曲", "诗经", "楚辞", "论语", "蒙学", "四书五经", "其他"],
  "hint": "可选值：……。另外注意中文参数需要 URL 编码……"
}}
```

`author` 走同一套：查不到人不是空结果，是 400，并提示库里按**本名**存
（陶渊明→陶潜），可用 `/authors/resolve` 解析别名。

#### ⚠️ Windows 上 curl 发中文的坑（实测，不是理论）

**`curl --data-urlencode 'genre=词'` 在 Windows 上会把中文按本地代码页（GBK）发出去。**
实测「词」被发成 `%B4%CA`（GBK 字节），服务端解出来的不是 UTF-8 的「词」，
于是参数对不上 —— 改之前这里会**安静地返回 0 条**，用的人只会认为「接口坏了」。

现在接口会**认出这是编码问题**（而不是拼写问题）并明说：

```
400 unknown_filter_value
message: genre 参数没能正确解码 —— 送进来的是非法 UTF-8 字节
hint:    这不是拼写问题，是编码问题。请以 UTF-8 发送中文并做 URL 编码
         （如 genre=%E8%AF%8D）。Windows 上的 curl 会按本地代码页（GBK）编码参数……
```

三种正确的发法，任选：

```bash
# ① 显式写 UTF-8 百分号编码（curl 下最省事）
curl "http://127.0.0.1:8710/api/v1/poems?genre=%E8%AF%8D&dynasty=%E5%AE%8B"

# ② Python —— 自己控制编码，最稳
python -c "import urllib.parse,urllib.request,json; \
  u='http://127.0.0.1:8710/api/v1/poems?'+urllib.parse.urlencode({'genre':'词'}); \
  print(json.load(urllib.request.urlopen(u))['total'])"

# ③ 本仓库的 SDK —— 编码交给它
```

请求体同理：`POST /graphql` 的 body 不是合法 UTF-8 时，返回
`400 bad_request` +「请求体不是合法的 UTF-8」，**而不是把 Python 的裸编解码错误丢给你**。

---

## 四、GraphQL

`http://127.0.0.1:8710/graphql`（浏览器打开是 GraphiQL 交互界面）。

**REST 与 GraphQL 共用同一套查询语义**（同一层 `api/storage.py`），所以同一个
查询在两个协议下必然同数。这一点有自动化测试盯着（`tests/test_api.py`）。

字段名保持 `snake_case`，与 REST 的 JSON 一致 —— 两个协议之间可以无痛切换。

```graphql
{
  poems(genre: "词", dynasty: "宋", limit: 3) {
    total
    items {
      id
      title
      text
      dynasty { name start_year end_year }
      genre { name description }
      form { name lines chars_per_line }
      tune
      author { id name }
      rights { license license_url attribution_required share_alike commercial_ok }
    }
  }
}
```

好处是不用取不需要的字段：只要标题就写 `title`，不会像 REST 那样把整首诗都传回来。

```graphql
# 跨朝代查一个词牌
{ tunes(genre: "词", q: "水调") { name count } }

# 别名解析
{ resolve_author(name: "陶渊明") { name description poem_count } }

# 只用公有领域材料的检索
{ search(q: "明月几时有", license: "PD-US") { total items { snippet poem { title } } } }
```

---

## 五、Python SDK

`sdk/poetry_tree.py`，**零第三方依赖**（只用标准库）：

```python
import sys; sys.path.insert(0, "sdk")
from poetry_tree import PoetryTree

pt = PoetryTree("http://127.0.0.1:8710")

# 清词
p = pt.random(genre="词", dynasty="清")
print(f"《{p['title']}》 {p['author']['name']}  [{p['dynasty']['name']}·{p['genre']['name']}]")
print("".join(p["content"]))

# 检索
for h in pt.search("明月几时有", limit=5)["items"]:
    print(h["score"], h["poem"]["title"], h["snippet"])

# 遍历（自动翻页 —— 游标是服务端的实现细节，调用方不该看到）
for poem in pt.iter_poems(author="陶渊明", page_size=200):
    print(poem["title"])

# 别名
print(pt.resolve_author("陶渊明")[0]["name"])   # 陶潜

# 直接打 GraphQL
print(pt.stats()["by_genre"])
```

出错时抛 `PoetryTreeError`，带 `code` / `message` / `hint`，可直接展示给用户：

```python
from poetry_tree import PoetryTreeError
try:
    pt.poems(after="乱写的")
except PoetryTreeError as e:
    print(e.code, e.message, e.hint)
```

---

## 六、下游怎么合规使用

1. **读每条的 `rights`**。`attribution_required` 为真就必须署名（写明来源与
   许可），`share_alike` 为真则你的衍生作品也要以兼容许可发布。
2. **不能履行 ShareAlike 就排除这些记录**，这是库政策明确要求提供的能力：
   `?share_alike=false`。
3. **译文与原文可分权**。译文是独立的作品、有独立的译者与许可，接口把它单独
   放在 `translations` 里，就是为了让你能只取原文、不取译文（反之亦然）。
4. **来源登记表**见 `/api/v1/sources`，含 `license_url`（许可原文）与
   `checked_evidence`（核查依据）。

---

## 七、扩展：换一个数据源

接口不认识「百川」，只认识 `api/storage.py` 定义的领域模型。
要让接口服务别的库（比如将来的多语言诗歌库），只需满足：

* 库里有 `poems_{script}` / `authors_{script}` / `dynasties_{script}` 这些表；
* 可选表（`genres_*`、`poems_fts_*`、`author_aliases`）缺失时接口自动降级，
  并在 `/api/v1/` 的 `capabilities` 里如实报告。

`poem.py` / `ingest.py` 之于百川，就像驱动程序之于内核 —— 换数据源就是换驱动。

---

## 八、已知限制

| 限制 | 说明 |
|---|---|
| 1–2 字检索走子串扫描 | trigram 索引的固有特性，接口自动换路（结果完整，约 0.3 秒）；3 字以上才走索引 |
| 无 Docker 镜像 | 目前只提供 Python 直接运行 |
| 无写接口 | 接口是只读的，入库只能走构建流程（有意为之：让授权闸门无法绕过） |
| 单机 SQLite | 读多写零的场景足够；要横向扩展需换存储后端 |
| 词牌表来自数据 | 词牌 / 曲牌是从已有词作**反查导出**的，不是手工编的；编号体系不同的诗话词谱可能对不上 |
| 全文索引约占 2 GB | 建了索引的库明显更大；不建也能用（自动走子串扫描） |
| bm25 深翻页无游标 | 相关性排序没有稳定游标，检索最多翻 `search_offset_max` 条；更深的请加条件缩小范围 |

---

## 九、设计决策备忘

| 决策 | 为什么 |
|---|---|
| 朝代 / 体裁 / 形式拆三轴 | 压扁成「唐诗」这种标签会导致「所有词」表达不出来（见 §1.2） |
| `script` 是参数不是端点 | 简繁是同一首诗的两种呈现，同一 id；做成两套数据必然漂移 |
| 游标分页而非 offset | offset 在变动数据上漏行重行，深翻页还慢 |
| 筛选用名字不用 id | 调用方不该先查一遍 id 表 |
| rights 挂在每条上 | 合规判断不该要求用户去翻项目 README |
| 启动时审计来源 | 否则接口会成为绕过构建闸门的后门 |
| REST 与 GraphQL 共用 storage | 两个协议各写一套查询，迟早答得不一样 |
| 接口只读 | 入库只能走构建流程，授权闸门才无法绕过 |
| 错误结构 REST / GraphQL 统一 | 客户端可以一套错误处理走到底 |
