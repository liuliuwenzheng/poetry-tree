# -*- coding: utf-8 -*-
"""接口侧的授权层 —— **复用构建闸门那一份登记表，不另建一套**。

设计要点：

1. 登记表只有一份：`licenses/sources.json`。构建流程（`corpus/rights.py`）
   和接口（这里）读同一个文件。否则两边会漂移，出现「构建说不能收、接口却在服务」
   这种最危险的状态。
2. **fail-closed 延伸到服务期**：库里出现了登记表里没有的来源，启动就失败。
   不能「构建时严格、服务时宽松」——那等于第一原则没落地。
3. 政策里写明的一条接口义务：**必须能按 license 过滤**，
   让无法履行 ShareAlike 的下游用户能排除 CC-BY-SA 记录。
   所以这里提供 `license` / `share_alike` / `commercial_ok` 三个筛选维度。
"""
import json
import os
import sys

# 让 api/ 能 import 到 corpus/rights.py（同仓库，两个包）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.models import Rights, SourceInfo  # noqa: E402

try:                                  # 与构建闸门共用同一套判定常量
    from corpus.rights import SHARE_ALIKE, OPEN_LICENSES  # noqa: E402
except Exception:                     # pragma: no cover - 独立运行时兜底
    SHARE_ALIKE = {"CC-BY-SA-4.0", "CC-BY-SA-3.0", "GPL-3.0"}
    OPEN_LICENSES = {"PD-US", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "MIT"}

# 数据库里 `source` 列的取值 → 登记表键。
# 必须**显式**列出而不是靠猜：映射不上就是要人去登记，不是让程序蒙一个。
SOURCE_KEY_ALIASES = {
    "chinese-poetry": "chinese_poetry",
    "chinese_poetry": "chinese_poetry",
    "werneror": "werneror_poetry",
    "werneror-poetry": "werneror_poetry",
    "gutenberg": "gutenberg",
    "project-gutenberg": "gutenberg",
    "wikisource": "wikisource",
}


class UnregisteredSource(RuntimeError):
    """库里出现了未登记来源 —— 说不清授权的数据不许服务。"""


class RightsRegistry:
    """来源登记表的只读视图。"""

    def __init__(self, sources_json: str):
        self.path = sources_json
        with open(sources_json, encoding="utf-8") as f:
            raw = json.load(f)
        self.schema_version = raw.get("schema_version")
        self.policy = raw.get("_policy") or {}
        self._sources: dict[str, dict] = raw.get("sources") or {}

    # -------------------------------------------------------------- 内部映射
    def resolve_key(self, db_source: str) -> str | None:
        """把库里的 `source` 值解析成登记表键。解析不出返回 None（调用方决定怎么办）。"""
        s = (db_source or "").strip()
        if not s:
            return None
        if s in self._sources:
            return s
        if s in SOURCE_KEY_ALIASES and SOURCE_KEY_ALIASES[s] in self._sources:
            return SOURCE_KEY_ALIASES[s]
        norm = s.lower().replace("-", "_").replace(" ", "_")
        if norm in self._sources:
            return norm
        # 到此为止。**刻意不猜**：不做前缀/模糊/包含匹配。
        # 否则 `chinese_poetry_2024` 这种没登记的东西会悄悄蹭上 `chinese_poetry`
        # 的 MIT 许可——那就正好绕过了启动审计，把第一原则变成了摆设。
        # 解析不出来 = 要人去登记，不是让程序蒙一个。
        return None

    def get(self, db_source: str) -> Rights | None:
        """库里的 source 值 → 权利块。未登记返回 None。"""
        key = self.resolve_key(db_source)
        if not key:
            return None
        s = self._sources[key]
        return self._to_rights(key, s)

    def require(self, db_source: str) -> Rights:
        """同上，但未登记直接抛 —— 供「不许宽」的路径用。"""
        r = self.get(db_source)
        if r is None:
            raise UnregisteredSource(
                f"来源「{db_source}」未在 {os.path.basename(self.path)} 登记。"
                f"说不清来源与许可的文本不予服务 —— 先登记，再上线。")
        return r

    # -------------------------------------------------------------- 转模型
    @staticmethod
    def _to_rights(key: str, s: dict) -> Rights:
        return Rights(
            source=key,
            source_name=s.get("name"),
            homepage=s.get("homepage"),
            license=s.get("license"),
            license_url=s.get("license_url"),
            rights_holder=s.get("rights_holder"),
            attribution_required=bool(s.get("attribution_required")),
            commercial_ok=bool(s.get("commercial_ok", True)),
            share_alike=bool(s.get("share_alike")),
            per_item_check_required=bool(s.get("per_item_check_required")),
            license_checked=bool(s.get("license_checked")),
            checked_on=str(s["checked_on"]) if s.get("checked_on") else None,
            roles=list(s.get("roles") or []),
            note=(s.get("notes") or "").strip() or None,
        )

    def to_source_info(self, key: str) -> SourceInfo:
        s = self._sources[key]
        base = self._to_rights(key, s)
        return SourceInfo(
            **base.model_dump(),
            key=key,
            language=list(s.get("language") or []),
            blocked=bool(s.get("blocked")),
            blocked_reason=(s.get("blocked_reason") or "").strip() or None,
            checked_evidence=(s.get("checked_evidence") or "").strip() or None,
        )

    def all_sources(self, include_blocked: bool = True) -> list[SourceInfo]:
        out = []
        for k, s in self._sources.items():
            if s.get("blocked") and not include_blocked:
                continue
            out.append(self.to_source_info(k))
        return sorted(out, key=lambda x: (x.blocked, x.key))

    def usable_keys(self) -> list[str]:
        """可用的来源：登记过 + 已核实 + 未被禁用。"""
        return [k for k, s in self._sources.items()
                if s.get("license_checked") and not s.get("blocked")]

    # -------------------------------------------------------------- 启动审计
    def audit(self, db_sources: list[str]) -> list[str]:
        """审计库里出现过的来源。返回问题列表（空 = 通过）。

        这是 fail-closed 从构建期延伸到服务期的那一步：数据在库里的每一行
        都必须能说出授权，否则不许上线。
        """
        problems = []
        for src in sorted(set(db_sources)):
            s = (src or "").strip()
            if not s:
                problems.append("存在 source 为空的记录 —— 无法追溯来源")
                continue
            key = self.resolve_key(s)
            if not key:
                problems.append(
                    f"来源「{s}」未登记（去 licenses/sources.json 补一条，"
                    f"附上真的读到的授权原文）")
                continue
            rec = self._sources[key]
            if rec.get("blocked"):
                problems.append(f"来源「{s}」已被标记禁用：{rec.get('blocked_reason')}")
            if not rec.get("license_checked"):
                problems.append(f"来源「{s}」的 license_checked 不为 true —— 未核实不得服务")
            if not rec.get("license"):
                problems.append(f"来源「{s}」没有 license 标识（SPDX）")
        return problems

    # -------------------------------------------------------------- 筛选语义
    def license_filters(self) -> dict:
        """给接口文档用：可选的 license 取值。"""
        lic = {s["license"] for s in self._sources.values() if s.get("license")}
        sa = {s["license"] for s in self._sources.values()
              if s.get("share_alike") and s.get("license")}
        return {
            "licenses": sorted(lic),
            "share_alike": sorted(sa),
            "open": sorted(OPEN_LICENSES),
        }
