"""原子化模组：数据类、JSON 规范校验与加载。

每个模组是一个自包含的 JSON 文件（编写规范见 docs/MODULE_SPEC.md），
可引用 SRD 内置怪物，也可在文件内定义 custom_monsters 使模组完全自包含。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..rules import data as D

MAX_MODULE_BYTES = 4 * 1024 * 1024
ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")

_REQUIRED_TOP = ("id", "title", "setting", "synopsis",
                 "act_titles", "epilogue", "scenes", "encounters")


def _err(errors: list[str], msg: str) -> None:
    errors.append(msg)


def _check_scene_chain(scenes: list[dict], errors: list[str]) -> None:
    ids = [s.get("id") for s in scenes]
    if len(ids) != len(set(ids)):
        _err(errors, "场景 id 存在重复")
        return
    seen: set[str] = set()
    cur = scenes[0]["id"]
    while cur is not None:
        if cur in seen:
            _err(errors, f"场景链出现环：{cur}")
            return
        seen.add(cur)
        scene = next((s for s in scenes if s["id"] == cur), None)
        if scene is None:
            _err(errors, f"场景链断裂：{cur} 不存在")
            return
        cur = scene.get("next")
    if len(seen) != len(scenes):
        unreachable = [s["id"] for s in scenes if s["id"] not in seen]
        _err(errors, f"存在链外的孤场景：{unreachable}")


def validate_module(data: dict) -> tuple[list[str], list[str]]:
    """校验模组 JSON。返回 (errors, warnings)。errors 非空则不可用。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["模组必须是 JSON 对象"], []
    for key in _REQUIRED_TOP:
        if key not in data:
            _err(errors, f"缺少顶层字段 {key}")
    if errors:
        return errors, warnings

    mid = str(data["id"])
    if not (1 <= len(mid) <= 48) or not all(c.isalnum() or c in "-_" for c in mid):
        _err(errors, f"模组 id 只允许字母/数字/-/_（1-48 字符）：{mid!r}")

    # ---- 场景
    scenes = data["scenes"]
    if not isinstance(scenes, list) or not scenes:
        _err(errors, "scenes 必须是非空数组")
        return errors, warnings
    valid_skills = set(D.SKILLS)
    for i, s in enumerate(scenes):
        for key in ("id", "act", "location", "intro", "next"):
            if key not in s:
                _err(errors, f"场景[{i}] 缺少字段 {key}")
        act = s.get("act")
        if not isinstance(act, int) or not (1 <= act <= 9):
            _err(errors, f"场景[{i}] 的 act 必须是 1-9 的整数")
        if s.get("encounter") and s["encounter"] not in data["encounters"]:
            _err(errors, f"场景 {s.get('id')} 引用了未定义的遭遇 {s.get('encounter')}")
        for sk in s.get("skills", []) or []:
            if sk.get("skill") not in valid_skills:
                _err(errors, f"场景 {s.get('id')} 使用了未知技能 {sk.get('skill')}")
            dc = sk.get("dc", 0)
            if not isinstance(dc, (int, float)) or not (5 <= dc <= 30):
                _err(errors, f"场景 {s.get('id')} 的检定 DC 应在 5-30")
        lt = s.get("level_to")
        if lt is not None and (not isinstance(lt, int) or not (1 <= lt <= 5)):
            _err(errors, f"场景 {s.get('id')} 的 level_to 必须是 1-5")
    _check_scene_chain(scenes, errors)

    # ---- 怪物表
    monster_table = set(D.MONSTERS) | set(data.get("custom_monsters", {}) or {})
    for eid, enc in data["encounters"].items():
        if not enc.get("monsters"):
            _err(errors, f"遭遇 {eid} 没有怪物")
            continue
        for m in enc["monsters"]:
            if not (isinstance(m, list) and len(m) == 2):
                _err(errors, f"遭遇 {eid} 的怪物应写作 [\"怪物id\", 数量]")
                continue
            if m[0] not in monster_table:
                _err(errors, f"遭遇 {eid} 引用了未定义的怪物 {m[0]}（可加入 custom_monsters）")
            if not isinstance(m[1], int) or m[1] < 1:
                _err(errors, f"遭遇 {eid} 的怪物数量非法")

    # ---- 经验预算（警告级）
    total_xp = 0
    for eid, enc in data["encounters"].items():
        for m in enc.get("monsters", []):
            table = D.MONSTERS.get(m[0]) or (data.get("custom_monsters") or {}).get(m[0]) or {}
            total_xp += int(table.get("xp", 0)) * int(m[1])
    milestones = sum(int(s.get("milestone") or 0) for s in scenes)
    per_player = (total_xp + milestones) // 4
    if per_player < 900:
        warnings.append(f"经验预算偏少：四人队人均约 {per_player} XP"
                        f"（升到 3 级需要 900；可增加遭遇或 milestone）")
    elif per_player > 6600:
        warnings.append(f"经验预算偏多：人均约 {per_player} XP（超出 5 级上限）")

    # ---- 依赖（可选，Cordis 插件语义：requires: [其他模组id]）
    req = data.get("requires") or []
    if not isinstance(req, list) or not all(isinstance(r, str) for r in req):
        _err(errors, "requires 必须是模组 id 字符串数组")
    else:
        if len(req) != len(set(req)):
            _err(errors, "requires 中存在重复的模组 id")
        if str(data["id"]) in req:
            _err(errors, "requires 不能引用自身（形成环）")
        for r in req:
            if not (1 <= len(r) <= 48) or not all(c.isalnum() or c in "-_" for c in r):
                _err(errors, f"requires 中的模组 id 非法：{r!r}")

    # ---- 同伴（可选）
    for c in data.get("companions", []) or []:
        for key in ("id", "name", "race", "klass", "background", "scores", "skills"):
            if key not in c:
                _err(errors, f"同伴 {c.get('name', '?')} 缺少字段 {key}")
        if c.get("race") not in D.RACES:
            _err(errors, f"同伴 {c.get('name', '?')} 的种族 {c.get('race')} 未定义")
        if c.get("klass") not in D.CLASSES:
            _err(errors, f"同伴 {c.get('name', '?')} 的职业 {c.get('klass')} 未定义")
        if c.get("background") not in D.BACKGROUNDS:
            _err(errors, f"同伴 {c.get('name', '?')} 的背景 {c.get('background')} 未定义")
        scores = c.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(ABILITY_KEYS):
            _err(errors, f"同伴 {c.get('name', '?')} 的 scores 需要完整六项属性")

    # ---- 自定义怪物（字段与 SRD 表同构，此处做基本检查）
    for cid, m in (data.get("custom_monsters") or {}).items():
        for key in ("name", "xp", "hp", "ac", "abilities", "attacks"):
            if key not in m:
                _err(errors, f"自定义怪物 {cid} 缺少字段 {key}")

    return errors, warnings


class Module:
    """一个已加载的模组实例（对游戏引擎暴露与内置模组相同的访问接口）。"""

    def __init__(self, data: dict, source: str = "imported", path: str | None = None):
        self.id: str = str(data["id"])
        self.source = source          # builtin | imported | generated
        self.path = path
        self.title: str = data["title"]
        self.en_title: str = data.get("en_title", "")
        self.setting: str = data.get("setting", "")
        self.synopsis: str = data.get("synopsis", "")
        self.party_level_start: int = int(data.get("party_level_start", 1))
        self.expected_end_level: int = int(data.get("expected_end_level", 3))
        self.act_titles: dict[int, str] = {int(k): v
                                           for k, v in (data.get("act_titles") or {}).items()}
        self.epilogue: str = data.get("epilogue", "—— 冒险结束 ——")
        self.npcs: dict = data.get("npcs", {}) or {}
        self.companions: list[dict] = data.get("companions", []) or []
        self.requires: list[str] = [str(r) for r in (data.get("requires") or [])]
        self.encounters: dict = data["encounters"]
        self.scenes: list[dict] = data["scenes"]
        self.custom_monsters: dict = data.get("custom_monsters", {}) or {}
        self.monster_table: dict = {**D.MONSTERS, **self.custom_monsters}
        self.warnings: list[str] = data.get("_warnings", [])

    # ------------------------------------------------------------ 访问
    def scene_by_id(self, scene_id: str) -> dict | None:
        return next((s for s in self.scenes if s["id"] == scene_id), None)

    def first_scene_id(self) -> str:
        return self.scenes[0]["id"]

    def encounter(self, encounter_id: str) -> dict | None:
        return self.encounters.get(encounter_id)

    def handbook(self) -> str:
        lines = [f"模组：{self.title}（{self.en_title}）".rstrip("（）"),
                 self.synopsis, ""]
        for act in sorted({s["act"] for s in self.scenes}):
            lines.append(f"== 第{act}幕：{self.act_titles.get(act, '')} ==")
            for s in self.scenes:
                if s["act"] == act:
                    lines.append(f"[{s['id']}] {s['location']}")
                    lines.append("  " + s["intro"].replace("\n", " ")[:120])
            lines.append("")
        lines.append("== 关键NPC ==")
        for n in self.npcs.values():
            lines.append(f"{n['name']}（{n.get('race', '?')}）：{n['desc']}")
        return "\n".join(lines)

    def summary(self) -> dict:
        return {
            "id": self.id, "title": self.title, "en_title": self.en_title,
            "source": self.source, "setting": self.setting,
            "synopsis": self.synopsis,
            "acts": len(self.act_titles), "scenes": len(self.scenes),
            "encounters": len(self.encounters),
            "companions": len(self.companions),
            "warnings": self.warnings,
            "has_custom_monsters": bool(self.custom_monsters),
        }

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_dict(cls, data: dict, source: str = "imported",
                  path: str | None = None) -> tuple["Module | None", list[str], list[str]]:
        """校验并构造。返回 (module|None, errors, warnings)。"""
        errors, warnings = validate_module(data)
        if errors:
            return None, errors, warnings
        return cls(data, source=source, path=path), [], warnings

    @classmethod
    def from_file(cls, path: Path) -> tuple["Module | None", list[str], list[str]]:
        raw = path.read_bytes()
        if len(raw) > MAX_MODULE_BYTES:
            return None, [f"模组文件过大（>{MAX_MODULE_BYTES // 1024}KB）"], []
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            return None, [f"JSON 解析失败：{e}"], []
        source = "builtin" if path.name == "starfall" or "builtin" in str(path) else "imported"
        return cls.from_dict(data, source=source, path=str(path))
