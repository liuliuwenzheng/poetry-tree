# 设计：授权优先的数据模型

> 本项目的第一性原理：**一段文本的价值，取决于它能不能被合法地分享。**
> 说不清授权的文本，即使文学价值再高，也不进库。

---

## 一、权利块（Rights Block）—— 每条文本的身份证

库里**每一段文本**（原文、译文各自独立）都必须携带一个权利块。
缺任何必填字段，构建**失败**（fail-closed，不是警告）。

```json
{
  "source_id": "gutenberg",
  "source_url": "https://www.gutenberg.org/ebooks/1041",
  "retrieved_on": "2026-10-09",
  "license": "PD-US",
  "pd_basis": "us_published_before_1930",
  "rights_holder": "William Shakespeare",
  "evidence_url": "https://www.gutenberg.org/policy/license.html",
  "verified_by": "poetry-tree maintainers"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `source_id` | ✅ | 必须出现在 `licenses/sources.json` 登记表里 |
| `source_url` | ✅ | 精确到条目，不是首页 |
| `retrieved_on` | ✅ | 取回日期（证明当时状态） |
| `license` | ✅ | SPDX 风格标识：`PD-US` / `CC0-1.0` / `CC-BY-4.0` / `CC-BY-SA-4.0` / `MIT` |
| `pd_basis` | ✅（当 license 为 PD 时） | 公有领域的**依据**，见下节；不接受「因为网上有」 |
| `rights_holder` | ✅ | 权利人/作者；公有领域也填（用于署名） |
| `evidence_url` | ✅ | 授权声明的原文出处（许可页/版权页） |
| `verified_by` | ✅ | 谁核的 |

## 二、公有领域（PD）判定规则 —— 写死在代码里

**不接受主观判断。** 只有下列依据之一成立，才允许标 `PD`：

| 依据代码 | 规则 | 2026 年的门槛 |
|---|---|---|
| `us_published_before_1930` | 美国：出版年 ≤ 当年 − 96 | **≤ 1930** |
| `author_death_70` | 作者**卒年 + 70 < 当年**（中/欧/日等 life+70 辖区） | **≤ 1955** |
| `author_death_50` | 少数 life+50 辖区 | **≤ 1975**（**默认不用**，除非明确目标辖区） |
| `government_work_pd` | 政府作品/无版权声明 | 需逐案证据 |
| `cc0_explicit` | 权利人主动放弃 | 需 CC0 声明链接 |

**保守原则**：一个作品只有在**全部目标辖区**都 PD 时，才标 `PD`；
否则只能标具体许可（如 `CC-BY-SA-4.0`）或**不收**。

### ⚠️ 译者是被独立保护的人

**这是最容易被告的地方。**

- 鲁米（1207–1273）的原诗：公有领域 ✅
- R. A. Nicholson 的英译（1925–1940）：他卒于 1945 → 2026 年已过 life+70 → **可用** ✅
- A. J. Arberry 的英译（1950s）：他卒于 1969 → 2039 年前**不可用** ❌
- 松尾芭蕉（1644–1694）原文：公有领域 ✅
- R. H. Blyth 的英译：他卒于 1964 → **2034 年前不可用** ❌

所以**译文本体必须走同一套 PD 判定**，用**译者**的卒年/出版年，而不是原作者的。

### ⚠️ 整理成果也可能是作品

标点、校勘、注释、选集编排，在某些辖区受**独立**保护（如中国的古籍整理成果）。
判定使用**该校勘者/整理者**的信息，不是原作者的。

## 三、数据模型 —— 原文与译文分离

```
work            作品（抽象）
  ├── work_edition    原文版本（语言、来源、权利块）
  │     └── text_unit 文本（段落，JSON 数组）
  └── rendition       译文（语言、译者、权利块）
        └── text_unit 文本
```

**关键设计**：译文是**独立实体**，挂自己的权利块。
权利人可以只要求撤下某个译文，而不影响原文——**撤下是按钮，不是考古**。

```sql
CREATE TABLE work (            -- 作品
  id INTEGER PRIMARY KEY,
  author_id INTEGER, title_orig TEXT, lang_orig TEXT,
  composed_from INTEGER, composed_to INTEGER,   -- 创作年代（可空）
  kind TEXT                                     -- poem / lyric / epic / haiku …
);
CREATE TABLE author (
  id INTEGER PRIMARY KEY, name_orig TEXT, name_zh TEXT, name_en TEXT,
  country TEXT, lang_orig TEXT, birth_year INTEGER, death_year INTEGER
);
CREATE TABLE work_edition (   -- 原文版本
  id INTEGER PRIMARY KEY, work_id INTEGER,
  source_id TEXT NOT NULL, license TEXT NOT NULL, rights_holder TEXT,
  pd_basis TEXT, source_url TEXT, evidence_url TEXT,
  retrieved_on TEXT, verified_by TEXT,
  title TEXT, content TEXT NOT NULL            -- JSON 数组，段落
);
CREATE TABLE rendition (      -- 译文（独立权利块！）
  id INTEGER PRIMARY KEY, work_id INTEGER,
  lang TEXT NOT NULL, translator TEXT, translator_death_year INTEGER,
  source_id TEXT NOT NULL, license TEXT NOT NULL, rights_holder TEXT,
  pd_basis TEXT, source_url TEXT, evidence_url TEXT,
  retrieved_on TEXT, verified_by TEXT,
  title TEXT, content TEXT
);
CREATE TABLE source (         -- 来源登记表（构建的唯一依据）
  id TEXT PRIMARY KEY, name TEXT, license TEXT, license_url TEXT,
  homepage TEXT, commercial_ok INTEGER, share_alike INTEGER,
  attribution_required INTEGER, notes TEXT
);
```

`NOT NULL` 不是装饰——**授权字段为空的 INSERT 会直接被 SQLite 拒绝**，
这就是最后一道防线。

## 三之二、朝代 × 体裁必须正交（血换来的教训）

**这一条是百川 v0.2.0 的真实 bug 换来的，不是理论洁癖。**

旧模型里，体裁表的条目直接叫「**唐诗**」「**宋词**」「**五代词**」——
把【朝代】和【体裁】两个维度压进了同一个字段。后果实测：

| 病症 | 实测数据 |
|---|---|
| 体裁「五言绝句」的表分类是「唐诗」，却挂满了所有朝代 | 七言绝句：宋 **87,173** 首 vs 唐 **12,459** 首 |
| 纳兰性德的**清**词被标成体裁「**宋词**」 | 宋词体裁下混着清 257 首 |
| 「五代词」和「宋词」是两个条目，其实是同一个体裁 | 想查「所有词」只能硬编码 `type_id IN (20,21)` |
| 来一个新朝代就崩 | 明词、清词没有对应条目，只能被硬塞进「宋词」 |

**根子**：`唐诗 = 【唐】×【诗】`、`宋词 = 【宋】×【词】`。
用一个字段表达两个维度，就必然在某一维上出错。

### 本项目的模型：三个正交轴

```sql
-- 轴一：朝代（何时）
dynasties(id, name, name_en, start_year, end_year)

-- 轴二：体裁（何种文体）—— 与朝代无关，跨朝代存在
genres(id, name, name_en, description)
--   1 诗 ｜ 2 词 ｜ 3 曲 ｜ 4 诗经 ｜ 5 楚辞 ｜ 6 论语 ｜ 7 蒙学 ｜ 8 四书五经 ｜ 9 其他

-- 轴三：形式/格律（可选，属于某个体裁之下）
forms(id, genre_id, name, lines, chars_per_line)
--   11 五言绝句(诗) ｜ 12 七言绝句(诗) ｜ 13 五言律诗(诗) ｜ 14 七言律诗(诗)
--   15 五言古诗(诗) ｜ 16 七言古诗(诗) ｜ 17 乐府(诗)

-- 作品：三轴独立引用，外加词牌
texts(id, genre_id, form_id NULL, tune NULL, dynasty_id, author_id, title, content, ...)
```

一条作品的完整身份长这样：

| 作品 | 朝代（何时） | 体裁（何种） | 形式 / 词牌 |
|---|---|---|---|
| 李白《静夜思》 | 唐 | 诗 | 五言绝句 |
| 苏轼《水调歌头》 | 宋 | 词 | 词牌=水调歌头 |
| **纳兰性德《长相思》** | **清** | **词** | 词牌=长相思 |
| 关汉卿《诈妮子调风月》 | 元 | 曲 | — |
| 《关雎》 | 先秦 | 诗经 | — |

**纳兰性德那行就是修复点**：旧模型下它是「宋词」，新模型下是【清】×【词】。

### 这样设计，接口才能自然表达

```sql
WHERE genre_id=2                        -- 所有词，不分朝代（旧模型做不到）
WHERE genre_id=1 AND dynasty_id=6       -- 唐诗
WHERE genre_id=1 AND dynasty_id=8       -- 宋诗
WHERE genre_id=2 AND tune='水调歌头'    -- 所有《水调歌头》，跨朝代
WHERE genre_id=2 AND dynasty_id=10      -- 清词
```

**反过来说**：如果 dynasty 和 genre 不拆开，上面每一个查询都要靠硬编码的 id 列表来拼，
而每次新增一个朝代（或新增一个体裁）就得回来改一次代码和索引。**这就是「方便未来更好的
接口调入与使用」的实际含义**——不是多写几个 API，而是让数据模型本身不再需要改。

### 体裁的判定必须有依据

对没有体裁列的来源（如 Werneror/Poetry），**不能靠猜**。做法：
词牌表**从已有的词作里导出**（chinese-poetry 的宋词、花间集、南唐二主词），
标题命中词牌或「词牌·副题」形式的判为词，其余按诗。
判不出的**保守落到「诗」**，并允许为空——宁可标注「未知」，也不给假标签。

---

## 四、构建闸门（fail-closed）

```
for 每个来源:
  1. 来源是否在 source 表登记？        否 → 中止
  2. 记录的权利块字段是否齐全？        否 → 中止并列出缺哪项
  3. license 与登记的 license 一致？   否 → 中止（防偷偷换源）
  4. 若 license 是 PD：pd_basis 成立？  否 → 中止并打印门槛年份
  5. 若目标辖区数与 pd_basis 辖区不符 → 降级为「仅元数据」，不收正文
  6. 全通过 → 入库；否则该条跳过并写入 rejects.json，附原因
```

**永远不要「先收着，以后再清理」。** 收进来的东西会流出去，清理永远来不及。

## 五、来源登记表（`licenses/sources.json`）

```json
{
  "chinese_poetry": {
    "name": "chinese-poetry",
    "homepage": "https://github.com/chinese-poetry/chinese-poetry",
    "license": "MIT",
    "license_url": "https://github.com/chinese-poetry/chinese-poetry/blob/master/LICENSE",
    "rights_holder": "JackeyGao and contributors",
    "attribution_required": true,
    "commercial_ok": true,
    "share_alike": false,
    "notes": "古代作品文本属公有领域；该项目的整理结构化成果以 MIT 授权"
  },
  "werneror_poetry": {
    "name": "Werneror/Poetry",
    "homepage": "https://github.com/Werneror/Poetry",
    "license": "MIT",
    "rights_holder": "Werner",
    "attribution_required": true,
    "commercial_ok": true,
    "share_alike": false
  },
  "gutenberg": {
    "name": "Project Gutenberg",
    "homepage": "https://www.gutenberg.org",
    "license": "PD-US",
    "license_url": "https://www.gutenberg.org/policy/license.html",
    "attribution_required": false,
    "commercial_ok": true,
    "share_alike": false,
    "notes": "逐条判断；Gutenberg 的商标条款不影响文本本身"
  },
  "wikisource": {
    "name": "Wikisource",
    "homepage": "https://wikisource.org",
    "license": "CC-BY-SA-4.0",
    "license_url": "https://wikisource.org/wiki/Wikisource:Copyright",
    "attribution_required": true,
    "commercial_ok": true,
    "share_alike": true,
    "notes": "同协议共享：衍生作品也须 CC BY-SA"
  }
}
```

## 六、为什么这样做

1. **法律**：先说清权利，再谈数据。
2. **可维护**：来源、授权、证据都在数据里，不是散在聊天记录里。
3. **可撤下**：译文独立成实体，权利人一投诉就能精确移除。
4. **诚实**：做不到的地方（如「仅元数据」）明说，不假装拿到了授权。

> 我们要做的是**让人更容易遇见诗**，不是把别人的心血搬到自己家里。
