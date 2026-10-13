"""原子化模组：数据类、JSON 规范校验与加载。

每个模组是一个自包含的 JSON 文件（新规范见 ``_work/design/PORT_SPEC.md`` §3），
可引用规则层内置的 NPC 数据表（``D.NPCS``），也可在文件内定义 ``custom_npcs``
使模组完全自包含。

规则体系为《赛博朋克 RED》轻松模式：十项属性（``D.STAT_ORDER``）、41 项技能
（``D.SKILLS``）、难度值 DV（``D.DV_MIN..D.DV_MAX``）、改进点 IP、欧元 eb。
"""

from __future__ import annotations

import json
from pathlib import Path
from ..rules import data as D

MAX_MODULE_BYTES = 4 * 1024 * 1024
ASSET_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".svg")
MAP_TOKEN_KINDS = ("pc", "npc", "monster", "prop")

#: 内置（随发行版附带）模组的目录名；``cordis.BUILTIN_IDS`` 以此为唯一来源。
BUILTIN_MODULE_IDS: frozenset[str] = frozenset({"cpred-easy-mode"})

#: 全篇改进点总量的软上限（超出只警告，不拦截）
IP_SOFT_CAP = 4000

_REQUIRED_TOP = ("id", "title", "setting", "synopsis",
                 "act_titles", "epilogue", "scenes", "encounters")


def _err(errors: list[str], msg: str) -> None:
    errors.append(msg)


def _is_int(value) -> bool:
    """严格整数判定（``bool`` 不算整数）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _keys_of(value) -> set[str]:
    """把 ``[key, ...]`` 或 ``{key: ...}`` 两种写法统一取出 key 集合。"""
    if isinstance(value, dict):
        return {str(k) for k in value}
    if isinstance(value, (list, tuple)):
        return {str(v) for v in value}
    return set()


def _check_asset_path(path) -> bool:
    """模组资产相对路径安全检查（相对 modules/<id>/，禁绝对路径与上跳）。"""
    if not isinstance(path, str) or not path:
        return False
    p = path.replace("\\", "/")
    if p.startswith(("/", "~")) or ":" in p.split("/")[0]:
        return False
    if ".." in p.split("/"):
        return False
    return p.lower().endswith(ASSET_EXTS)


def _check_scene_map(s: dict, errors: list[str]) -> None:
    """场景战术地图声明（docs/battlemap-design.md §2）：全部可选字段。"""
    mp = s.get("map")
    if mp is None:
        return
    sid = s.get("id")
    if not isinstance(mp, dict):
        _err(errors, f"场景 {sid} 的 map 必须是对象")
        return
    image = mp.get("image")
    if image is not None and not _check_asset_path(image):
        _err(errors, f"场景 {sid} 的 map.image 非法：{image!r}"
                     f"（相对模组目录，扩展名 {'/'.join(ASSET_EXTS)}，禁止上跳）")
    grid = mp.get("grid")
    if not (isinstance(grid, list) and len(grid) == 2
            and all(isinstance(v, int) and 4 <= v <= 64 for v in grid)):
        _err(errors, f"场景 {sid} 的 map.grid 必须是 [宽,高]（4-64 格）")
        return
    w, h = grid
    ig = mp.get("image_grid")
    if ig is not None and (not isinstance(ig, list) or len(ig) != 2
                           or not all(isinstance(v, int) and 4 <= v <= 128 for v in ig)):
        _err(errors, f"场景 {sid} 的 map.image_grid 必须是 [宽,高]（4-128 格）")
    crop = mp.get("crop")
    if crop is not None:
        if (not isinstance(crop, list) or len(crop) != 4
                or not all(isinstance(v, int) and v >= 0 for v in crop)):
            _err(errors, f"场景 {sid} 的 map.crop 必须是 [x,y,w,h]")
        else:
            src = ig or grid
            if crop[2] < 4 or crop[3] < 4 or crop[0] + crop[2] > src[0] \
                    or crop[1] + crop[3] > src[1]:
                _err(errors, f"场景 {sid} 的 map.crop {crop} 超出源图网格 {src}")
    taken: set[tuple[int, int]] = set()
    for o in mp.get("obstacles") or []:
        if not (isinstance(o, (list, tuple)) and len(o) == 2
                and all(isinstance(v, int) for v in o)
                and 0 <= o[0] < w and 0 <= o[1] < h):
            _err(errors, f"场景 {sid} 的障碍格 {o!r} 越界或非法")
        else:
            taken.add((o[0], o[1]))
    ps = mp.get("party_spawn")
    if ps is not None and (not isinstance(ps, (list, tuple)) or len(ps) != 2
                           or not all(isinstance(v, int) for v in ps)
                           or not (0 <= ps[0] < w and 0 <= ps[1] < h)
                           or (ps[0], ps[1]) in taken):
        _err(errors, f"场景 {sid} 的 map.party_spawn 越界或落在障碍上")
    # 掩体：{材质: [[x,y], ...]}，材质取规则层 COVER 表；与障碍格不重叠
    cover_spec = mp.get("cover")
    if cover_spec is not None:
        if not isinstance(cover_spec, dict):
            _err(errors, f"场景 {sid} 的 map.cover 必须是对象（材质 → 格坐标数组）")
        else:
            covered: set[tuple[int, int]] = set()
            for material, cells in cover_spec.items():
                if material not in D.COVER:
                    _err(errors, f"场景 {sid} 的掩体材质 {material!r} 未定义"
                                 f"（可用：{'/'.join(D.COVER)}）")
                if not isinstance(cells, (list, tuple)) or not cells:
                    _err(errors, f"场景 {sid} 的掩体 {material} 必须是非空格数组")
                    continue
                for cell in cells:
                    if not (isinstance(cell, (list, tuple)) and len(cell) == 2
                            and all(isinstance(v, int) for v in cell)
                            and 0 <= cell[0] < w and 0 <= cell[1] < h):
                        _err(errors, f"场景 {sid} 的掩体格 {cell!r} 越界或非法")
                        continue
                    c = (cell[0], cell[1])
                    if c in taken:
                        _err(errors, f"场景 {sid} 的掩体格 {list(c)} 与障碍格重叠"
                                     f"（掩体不挡视线，实心障碍请只写进 obstacles）")
                    covered.add(c)
            overlap = covered & taken
            if overlap:
                _err(errors, f"场景 {sid} 的掩体与障碍重叠：{sorted(overlap)}")
    seen_tokens: set[str] = set()
    for t in mp.get("tokens") or []:
        tid = t.get("id") if isinstance(t, dict) else None
        if not tid or tid in seen_tokens:
            _err(errors, f"场景 {sid} 的 map.tokens 存在缺失/重复的 id")
            continue
        seen_tokens.add(str(tid))
        if t.get("kind") is not None and t["kind"] not in MAP_TOKEN_KINDS:
            _err(errors, f"场景 {sid} 棋子 {tid} 的 kind 必须是 {'/'.join(MAP_TOKEN_KINDS)}")
        cell = t.get("cell")
        if (not isinstance(cell, (list, tuple)) or len(cell) != 2
                or not all(isinstance(v, int) for v in cell)
                or not (0 <= cell[0] < w and 0 <= cell[1] < h)
                or (cell[0], cell[1]) in taken):
            _err(errors, f"场景 {sid} 棋子 {tid} 的落格 {cell!r} 越界/障碍/非法")
        else:
            taken.add((cell[0], cell[1]))


def _check_encounter_spawns(eid: str, enc: dict, errors: list[str]) -> None:
    """遭遇敌人初始布点：spawns = {npc_id: [[x,y], ...]}（格坐标，1 格 = 2 米）。"""
    spawns = enc.get("spawns")
    if spawns is None:
        return
    if not isinstance(spawns, dict):
        _err(errors, f"遭遇 {eid} 的 spawns 必须是对象（NPC id → 布点数组）")
        return
    known = {m[0] for m in enc.get("enemies", []) or []
             if isinstance(m, (list, tuple)) and len(m) == 2}
    for nid, cells in spawns.items():
        if nid not in known:
            _err(errors, f"遭遇 {eid} 的 spawns 引用了未参战的 NPC {nid}")
        if not isinstance(cells, list) or not all(
                isinstance(c, (list, tuple)) and len(c) == 2
                and all(isinstance(v, int) and 0 <= v <= 64 for v in c)
                for c in cells):
            _err(errors, f"遭遇 {eid} 的 NPC {nid} 布点必须是 [[x,y], ...] 整数格坐标")


def _check_scene_chain(scenes: list[dict], errors: list[str]) -> None:
    """场景链：从第一场沿 next 走到唯一 null 终点；无环、无孤立场景。"""
    ids = [s.get("id") for s in scenes if isinstance(s, dict)]
    if not ids or not all(isinstance(i, str) and i for i in ids):
        return                     # 缺失/非法 id 已由场景字段检查报告
    if len(ids) != len(set(ids)):
        _err(errors, "场景 id 存在重复")
        return
    seen: set[str] = set()
    cur = ids[0]
    while cur is not None:
        if cur in seen:
            _err(errors, f"场景链出现环：{cur}")
            return
        seen.add(cur)
        scene = next((s for s in scenes
                      if isinstance(s, dict) and s.get("id") == cur), None)
        if scene is None:
            _err(errors, f"场景链断裂：{cur} 不存在")
            return
        cur = scene.get("next")
    if len(seen) != len(scenes):
        unreachable = [s.get("id") for s in scenes if s.get("id") not in seen]
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

    # ---- 幕标题（key 必须是 1-9 的幕号）
    act_titles = data.get("act_titles")
    if not isinstance(act_titles, dict):
        _err(errors, "act_titles 必须是对象（幕号 → 标题）")
    else:
        for k in act_titles:
            try:
                num = int(k)
            except (TypeError, ValueError):
                _err(errors, f"act_titles 的幕号 {k!r} 必须是 1-9 的整数")
                continue
            if not (1 <= num <= 9):
                _err(errors, f"act_titles 的幕号 {k!r} 必须是 1-9 的整数")

    # ---- 遭遇表（top 字段，必须先于场景检查取键）
    encounters = data["encounters"]
    if not isinstance(encounters, dict):
        _err(errors, "encounters 必须是对象（id → 遭遇）")
        encounters = {}

    # ---- 自定义 NPC（可选）：至少需要 name/stats/skills/weapons
    custom = data.get("custom_npcs") or {}
    if not isinstance(custom, dict):
        _err(errors, "custom_npcs 必须是对象（key → NPC 数据块）")
        custom = {}
    for cid, block in custom.items():
        if not isinstance(block, dict):
            _err(errors, f"自定义 NPC {cid} 必须是对象")
            continue
        for key in ("name", "stats", "skills", "weapons"):
            if key not in block:
                _err(errors, f"自定义 NPC {cid} 缺少字段 {key}")
        stats = block.get("stats")
        if stats is not None and not isinstance(stats, dict):
            _err(errors, f"自定义 NPC {cid} 的 stats 必须是对象（十项属性）")
        elif isinstance(stats, dict):
            for k in stats:
                if k not in D.STAT_ORDER:
                    _err(errors, f"自定义 NPC {cid} 的属性 {k} 未定义")
        skills = block.get("skills")
        if skills is not None and not isinstance(skills, dict):
            _err(errors, f"自定义 NPC {cid} 的 skills 必须是对象（技能 → 等级）")
        elif isinstance(skills, dict):
            for sk in skills:
                if sk not in D.SKILLS:
                    _err(errors, f"自定义 NPC {cid} 使用了未知技能 {sk}")
        weapons = block.get("weapons")
        if weapons is not None:
            wks = _keys_of(weapons)
            if not wks:
                _err(errors, f"自定义 NPC {cid} 的 weapons 必须是非空武器 key 数组")
            for wk in wks:
                if wk not in D.WEAPONS:
                    _err(errors, f"自定义 NPC {cid} 使用了未知武器 {wk}")
        armor = block.get("armor")            # NPC 的护甲直接写 SP 数值
        if armor is not None and (not _is_int(armor) or armor < 0):
            _err(errors, f"自定义 NPC {cid} 的 armor 必须是非负整数（护甲 SP）")

    # ---- 场景
    scenes = data["scenes"]
    if not isinstance(scenes, list) or not scenes:
        _err(errors, "scenes 必须是非空数组")
        return errors, warnings
    for i, s in enumerate(scenes):
        if not isinstance(s, dict):
            _err(errors, f"场景[{i}] 必须是对象")
            continue
        for key in ("id", "act", "location", "intro", "next"):
            if key not in s:
                _err(errors, f"场景[{i}] 缺少字段 {key}")
        act = s.get("act")
        if not _is_int(act) or not (1 <= act <= 9):
            _err(errors, f"场景[{i}] 的 act 必须是 1-9 的整数")
        enc_id = s.get("encounter")
        if enc_id and enc_id not in encounters:
            _err(errors, f"场景 {s.get('id')} 引用了未定义的遭遇 {enc_id}")
        checks = s.get("checks") or []
        if not isinstance(checks, list):
            _err(errors, f"场景 {s.get('id')} 的 checks 必须是数组")
            checks = []
        for ck in checks:
            if not isinstance(ck, dict):
                _err(errors, f"场景 {s.get('id')} 的检定必须是对象")
                continue
            if ck.get("skill") not in D.SKILLS:
                _err(errors, f"场景 {s.get('id')} 使用了未知技能 {ck.get('skill')}")
            dv = ck.get("dv")
            if not _is_int(dv) or not (D.DV_MIN <= dv <= D.DV_MAX):
                _err(errors, f"场景 {s.get('id')} 的检定难度值 DV 应在 "
                             f"{D.DV_MIN}-{D.DV_MAX}")
        ip = s.get("ip")
        if ip is not None and (not _is_int(ip) or ip < 0):
            _err(errors, f"场景 {s.get('id')} 的 ip 必须是非负整数")
        _check_scene_map(s, errors)
    _check_scene_chain(scenes, errors)

    # ---- 棋子图标库（可选）：tokens = {NPC/物件 id: 资产路径}
    tokens = data.get("tokens") or {}
    if not isinstance(tokens, dict):
        _err(errors, "tokens 必须是对象（id → 资产路径）")
    else:
        for tid, path in tokens.items():
            if not _check_asset_path(path):
                _err(errors, f"棋子图标 {tid} 的路径非法：{path!r}")

    # ---- 遭遇：enemies = [[NPC id, 数量], ...]
    npc_table = set(D.NPCS) | set(custom)
    for eid, enc in encounters.items():
        if not isinstance(enc, dict):
            _err(errors, f"遭遇 {eid} 必须是对象")
            continue
        enemies = enc.get("enemies")
        if not enemies or not isinstance(enemies, list):
            _err(errors, f"遭遇 {eid} 没有敌人"
                         f"（enemies: [[\"NPC id\", 数量], ...]）")
            continue
        _check_encounter_spawns(eid, enc, errors)
        for m in enemies:
            if not (isinstance(m, (list, tuple)) and len(m) == 2):
                _err(errors, f"遭遇 {eid} 的敌人应写作 [\"NPC id\", 数量]")
                continue
            if m[0] not in npc_table:
                _err(errors, f"遭遇 {eid} 引用了未定义的 NPC {m[0]}"
                             f"（可加入 custom_npcs）")
            if not _is_int(m[1]) or m[1] < 1:
                _err(errors, f"遭遇 {eid} 的敌人数量非法")

    # ---- 改进点预算（警告级）：全篇总量与软上限
    total_ip = 0
    ip_declared = False
    for s in scenes:
        if not isinstance(s, dict):
            continue
        ip = s.get("ip")
        if ip is None:
            continue
        ip_declared = True
        if _is_int(ip):
            total_ip += ip
    if total_ip <= 0:
        warnings.append("模组没有任何场景发放改进点 IP，队伍将无法成长")
    elif total_ip > IP_SOFT_CAP:
        warnings.append(f"改进点预算偏多：全篇共 {total_ip} IP"
                        f"（软上限 {IP_SOFT_CAP}）")
    if not encounters:
        warnings.append("模组没有任何遭遇（encounters 为空），全程不会有战斗")

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

    # ---- 预设角色（可选）：轻松模式的五名角色卡同构数据
    pregens = data.get("pregens") or {}
    if not isinstance(pregens, dict):
        _err(errors, "pregens 必须是对象（key → 预设角色）")
        pregens = {}
    for pk, pg in pregens.items():
        if not isinstance(pg, dict):
            _err(errors, f"预设角色 {pk} 必须是对象")
            continue
        label = f"预设角色 {pk}"
        for key in ("name", "role", "stats"):
            if key not in pg:
                _err(errors, f"{label} 缺少字段 {key}")
        role = pg.get("role")
        if role not in D.ROLES:
            _err(errors, f"{label} 的职业 {role!r} 未定义（见 D.ROLES）")
        stats = pg.get("stats")
        if not isinstance(stats, dict):
            _err(errors, f"{label} 的 stats 必须是对象（十项属性）")
        else:
            missing = [k for k in D.STAT_ORDER if k not in stats]
            if missing:
                _err(errors, f"{label} 的 stats 需要完整十项属性，"
                             f"缺少 {'/'.join(missing)}")
            for k, v in stats.items():
                if k not in D.STAT_ORDER:
                    _err(errors, f"{label} 的属性 {k} 未定义")
                elif not _is_int(v) or v < 1:
                    _err(errors, f"{label} 的属性 {k} 必须是正整数")
        levels = pg.get("skill_levels") or {}
        if not isinstance(levels, dict):
            _err(errors, f"{label} 的 skill_levels 必须是对象（技能 → 等级）")
        else:
            for sk, lv in levels.items():
                if sk not in D.SKILLS:
                    _err(errors, f"{label} 使用了未知技能 {sk}")
                elif not _is_int(lv) or not (0 <= lv <= D.SKILL_MAX):
                    _err(errors, f"{label} 的技能 {sk} 等级应在 0-{D.SKILL_MAX}")
        for wk in _keys_of(pg.get("weapons")):
            if wk not in D.WEAPONS:
                _err(errors, f"{label} 使用了未知武器 {wk}")
        armor = pg.get("armor")
        if armor is not None and armor not in D.ARMORS:
            _err(errors, f"{label} 的护甲 {armor!r} 未定义（见 D.ARMORS）")
        for cw in _keys_of(pg.get("cyberware")):
            if cw not in D.CYBERWARE:
                _err(errors, f"{label} 使用了未知义体 {cw}")

    # ---- 默认队友：必须是已定义的预设角色
    dc = data.get("default_companions") or []
    if not isinstance(dc, list) or not all(isinstance(x, str) for x in dc):
        _err(errors, "default_companions 必须是预设角色 key 数组")
    else:
        for ck in dc:
            if ck not in pregens:
                _err(errors, f"default_companions 引用了未定义的预设角色 {ck}")

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
        self.act_titles: dict[int, str] = {int(k): v
                                           for k, v in (data.get("act_titles") or {}).items()}
        self.epilogue: str = data.get("epilogue", "—— 行动结束 ——")
        self.npcs: dict = data.get("npcs", {}) or {}
        # 预设角色（轻松模式的角色卡）：key → 数据块
        self.pregens: dict = data.get("pregens", {}) or {}
        # 玩家未另选队友时启用的预设角色 key
        self.default_companions: list[str] = [str(k) for k in
                                              (data.get("default_companions") or [])]
        self.requires: list[str] = [str(r) for r in (data.get("requires") or [])]
        # 棋子图标库（docs/battlemap-design.md §2）：{NPC/物件 id: 资产路径}
        self.tokens: dict = {str(k): str(v)
                             for k, v in (data.get("tokens") or {}).items()}
        self.token_icons: dict = dict(self.tokens)
        # DM 卡扩展（ST 组装线）：persona/voice/system_prompt/depth_prompt/book
        self.dm: dict = data.get("dm") if isinstance(data.get("dm"), dict) else {}
        self.encounters: dict = data["encounters"]
        self.scenes: list[dict] = data["scenes"]
        # 自定义 NPC 与内置 NPC 数据表合并后的敌人表（key → 数据块）
        self.custom_npcs: dict = data.get("custom_npcs", {}) or {}
        self.npc_table: dict = {**D.NPCS, **self.custom_npcs}
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
                    lines.append("  " + str(s.get("intro", "")).replace("\n", " ")[:120])
            lines.append("")
        if self.pregens:
            lines.append("== 预设角色（职业 · 生命值 PW）==")
            for k, pg in self.pregens.items():
                role = D.ROLES.get(pg.get("role"), {}).get("name", pg.get("role", "?"))
                st = pg.get("stats") or {}
                body, will = int(st.get("body", 5)), int(st.get("will", 5))
                hp = pg.get("hp") or 10 + 5 * ((body + will + 1) // 2)
                lines.append(f"[{k}] {pg.get('name', k)}（{role} · PW {hp}）")
            lines.append("")
        lines.append("== 关键NPC ==")
        for n in self.npcs.values():
            lines.append(f"{n.get('name', '?')}（{n.get('role', '?')}）：{n.get('desc', '')}")
        return "\n".join(lines)

    def summary(self) -> dict:
        return {
            "id": self.id, "title": self.title, "en_title": self.en_title,
            "source": self.source, "setting": self.setting,
            "synopsis": self.synopsis,
            "acts": len(self.act_titles), "scenes": len(self.scenes),
            "encounters": len(self.encounters),
            "pregens": len(self.pregens),
            "default_companions": list(self.default_companions),
            "npcs": len(self.npcs),
            "warnings": self.warnings,
            "has_custom_npcs": bool(self.custom_npcs),
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
        source = "builtin" if path.parent.name in BUILTIN_MODULE_IDS \
            or "builtin" in str(path) else "imported"
        return cls.from_dict(data, source=source, path=str(path))
