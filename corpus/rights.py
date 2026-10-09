#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
授权闸门 —— 诗文树的第一道也是最后一道防线。

设计原则：**fail-closed（默认拒绝）**。
任何一项证据不足，就拒绝收录，并明确说明缺什么。

两道检查：
  ① 来源闸门 check_source()    —— 来源必须在 licenses/sources.json 登记，
                                且 license_checked 必须为 true 且未被 blocked。
  ② 记录闸门 validate_rights() —— 每条文本的权利块字段必须齐全，
                                且若标 PD，pd_basis 必须在阈值上成立。

用法：
  python corpus/rights.py --selftest            # 跑内置案例（含译者陷阱）
  python corpus/rights.py --check-source gutenberg
  python corpus/rights.py --list
  python corpus/rights.py --thresholds          # 打印当前年份下的 PD 门槛
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCES_FILE = os.path.join(ROOT, "licenses", "sources.json")
CURRENT_YEAR = datetime.date.today().year

# ---------------------------------------------------------------- 许可
OPEN_LICENSES = {
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0",
    "MIT", "Apache-2.0", "BSD-3-Clause", "PD-US", "PD",
}
# 允许但要求同协议共享的许可（引入前须确认本项目整体策略兼容）
SHARE_ALIKE = {"CC-BY-SA-4.0", "CC-BY-SA-3.0", "GPL-3.0"}

REQUIRED_FIELDS = (
    "source_id", "source_url", "retrieved_on", "license",
    "rights_holder", "evidence_url", "verified_by",
)


# ---------------------------------------------------------------- PD 规则
def pd_rules(year: int = CURRENT_YEAR) -> dict:
    """公有领域判定门槛（写死在代码里，不接受主观判断）。

    门槛随年份自动前移：
      us_published_before_1930 : 美国，出版年 <= year-96（2026 年即 <= 1930）
      author_death_70          : 作者卒年 + 70 < year（2026 年即 <= 1955）
      author_death_50          : 少数 life+50 辖区（默认不用）
    """
    return {
        "us_published_before_1930": {
            "desc": "美国：出版年 <= 当年-96（滚动）",
            "max_published_year": year - 96,
            "explain": f"{year} 年门槛：出版年 <= {year - 96}",
        },
        "author_death_70": {
            "desc": "作者/译者卒年 + 70 < 当年（life+70 辖区）",
            "max_death_year": year - 71,
            "explain": f"{year} 年门槛：卒年 <= {year - 71}",
        },
        "author_death_50": {
            "desc": "作者/译者卒年 + 50 < 当年（life+50 辖区，默认不用）",
            "max_death_year": year - 51,
            "explain": f"{year} 年门槛：卒年 <= {year - 51}（除非明确目标辖区）",
        },
        "cc0_explicit": {
            "desc": "权利人主动以 CC0 放弃权利（需提供 CC0 声明链接）",
            "explain": "须有 CC0 声明页作为 evidence_url",
        },
        "government_work_pd": {
            "desc": "政府作品 / 无版权声明（须逐案证据）",
            "explain": "须提供具体依据链接",
        },
    }


def load_sources(path: str = SOURCES_FILE) -> dict:
    if not os.path.exists(path):
        sys.exit(f"缺少来源登记表：{path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("sources", {})


def check_source(source_id: str, sources: dict) -> list[str]:
    """来源闸门。返回问题列表，空列表 = 通过。"""
    problems = []
    s = sources.get(source_id)
    if s is None:
        return [f"来源「{source_id}」未在 licenses/sources.json 登记 —— 说不清来源就是风险，拒绝"]
    if s.get("blocked"):
        problems.append(f"来源「{source_id}」已被标记禁用：{s.get('blocked_reason', '')}")
    if s.get("license_checked") is not True:
        problems.append(
            f"来源「{source_id}」的 license_checked 不为 true —— "
            f"必须有人真的读过授权原文并记录日期（当前：{s.get('checked_on')}）")
    if not s.get("checked_evidence"):
        problems.append(f"来源「{source_id}」缺少 checked_evidence（读到的是什么）")
    if s.get("license") not in OPEN_LICENSES:
        problems.append(f"来源「{source_id}」的许可 {s.get('license')} 不在允许清单内")
    if not s.get("license_url"):
        problems.append(f"来源「{source_id}」缺少 license_url")
    return problems


def validate_rights(rec: dict, sources: dict) -> list[str]:
    """单条记录的权利块闸门。rec 含 rights 块字段 + 可选 pd_basis/卒年。"""
    problems = []
    for f in REQUIRED_FIELDS:
        if not rec.get(f):
            problems.append(f"权利块缺字段 `{f}`")
    if problems:
        return problems

    problems += check_source(rec["source_id"], sources)
    if problems:
        return problems

    declared = sources[rec["source_id"]].get("license")
    if rec["license"] != declared:
        problems.append(f"记录声明 {rec['license']} 与来源登记的 {declared} 不一致 —— "
                        f"拒绝（防止偷偷换源）")

    lic = rec["license"]
    if lic.startswith("PD"):
        basis = rec.get("pd_basis")
        if not basis:
            problems.append("标为公有领域但未给 pd_basis —— 「网上有」不是依据")
            return problems
        rules = pd_rules()
        if basis not in rules:
            problems.append(f"未知的 pd_basis：{basis}")
            return problems
        r = rules[basis]
        if basis == "us_published_before_1930":
            y = rec.get("published_year")
            if not y or y > r["max_published_year"]:
                problems.append(f"pd_basis 不成立：出版年 {y} > 门槛 {r['max_published_year']}")
        elif basis in ("author_death_70", "author_death_50"):
            y = rec.get("death_year")
            if not y or y > r["max_death_year"]:
                problems.append(f"pd_basis 不成立：卒年 {y} > 门槛 {r['max_death_year']} "
                                f"（译者按译者的卒年算！）")
    if rec.get("share_alike"):
        problems.append("允许但需注意：该来源为 ShareAlike，衍生作品须同协议发布")
    return problems


# ---------------------------------------------------------------- 自测
CASES = [
    # (名称, 记录, 期望通过?, 期望命中的关键词)
    ("① 通过：唐代诗，MIT 来源已核实", {
        "source_id": "mit_test_source",
        "source_url": "https://example.org/tang.json",
        "retrieved_on": "2026-10-09", "license": "MIT",
        "rights_holder": "Test Rights Holder",
        "evidence_url": "https://example.org/LICENSE",
        "verified_by": "poetry-tree",
    }, True, ""),

    ("② 拦：来源未登记", {
        "source_id": "some_random_blog", "source_url": "https://blog.example/x",
        "retrieved_on": "2026-10-09", "license": "MIT", "rights_holder": "?",
        "evidence_url": "https://blog.example/x", "verified_by": "poetry-tree",
    }, False, "未在 licenses/sources.json 登记"),

    ("③ 拦：来源未核实授权（license_checked=false）", {
        "source_id": "unverified_test_source", "source_url": "https://example.org/poem",
        "retrieved_on": "2026-10-09", "license": "PD-US",
        "rights_holder": "Some Author",
        "evidence_url": "https://example.org/copyright",
        "verified_by": "poetry-tree",
    }, False, "license_checked"),

    ("④ 拦：标 PD 但没给依据", {
        "source_id": "pd_test_source", "source_url": "https://example.org/book",
        "retrieved_on": "2026-10-09", "license": "PD-US",
        "rights_holder": "Some Author",
        "evidence_url": "https://example.org/copyright",
        "verified_by": "poetry-tree",
    }, False, "未给 pd_basis"),

    ("⑤ 拦：GPL-3.0 来源（已标记禁用）", {
        "source_id": "blocked_test_source",
        "source_url": "https://example.org/gpl-artifact.db",
        "retrieved_on": "2026-10-09", "license": "GPL-3.0",
        "rights_holder": "someone",
        "evidence_url": "https://example.org/LICENSE",
        "verified_by": "poetry-tree",
    }, False, "已被标记禁用"),

    ("⑥ 拦：PD 依据不成立（出版年超门槛）", {
        "source_id": "pd_test_source", "source_url": "https://example.org/book",
        "retrieved_on": "2026-10-09", "license": "PD-US", "pd_basis": "us_published_before_1930",
        "published_year": 1931,
        "rights_holder": "Some Author", "evidence_url": "https://example.org/copyright",
        "verified_by": "poetry-tree",
    }, False, "pd_basis 不成立：出版年 1931"),

    ("⑦ 拦：译者未过保护期（Arberry 卒 1969）", {
        "source_id": "pd_test_source", "source_url": "https://example.org/rumi",
        "retrieved_on": "2026-10-09", "license": "PD-US", "pd_basis": "author_death_70",
        "death_year": 1969,
        "rights_holder": "A. J. Arberry", "evidence_url": "https://example.org/copyright",
        "verified_by": "poetry-tree",
    }, False, "卒年 1969"),

    ("⑧ 通过：原诗 PD + 老译本 PD（卒 1945）", {
        "source_id": "pd_test_source", "source_url": "https://example.org/rubaiyat",
        "retrieved_on": "2026-10-09", "license": "PD-US", "pd_basis": "author_death_70",
        "death_year": 1945,
        "rights_holder": "Edward FitzGerald (translator)",
        "evidence_url": "https://example.org/copyright", "verified_by": "poetry-tree",
    }, True, ""),
]

# ---------------------------------------------------------------------------
# 自测必须**自足**：只使用下面的测试桩，绝不读真实登记表。
#
# 教训：原来用例③直接引用登记表里的 gutenberg，期望它 license_checked=false。
# 等我真的去核了 Gutenberg 授权、把它置为 true 之后，③就失败了 ——
# 测试被真实数据的状态绑住，一变就红。测试要用自己的桩，才能稳定表达规则本身。
# ---------------------------------------------------------------------------
FIXTURES = {
    "mit_test_source": {
        "name": "测试用 MIT 来源", "homepage": "https://example.org",
        "license": "MIT", "license_url": "https://example.org/LICENSE",
        "rights_holder": "Test Rights Holder", "license_checked": True,
        "checked_on": "2026-10-09", "checked_evidence": "测试桩：已核实的 MIT 来源",
        "attribution_required": True, "commercial_ok": True, "share_alike": False,
    },
    "unverified_test_source": {
        "name": "测试用未核实来源", "homepage": "https://example.org",
        "license": "PD-US", "license_url": "https://example.org/copyright",
        "rights_holder": "various", "license_checked": False,
        "checked_on": None, "checked_evidence": None,
        "attribution_required": False, "commercial_ok": True, "share_alike": False,
    },
    "pd_test_source": {
        "name": "测试用 PD 来源", "homepage": "https://example.org",
        "license": "PD-US", "license_url": "https://example.org/copyright",
        "rights_holder": "various", "license_checked": True, "checked_on": "2026-10-09",
        "checked_evidence": "测试桩：模拟一个已核实为公有领域的来源",
        "attribution_required": False, "commercial_ok": True, "share_alike": False,
    },
    "blocked_test_source": {
        "name": "测试用被禁来源", "homepage": "https://example.org",
        "license": "GPL-3.0", "license_url": "https://example.org/LICENSE",
        "rights_holder": "someone", "license_checked": True, "checked_on": "2026-10-09",
        "checked_evidence": "测试桩：模拟一个 GPL-3.0 且已标记禁用的来源",
        "attribution_required": True, "commercial_ok": True, "share_alike": True,
        "blocked": True, "blocked_reason": "测试桩：GPL-3.0 项目的产物，本项目不使用",
    },
}



def translator_trap_demo(year: int = CURRENT_YEAR) -> list[str]:
    """译者陷阱：原作者已 PD，不代表译文已 PD。"""
    r = pd_rules(year)
    out = [f"（{year} 年，life+70 门槛：译者卒年 <= {r['author_death_70']['max_death_year']}）"]
    rows = [
        ("鲁米原诗（卒 1273）", 1273, True),
        ("R. A. Nicholson 译鲁米（卒 1945）", 1945, None),
        ("A. J. Arberry 译鲁米（卒 1969）", 1969, None),
        ("松尾芭蕉原文（卒 1694）", 1694, True),
        ("R. H. Blyth 译芭蕉（卒 1964）", 1964, None),
        ("Fitzgerald 译《鲁拜集》（卒 1883）", 1883, True),
    ]
    for name, death, _ in rows:
        ok = death <= r["author_death_70"]["max_death_year"]
        out.append(f"  {'✅ 可用' if ok else '❌ 不可用'}  {name}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check-source", metavar="ID")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--thresholds", action="store_true")
    ap.add_argument("--year", type=int, default=CURRENT_YEAR)
    a = ap.parse_args()

    src = load_sources()

    if a.thresholds:
        print(f"公有领域门槛（{a.year} 年）")
        for k, v in pd_rules(a.year).items():
            print(f"  {k:<26}{v['explain']}")
        print()
        for line in translator_trap_demo(a.year):
            print(line)
        return

    if a.list:
        print(f"来源登记表（{len(src)} 条）")
        for sid, s in src.items():
            flag = "⛔ 禁用" if s.get("blocked") else ("✅ 可构建" if s.get("license_checked") else "⏳ 待核实")
            print(f"  {flag}  {sid:<32}{s.get('license'):<14}{s.get('name')}")
        return

    if a.check_source:
        problems = check_source(a.check_source, src)
        if problems:
            print(f"❌ {a.check_source} 未通过：")
            for p in problems:
                print(f"    · {p}")
            sys.exit(1)
        print(f"✅ {a.check_source} 通过")
        return

    if a.selftest:
        print("授权闸门自测\n" + "=" * 62)
        bad = 0
        src_test = dict(FIXTURES)              # 只用测试桩，不读真实登记表（保证自足）
        for name, rec, want_pass, kw in CASES:
            problems = validate_rights(rec, src_test)
            got_pass = not problems
            ok = (got_pass == want_pass)
            if want_pass is False and kw:
                ok = ok and any(kw in p for p in problems)
            bad += 0 if ok else 1
            print(f"{'✓' if ok else '✗'} {name}")
            for p in problems:
                print(f"      → {p}")
        print("=" * 62)
        print("\n译者陷阱演示：")
        for line in translator_trap_demo():
            print(line)
        print(f"\n{'✅ 全部通过' if not bad else f'❌ {bad} 项不符预期'}")
        sys.exit(1 if bad else 0)

    ap.print_help()


if __name__ == "__main__":
    main()
