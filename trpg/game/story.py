"""剧本模式（互动剧）：自写分支剧本的节点图演绎引擎。

数据：modules/<module_id>/story_*.json —— 与模组同目录分发（剧本模式基于
同世界观与人物创作）。本仓库当前不内置任何剧本数据：没有剧本文件时
load_stories 返回空列表（UI 显示"没有剧本数据"），引擎不会崩。
节点/选项语义与 Godot 版 scripts/story_mode.gd 完全一致，同一份剧本数据
两个引擎通用：
  节点：chapter / text（或 variants 条件文本）/ next / branch（条件跳转）/
        choices（见下）/ ending（id/title/rank/text）
  选项：text；require 显示条件 {flags:[...], not_flags:[...]}；goto 直跳；
        check {skill, dv} → 1d10 + 属性 + 技能 vs 难度值 DV（《赛博朋克 RED》
        口径，必须高于 DV），success/fail 各带 {goto,set,damage}；
        set 进入选项即写入旗标；damage 扣 PW，归零进入 death_ending。
UI 鸭子接口（ConsoleUI / WebUI / AutoUI 均已满足）：
  raw / system_line / choose / check_line / status_board / scene_header。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from ..rules import data as D
from ..rules.character import Character
from ..rules.dice import check_roll as _dice_check

UNLOCKS_FILE = Path("saves/story_endings.json")
MAX_STEPS = 200

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def skill_name(key: str) -> str:
    return D.SKILLS.get(key, {}).get("name", key)


def _dv_of(ck: dict, default: int = 15) -> int:
    """检定难度值：``dv`` 为准（RED），``dc`` 作为旧字段兜底。"""
    raw = ck.get("dv", ck.get("dc", default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


# ================================================================ 数据加载
def validate_story(data: dict) -> list[str]:
    """校验剧本 JSON。返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["剧本必须是 JSON 对象"]
    nodes = data.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return ["剧本缺少 nodes"]
    start = str(data.get("start", ""))
    if start not in nodes:
        errors.append(f"start 节点缺失：{start}")
    de = str(data.get("death_ending", ""))
    if de and de not in nodes:
        errors.append(f"death_ending 缺失：{de}")
    heroes = data.get("heroes")
    if not isinstance(heroes, list) or not heroes:
        errors.append("剧本缺少 heroes")
    for h in heroes or []:
        for key in ("id", "name", "title", "hp", "skills"):
            if key not in h:
                errors.append(f"主角 {h.get('name', '?')} 缺少字段 {key}")

    refs: list[str] = []
    if start:
        refs.append(start)
    if de:
        refs.append(de)

    def _walk_choice(cid: str, c: dict) -> None:
        if not isinstance(c, dict):
            errors.append(f"节点 {cid} 存在非对象选项")
            return
        if "goto" in c:
            refs.append(str(c["goto"]))
        ck = c.get("check")
        if isinstance(ck, dict):
            skill = str(ck.get("skill", ""))
            if skill not in D.SKILLS:
                errors.append(f"节点 {cid} 的检定使用了未知技能 {skill!r}")
            dv = _dv_of(ck, 0)
            if not D.DV_MIN <= dv <= D.DV_MAX:
                errors.append(f"节点 {cid} 的检定难度值 DV 应在 "
                              f"{D.DV_MIN}-{D.DV_MAX}：{dv}")
        for key in ("success", "fail"):
            branch = c.get(key)
            if isinstance(branch, dict):
                if "goto" in branch:
                    refs.append(str(branch["goto"]))
                elif "check" in c:
                    errors.append(f"节点 {cid} 的选项 {c.get('text', '?')!r} "
                                  f"{key} 分支缺少 goto")
        if "check" in c and "success" not in c:
            errors.append(f"节点 {cid} 的检定选项 {c.get('text', '?')!r} 缺少 success 分支")

    for nid, n in nodes.items():
        if not isinstance(n, dict):
            errors.append(f"节点 {nid} 不是对象")
            continue
        if "next" in n:
            refs.append(str(n["next"]))
        for b in n.get("branch", []) or []:
            if isinstance(b, dict) and "goto" in b:
                refs.append(str(b["goto"]))
        for c in n.get("choices", []) or []:
            _walk_choice(str(nid), c)

    for r in refs:
        if r and r not in nodes:
            errors.append(f"悬空引用：{r}")

    endings = [nid for nid, n in nodes.items()
               if isinstance(n, dict) and n.get("ending")]
    for nid in endings:
        n = nodes[nid]
        if not str(n.get("title", "")) or not str(n.get("text", "")):
            errors.append(f"结局 {nid} 缺少 title/text")
    want = int(data.get("endings_total", len(endings)))
    if len(endings) != want:
        errors.append(f"结局数 {len(endings)} 与声明 {want} 不一致")
    return errors


def iter_story_files(root: str | Path = "modules") -> list[tuple[str, Path]]:
    """扫描 modules/<module_id>/story_*.json。返回 [(module_id, path)]。"""
    out: list[tuple[str, Path]] = []
    rootp = Path(root)
    if not rootp.exists():
        return out
    for d in sorted(rootp.iterdir()):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("story_*.json")):
            out.append((d.name, f))
    return out


def load_story_file(path: str | Path) -> tuple[dict | None, list[str]]:
    """加载并校验单个剧本文件。返回 (story|None, errors)。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, [f"{Path(path).name} 读取失败：{e}"]
    errors = validate_story(data)
    return (None if errors else data), errors


def load_stories(root: str | Path = "modules") -> tuple[list[dict], list[str]]:
    """加载全部剧本（跳过损坏文件）。返回 (stories, errors)。"""
    stories: list[dict] = []
    errors: list[str] = []
    for module_id, path in iter_story_files(root):
        story, errs = load_story_file(path)
        if story is None:
            errors.extend(errs)
            continue
        story["_module_id"] = module_id
        stories.append(story)
    return stories, errors


# ================================================================ 结局图鉴
def ending_ids(story: dict) -> list[str]:
    """剧本全部结局节点 id（按 nodes 声明顺序）。"""
    return [nid for nid, n in story.get("nodes", {}).items()
            if isinstance(n, dict) and n.get("ending")]


def reachable_endings(story: dict) -> list[str]:
    """从 start 出发（忽略条件，过近似）静态可达的结局 id——多结局预设验证。"""
    nodes = story.get("nodes", {})
    start = str(story.get("start", ""))
    if start not in nodes:
        return []
    seen: set[str] = set()
    queue = [start]
    seen.add(start)
    while queue:
        node = nodes.get(queue.pop(0))
        if not isinstance(node, dict):
            continue
        targets: list[str] = []
        if "next" in node:
            targets.append(str(node["next"]))
        for b in node.get("branch", []) or []:
            if isinstance(b, dict):
                targets.append(str(b.get("goto", "")))
        for c in node.get("choices", []) or []:
            if not isinstance(c, dict):
                continue
            targets.append(str(c.get("goto", "")))
            for key in ("success", "fail"):
                branch = c.get(key)
                if isinstance(branch, dict):
                    targets.append(str(branch.get("goto", "")))
        for t in targets:
            if t and t in nodes and t not in seen:
                seen.add(t)
                queue.append(t)
    return [e for e in ending_ids(story) if e in seen]


def load_unlocks(path: str | Path = UNLOCKS_FILE) -> dict:
    """结局图鉴：{story_id: [ending_id, ...]}。文件缺失/损坏返回空。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def record_unlock(story_id: str, ending_id: str,
                  path: str | Path = UNLOCKS_FILE) -> list[str]:
    """记录达成的结局。返回该剧本更新后的解锁列表。"""
    if not story_id or not ending_id:
        return []
    data = load_unlocks(path)
    unlocked = [e for e in data.get(story_id, []) if isinstance(e, str)]
    if ending_id not in unlocked:
        unlocked.append(ending_id)
    data[story_id] = unlocked
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass
    return unlocked


# ================================================================ 引擎
def _parse_chapter(ch: str) -> tuple[int, str] | None:
    """『第一幕 · 庆功宴』→ (1, "庆功宴")；非幕式标题返回 None。"""
    m = re.match(r"^第([0-9一二三四五六七八九十]+)幕\s*[·：:]?\s*(.*)$", ch.strip())
    if not m:
        return None
    raw = m.group(1)
    if raw.isdigit():
        act = int(raw)
    elif "十" in raw:
        left, _, right = raw.partition("十")
        act = (_CN_NUM.get(left, 1) * 10 if left else 10) + _CN_NUM.get(right, 0)
    else:
        act = _CN_NUM.get(raw, 0)
    if act <= 0:
        return None
    rest = m.group(2).strip()
    return act, (rest or ch.strip())


class StorySession:
    """一场剧本演绎：节点图游走 → 分支选择 → 多结局。

    run() 返回达成结局的节点字典（含 id/title/rank/text）；图损坏时抛
    ValueError（开局前 validate_story 已挡住，运行期到达即引擎 bug）。"""

    def __init__(self, story: dict, ui, rng: random.Random | None = None):
        self.story = story
        self.ui = ui
        self.rng = rng if rng is not None else random.Random()
        self.hero: dict = {}
        self.hero_char: Character | None = None
        self.hp = 0
        self.hp_max = 0
        self.flags: dict = {}
        self.node_id = ""
        self.steps = 0

    # ------------------------------------------------------------ 入口
    def set_hero(self, idx: int) -> None:
        heroes = self.story.get("heroes", [])
        if not 0 <= idx < len(heroes):
            raise ValueError(f"主角序号越界：{idx}")
        self.hero = heroes[idx]
        self.hero_char = self._build_hero(self.hero)
        self.hp_max = int(self.hero.get("hp") or self.hero_char.hp_max or 10)
        self.hero_char.hp_max = self.hp_max
        self.hero_char.hp_cur = self.hp_max
        self.hp = self.hp_max
        self.flags[f"hero_{self.hero.get('id', '')}"] = "1"
        self._push_board()

    @staticmethod
    def _build_hero(hero: dict) -> Character:
        """剧本主角 → 规则层角色卡（RED：十项属性 + 技能值）。"""
        stats = {k: int(v) for k, v in (hero.get("stats") or {}).items()}
        for key in D.STAT_ORDER:
            stats.setdefault(key, 5)
        raw_skills = hero.get("skills") or {}
        skills = {}
        for key, value in raw_skills.items():
            if key in D.SKILLS:
                skills[key] = int(value)
        ch = Character.create(name=str(hero.get("name") or "主角"),
                              role=str(hero.get("role") or "solo"),
                              stats=stats, skill_levels=skills,
                              role_rank=int(hero.get("role_rank", 4)))
        if hero.get("handle"):
            ch.handle = str(hero["handle"])
        if hero.get("title"):
            ch.desc = str(hero["title"])
        return ch

    def run(self) -> dict:
        self.steps = 0
        nodes = self.story.get("nodes", {})
        self.ui.system_line(f"《{self.story.get('title', '')}》 —— 互动剧本"
                            + (f"（{self.story.get('en_title', '')}）"
                               if self.story.get("en_title") else ""))
        self.ui.raw(self.story.get("synopsis", ""))
        self.ui.system_line(
            f"你扮演：{self.hero.get('name', '?')}（{self.hero.get('title', '?')}）"
            f"—— {self.hero.get('desc', '')}")
        self.node_id = str(self.story.get("start", ""))
        while self.steps < MAX_STEPS:
            self.steps += 1
            node = nodes.get(self.node_id)
            if not isinstance(node, dict):
                raise ValueError(f"剧本节点缺失：{self.node_id}")
            if node.get("ending"):
                # 结局节点本身不带 id，但结局图鉴要用 id 记账（record_unlock）
                out = dict(node)
                out.setdefault("id", self.node_id)
                return out
            self.node_id = self._play_node(node)
            if not self.node_id:
                raise ValueError(f"剧本节点无去处（引擎 bug）：{self.node_id}")
        raise ValueError("剧本演绎步数超限（疑似死循环）")

    # ------------------------------------------------------------ 节点演绎
    def _play_node(self, node: dict) -> str:
        chapter = str(node.get("chapter", ""))
        if chapter:
            parsed = _parse_chapter(chapter)
            if parsed:
                act, rest = parsed
                self.ui.scene_header(act, f"第{act}幕：{rest}", rest)
            else:
                self.ui.system_line(chapter)
        for para in str(self._node_text(node)).split("\n"):
            if para.strip():
                self.ui.raw(para)
        choices = self._available_choices(node)
        nxt = self._resolve_next(node)
        if not choices:
            return nxt
        opts = [str(c.get("text", "…")) for c in choices]
        idx = self.ui.choose(str(node.get("prompt", "—— 你的选择 ——")), opts)
        if not 0 <= idx < len(choices):
            idx = 0
        ch = choices[idx]
        self._apply_set(ch)
        dmg = int(ch.get("damage", 0) or 0)
        if "check" in ch:
            ck = ch["check"]
            result = self._do_check(str(ck.get("skill", "perception")),
                                    _dv_of(ck, 15))
            branch = ch["success"] if result["success"] else ch.get("fail") or {}
            if isinstance(branch, dict):
                self._apply_set(branch)
                dmg += int(branch.get("damage", 0) or 0)
                if "goto" in branch:
                    nxt = str(branch["goto"])
        elif "goto" in ch:
            nxt = str(ch["goto"])
        if dmg > 0:
            self._hurt(dmg)
        if self.hp <= 0 and self.story.get("death_ending"):
            return str(self.story["death_ending"])
        return nxt

    def _do_check(self, skill_key: str, dv: int) -> dict:
        """《赛博朋克 RED》检定：1d10 + 属性 + 技能 vs 难度值 DV
        （由 trpg.rules 结算：Character.check / dice.check_roll）。"""
        ch = self.hero_char
        label = skill_name(skill_key)
        dv = int(dv)
        mods: list[str] = []
        if ch is not None and skill_key in D.SKILLS:
            res = ch.check(self.rng, skill_key, dv=dv)
            info = D.SKILLS[skill_key]
            mods = [f"{D.STATS[info['stat']]['name']}+{ch.stat(info['stat'])}",
                    f"{info['name']}+{ch.skill(skill_key)}"]
            if ch.check_penalty():
                mods.append(f"伤势{ch.check_penalty():+d}")
            self.hp = max(0, ch.hp_cur)
        else:                                  # 没有角色卡时的兜底：只掷 1d10
            res = _dice_check(self.rng, 0, dv=dv)
        ok = bool(res.success)
        self.ui.check_line({
            "kind": "check",
            "who": self.hero.get("name", ""),
            "label": label,
            "check": label,
            "skill": skill_key,
            "dv": dv,
            "detail": res.detail(),
            "mods": mods,
            "rolls": list(res.rolls),
            "expr": res.expr,
            "modifier": res.modifier,
            "total": res.total,
            "success": ok,
            "crit_success": res.crit_success,
            "crit_failure": res.crit_failure,
            "luck_spent": res.luck_spent,
        })
        self._push_board()
        return {"success": ok, "total": res.total, "dv": dv,
                "crit_success": res.crit_success, "crit_failure": res.crit_failure}

    def _hurt(self, n: int) -> None:
        n = max(0, int(n))
        if self.hero_char is not None:
            self.hero_char.take_damage(n, ignore_armor=True)   # 剧情伤害无视护甲
            self.hp = max(0, self.hero_char.hp_cur)
        else:
            self.hp = max(0, self.hp - n)
        self.ui.system_line(f"（PW −{n} …… 剩余 {self.hp}/{self.hp_max}）")
        self._push_board()
        if self.hp <= 0:
            self.ui.system_line("眼前发黑。你听见自己的心跳，慢了下来……")

    def _push_board(self) -> None:
        if not self.hero:
            return
        wound = self.hero_char.wound_name() if self.hero_char is not None else ""
        line = f"PW {self.hp}/{self.hp_max}" + (f"（{wound}）" if wound else "")
        self.ui.status_board([
            f"{self.hero.get('name', '')}（{self.hero.get('title', '')}）",
            line,
        ])

    # ------------------------------------------------------------ 条件与取值
    def _cond_ok(self, cond) -> bool:
        if not isinstance(cond, dict):
            return True
        for f in cond.get("flags", []) or []:
            if str(f) not in self.flags:
                return False
        for f in cond.get("not_flags", []) or []:
            if str(f) in self.flags:
                return False
        return True

    def _apply_set(self, d: dict) -> None:
        s = d.get("set")
        if isinstance(s, dict):
            for k, v in s.items():
                self.flags[str(k)] = str(v)

    def _node_text(self, node: dict) -> str:
        for v in node.get("variants", []) or []:
            if isinstance(v, dict) and self._cond_ok(v.get("require")):
                return str(v.get("text", ""))
        return str(node.get("text", ""))

    def _resolve_next(self, node: dict) -> str:
        for b in node.get("branch", []) or []:
            if isinstance(b, dict) and self._cond_ok(b.get("require")):
                return str(b.get("goto", ""))
        return str(node.get("next", ""))

    def _available_choices(self, node: dict) -> list[dict]:
        out: list[dict] = []
        for c in node.get("choices", []) or []:
            if not isinstance(c, dict):
                continue
            if not self._cond_ok(c.get("require")):
                continue
            if "goto" not in c and "check" not in c:
                continue
            out.append(c)
        return out
