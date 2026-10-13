"""tests/ 共享夹具与工具 —— 《赛博朋克 RED》口径。

本模块把原先散落在各测试文件里的重复 helper 收敛到一处（移植前的重复清单：
五份 ``make_pc``、三份 ``make_state``、两份 ``_RollGateUI``、两份
``http_post/http_get``、两份 HTTP 服务器 setUp/tearDown、三份按角色名发牌的
scripted adapter）。所有模组夹具锚定在 ``__file__`` 上，不依赖 cwd。

术语一律 RED：属性 STAT / 技能 skill / 难度值 DV / 生命值 PW / 护甲 SP /
幸运 / 人性 Humanity / 欧元 eb / 改进点 IP / 职业 Role。旧规则的术语与数据面
一律不再出现。

导入方式：``from helpers import ...``（``unittest discover -s tests`` 会把
``tests/`` 放进 sys.path）。本模块同时把仓库根插进 sys.path，因此
``from tests.test_x import y`` 这种包式导入也能用。
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

# ---------------------------------------------------------------- 路径锚点
TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent                     # 仓库根

for _p in (str(TESTS_DIR), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODULES_DIR = ROOT / "modules"
RED_MODULE_ID = "cpred-easy-mode"
RED_MODULE_PATH = MODULES_DIR / RED_MODULE_ID / "module.json"

#: LLM 相关环境变量（测试必须离线：进入服务器/会话夹具前清空）
LLM_ENV_VARS: tuple[str, ...] = (
    "CTRCG_LLM_API_KEY", "CTRCG_LLM_BASE_URL", "CTRCG_LLM_MODEL",
    "CTRCG_LLM_PROVIDER", "CTRCG_LLM_ALLOW_PRIVATE",
    "DEEPSEEK_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY",
    "MOONSHOT_API_KEY", "MINIMAX_API_KEY",
)


def strip_llm_env() -> dict:
    """清空一切可能让适配器"看起来已配置"的环境变量；返回原值快照。"""
    saved = {}
    for key in LLM_ENV_VARS:
        saved[key] = os.environ.pop(key, None)
    return saved


def restore_env(saved: dict) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# ================================================================ 确定性掷骰
class ScriptRng:
    """剧本化 RNG：``randint`` 按给定序列出值（越界钳到区间内），用尽后回落
    到 ``random.Random(seed)``。

    RED 引擎需要精确值断言（暴击追加、护甲磨损、致命伤 2d6、生存检定门槛），
    真随机数无法稳定复现，故整条套件统一用它。
    """

    def __init__(self, values, seed: int = 0):
        self.values = list(values)
        self.calls: list[tuple[int, int]] = []
        self._fallback = random.Random(seed)

    def randint(self, a: int, b: int) -> int:
        self.calls.append((a, b))
        if self.values:
            v = int(self.values.pop(0))
        else:
            v = self._fallback.randint(a, b)
        return max(a, min(b, v))

    # 引擎只调用 randint / random；给出可复现的 random 兜底
    def random(self) -> float:
        return self._fallback.random()

    def getstate(self):
        return self._fallback.getstate()

    def setstate(self, state):
        self._fallback.setstate(state)


# ================================================================ 模组夹具
def red_module_data() -> dict:
    """随包发行的唯一 RED 模组（modules/cpred-easy-mode/module.json）原始数据。"""
    return json.loads(RED_MODULE_PATH.read_text(encoding="utf-8"))


def red_module():
    """加载随包发行的 RED 模组实例。"""
    from ctrcg.campaign.loader import Module
    mod, errors, _warnings = Module.from_file(RED_MODULE_PATH)
    if mod is None:                      # pragma: no cover —— 发行数据损坏
        raise AssertionError(f"内置 RED 模组未通过校验：{errors}")
    return mod


def red_pregen_keys(mod=None) -> list[str]:
    mod = mod or red_module()
    return list(mod.pregens)


def red_pregen(key: str, mod=None) -> dict:
    return (mod or red_module()).pregens[key]


def fixture_module_data() -> dict:
    """完全自包含的内存模组（RED 规范）：2 场景 + 1 遭遇 + 1 自定义 NPC。

    零磁盘依赖，供规则层/编排层测试使用；想用随包发行的模组时用
    :func:`red_module` / ``make_state(red=True)``。
    """
    return {
        "id": "fixture-red",
        "title": "测试模组：雨夜交割",
        "en_title": "Test Fixture: Rain Handoff",
        "setting": "2045 年，夜之城海伍德工业区，酸雨。",
        "synopsis": "一箱假钞、一个中间人，和几条不该出现的枪。",
        "act_titles": {"1": "第一幕：交易"},
        "epilogue": "—— 冒险结束 ——",
        "npcs": {
            "salt_officer": {
                "name": "Salter",
                "role": "中间人",
                "desc": "码头上的盐税官，见人三分笑。",
                "persona": "圆滑的掮客，贪财但惜命，绝不硬碰。",
                "voice": "客客气气，句句留后路。",
                "goals": "保住私账，别被牵进假钞案。",
                "secret": "他见过夜里卸货的白西装，收了封口钱。",
            },
        },
        "encounters": {
            "e1": {
                "title": "巷口伏击",
                "intro": "雨幕里有人拉响了枪机。",
                "enemies": [["corrupt_cop", 2], ["salt_ghoul", 1]],
                "loot": {"eurobucks": 60, "items": {"ammo": 20}},
                "spawns": {"corrupt_cop": [[9, 2], [9, 5]], "salt_ghoul": [[8, 3]]},
            },
        },
        "custom_npcs": {
            "salt_ghoul": {
                "name": "盐渍食尸鬼",
                "stats": {"int": 3, "ref": 5, "dex": 5, "tech": 2, "cool": 3,
                          "will": 6, "luck": 3, "move": 4, "body": 7, "emp": 2},
                "skills": {"brawling": 6, "athletics": 4, "evasion": 2},
                "hp": 35, "armor": 7, "armor_name": "凯夫拉",
                "weapons": ["heavy_melee"], "notes": "嗑药嗑傻了的打手，专挑落单的。",
            },
        },
        "pregens": {
            "solo_x": {
                "name": "Kyle", "handle": "Wrench", "role": "solo", "role_rank": 4,
                "role_ability": "战斗直觉",
                "stats": {"int": 5, "ref": 7, "dex": 6, "tech": 4, "cool": 5,
                          "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 5},
                "hp": 45,
                "skill_levels": {"handgun": 6, "evasion": 4, "athletics": 4,
                                 "perception": 5, "brawling": 5},
                "armor": "light_armorjack",
                "weapons": ["heavy_pistol", "medium_melee"],
                "cyberware": [], "gear": {"ammo": 20},
                "eurobucks": 0,
                "persona": "话少活狠的老兵。", "voice": "短句。",
                "desc": "左脸一道旧疤，话比子弹少。",
            },
            "tech_bella": {
                "name": "Bella", "handle": "Iron", "role": "tech", "role_rank": 4,
                "role_ability": "改造",
                "stats": {"int": 7, "ref": 5, "dex": 5, "tech": 8, "cool": 5,
                          "will": 5, "luck": 5, "move": 4, "body": 5, "emp": 6},
                "hp": 35,
                "skill_levels": {"electronics_security": 6, "first_aid": 5,
                                 "perception": 5, "handgun": 4},
                "armor": "kevlar", "weapons": ["medium_pistol"],
                "cyberware": ["tool_hand"], "gear": {"toolkit": 1},
                "persona": "务实的老技工。", "voice": "简短，带行话。",
                "desc": "右手指尖永远有机油味。",
                "mes_example": ("<START>{{user}}：准备好了吗？\n"
                                "{{char}}：{{char}}从不迟到，{{user}}。"),
            },
            "fixer_olan": {
                "name": "Oran", "handle": "Ears", "role": "fixer", "role_rank": 4,
                "role_ability": "人脉",
                "stats": {"int": 6, "ref": 5, "dex": 5, "tech": 4, "cool": 7,
                          "will": 5, "luck": 5, "move": 4, "body": 5, "emp": 7},
                "hp": 35,
                "skill_levels": {"persuasion": 6, "conversation": 6,
                                 "human_perception": 5, "handgun": 4},
                "armor": "leather", "weapons": ["medium_pistol"],
                "cyberware": ["internal_agent"],
                "persona": "什么都听得见的掮客。", "voice": "引经据典地绕圈子。",
                "desc": "耳朵上永远挂着一副耳机。",
                "book": {"entries": [
                    {"keys": ["假钞"], "content": "假钞的纸张来自海伍德的旧印刷厂。",
                     "insertion_order": 90, "comment": "假钞"},
                ]},
            },
        },
        "default_companions": ["tech_bella", "fixer_olan"],
        "scenes": [
            {"id": "s1", "act": 1, "location": "海伍德 · 巷口",
             "intro": "雨把霓虹泡化成油膜。", "explore": "四处打探。",
             "checks": [{"skill": "perception", "dv": 15,
                         "success": "你注意到檐下的影子。",
                         "fail": "雨声盖住了一切。"}],
             "encounter": "e1", "next": "s2", "ip": 30,
             "map": {"grid": [11, 8], "label": "巷口",
                     "obstacles": [[5, 3]], "party_spawn": [1, 4],
                     "tokens": [{"id": "npc:salt_officer", "kind": "npc",
                                 "name": "Salter", "cell": [8, 4]}]}},
            {"id": "s2", "act": 1, "location": "海伍德 · 印刷厂",
             "intro": "卷帘门半开着。", "explore": "进去看看。",
             "checks": [], "encounter": None, "next": None, "ip": 20},
        ],
    }


def fixture_module():
    from ctrcg.campaign.loader import Module
    data = fixture_module_data()
    mod, errors, _w = Module.from_dict(data)
    if mod is None:                      # pragma: no cover —— 夹具自身损坏
        raise AssertionError(f"测试模组夹具未通过校验：{errors}")
    mod.source = "imported"
    return mod


# ================================================================ 角色工厂
#: 默认十项属性（PW = 10 + 5 × ⌈(体格 + 意志力)/2⌉ = 45）
DEFAULT_STATS = {"int": 5, "ref": 6, "dex": 6, "tech": 4, "cool": 5,
                 "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 6}
#: 默认技能等级（加值 = 属性 + 等级）
DEFAULT_SKILLS = {"handgun": 6, "evasion": 4, "brawling": 5,
                  "perception": 5, "athletics": 4}


def make_pc(name: str = "Kyle", role: str = "solo", stats: dict | None = None,
            skills: dict | None = None, *, hp: int | None = None,
            armor: str | None = "light_armorjack",
            weapons: list | None = None, cyberware: list | None = None,
            gear: dict | None = None, eurobucks: int = 0, ip: int = 0,
            kind: str = "pc", **kw):
    """RED 玩家角色工厂（``test_map`` / ``test_grid_combat`` 等文件按名导入）。

    与移植前同名同位置，但参数改为 RED 口径：职业 role + 十项属性 + 技能等级。
    """
    from ctrcg.rules.character import Character
    s = dict(DEFAULT_STATS)
    s.update(stats or {})
    c = Character.create(
        name=name, role=role, stats=s,
        skill_levels=dict(skills) if skills is not None else dict(DEFAULT_SKILLS),
        armor_key=armor,
        weapons=list(weapons) if weapons is not None else ["heavy_pistol"],
        cyberware=list(cyberware or []),
        gear=dict(gear or {}),
        eurobucks=eurobucks, kind=kind)
    c.ip = int(ip)
    if hp is not None:
        c.hp_max = int(hp)
        c.hp_cur = int(hp)
    for key, value in kw.items():
        if hasattr(c, key):
            setattr(c, key, value)
    return c


def make_npc(key: str = "corrupt_cop", tag: str = "", **overrides):
    """按 ``D.NPCS`` 数据块构造敌人。"""
    from ctrcg.rules.character import Character
    from ctrcg.rules import data as D
    block = dict(D.NPCS[key])
    block.update(overrides)
    return Character.from_statblock(key, block, tag=tag)


def make_companions(module=None, kinds=None) -> list:
    """按模组的 ``default_companions`` 装配 AI 队友（或指定 pregen key）。"""
    from ctrcg.rules.character import Character
    mod = module or fixture_module()
    keys = list(kinds) if kinds else list(mod.default_companions)
    return [Character.from_pregen(k, mod.pregens[k], kind="companion")
            for k in keys if k in mod.pregens]


# ================================================================ 游戏状态
def make_state(seed: int = 5, module=None, player=None, companions=None,
               red: bool = False, party: bool = True):
    """构造 ``GameState``（默认用内存夹具模组，``red=True`` 用随包模组）。

    收敛了移植前的三份 ``make_state``：玩家 + 模组默认队友 + 注入种子的 RNG。
    """
    from ctrcg.game import state as G
    if module is None:
        module = red_module() if red else fixture_module()
    if player is None:
        key = module.pregens and (list(module.pregens)[0]) or None
        if key:
            from ctrcg.rules.character import Character
            player = Character.from_pregen(key, module.pregens[key], player=True)
            player.name = "Kyle"
        else:
            player = make_pc()
    comps = list(companions) if companions is not None \
        else (make_companions(module) if party else [])
    return G.GameState(player, comps, random.Random(seed), module=module)


# ================================================================ UI 桩
def _auto_ui():
    from ctrcg.ui.cli import AutoUI
    return AutoUI()


class RollGateUI:
    """玩家掷骰闸门 UI 桩（移植前的 ``_RollGateUI``，两份合一）。

    记录 ``ask_roll`` 提示，供断言"玩家本人的检定走骰子按钮、AI 队友自动掷"。
    """

    def __init__(self):
        self.rolls: list[str] = []
        self.checks: list = []
        self.saves: list = []
        self.lines: list[str] = []
        self.board: list = []

    def ask_roll(self, prompt: str) -> bool:
        self.rolls.append(prompt)
        return True

    def check_line(self, result, context: str = ""):
        self.checks.append(result)
        return {"kind": "dice"} if isinstance(result, dict) else {}

    def save_line(self, result):
        self.saves.append(result)
        return {}

    def roll_event(self, ev: dict) -> None:
        self.saves.append(ev)

    def system_line(self, text: str) -> None:
        self.lines.append(text)

    def raw(self, text: str) -> None:
        self.lines.append(text)

    def scene_header(self, act, act_title, location) -> None:
        self.lines.append(f"{act}/{act_title}/{location}")

    def status_board(self, lines) -> None:
        self.board.append(list(lines))

    def map_update(self, brief) -> None:
        pass

    def dm_narration(self, text: str):
        self.lines.append(text)
        return {}

    def npc_line(self, npc: str, text: str):
        self.lines.append(f"{npc}:{text}")
        return {}

    def companion_line(self, name: str, text: str, interrupt: bool = False):
        self.lines.append(f"\u25c8 {name}:{text}")
        return {}

    def crit_injury(self, who: str, injury: dict, text: str = ""):
        self.lines.append(f"crit:{who}")
        return {}

    def choose(self, prompt: str, options: list) -> int:
        return 0

    def free_input(self, prompt: str) -> str:
        return "继续前进，留意枪口。"


class CountUI(RollGateUI):
    """在 :class:`RollGateUI` 之上统计"人类交互点"，用于断言零交互。"""

    def __init__(self):
        super().__init__()
        self.choose_calls = 0
        self.free_inputs = 0

    def choose(self, prompt: str, options: list) -> int:
        self.choose_calls += 1
        return 0

    def free_input(self, prompt: str) -> str:
        self.free_inputs += 1
        return "继续前进，留意枪口。"


class ScriptedUI:
    """剧本模式（互动剧）鸭子 UI：``first`` / ``last`` 选项策略。"""

    def __init__(self, policy: str = "first"):
        self.policy = policy
        self.raws: list[str] = []
        self.check_lines: list = []
        self.boards: list = []

    def raw(self, text: str) -> None:
        self.raws.append(text)

    def system_line(self, text: str) -> None:
        self.raws.append(text)

    def scene_header(self, act, act_title, location) -> None:
        self.raws.append(f"{act}/{act_title}/{location}")

    def status_board(self, lines) -> None:
        self.boards.append(list(lines))

    def check_line(self, payload, context: str = "") -> None:
        self.check_lines.append(payload)

    def choose(self, prompt: str, options: list) -> int:
        return 0 if self.policy == "first" else max(0, len(options) - 1)

    def free_input(self, prompt: str) -> str:
        return ""


# ================================================================ 会话夹具
def make_agent_session(handler=None, state=None, ui=None, script=None,
                       model: str = "test-model", pc_agents: bool = True,
                       module=None):
    """AI 模式会话（DM Agent + 每个同伴一个 PC Agent），零网络。

    返回 ``(session, state, ui, adapter)``。移植前这份装配逻辑在
    ``test_game`` / ``test_social`` / ``test_npc_agent`` / ``test_aipc`` 里
    各有一份，且都往提示词里塞旧规则的资源提示；现统一到 RED 口径。
    """
    from ctrcg.game.agents import make_dm_agent, make_pc_agent
    from ctrcg.game.session import GameSession
    from ctrcg.harness.adapter import ScriptedAdapter
    from ctrcg.harness.loop import DefaultAgentLoop

    state = state if state is not None else make_state()
    ui = ui if ui is not None else _auto_ui()
    adapter = ScriptedAdapter(script=script, handler=handler)
    loop = DefaultAgentLoop(registry=None, adapter=adapter)
    module = module or state.module
    dm = make_dm_agent(model, module)
    agents = {}
    if pc_agents:
        for comp in state.companions:
            agents[comp.name] = make_pc_agent(
                comp, comp.role_rank, list(comp.weapons),
                _status_hint(comp), model)
    session = GameSession(state, ui, loop=loop, dm_agent=dm,
                          pc_agents=agents, module=module)
    return session, state, ui, adapter


def _status_hint(c) -> str:
    """同伴的现状摘要串（RED：PW / 伤势 / 护甲 SP / 幸运）。"""
    return (f"PW {c.hp_cur}/{c.hp_max}；伤势 {c.wound_name()}；"
            f"护甲 {c.armor_name()} SP {c.armor_at('torso')}；"
            f"幸运 {c.luck_cur}/{c.stat('luck')}")


#: 各宿主提示词的稳定标记（按标记发牌用）
DM_MARK = "跑团的 DM"
NPC_MARK = "子智能体扮演"


def message_system(messages) -> str:
    """拼接**所有** system 消息（深度注入会把备忘插到系统提示之前，故
    ``messages[0]`` 不一定是系统提示）。"""
    return "\n".join(str(m.get("content", "")) for m in (messages or [])
                     if m.get("role") == "system")


def is_fresh_run(messages) -> bool:
    """最后一轮是 user ⇒ 新一轮（工具回填轮不算），保证每轮只发一张牌。"""
    return bool(messages) and messages[-1].get("role") == "user"


# ================================================================ HTTP
def http_get(port: int, path: str, host: str = "127.0.0.1"):
    """返回 ``(status, body)``；JSON 解析为 dict，其它按 bytes 返回。"""
    conn = HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", path)
        res = conn.getresponse()
        raw = res.read()
        ctype = res.getheader("Content-Type", "")
        if "application/json" in ctype:
            try:
                return res.status, json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return res.status, raw
        return res.status, raw
    finally:
        conn.close()


def http_post(port: int, path: str, payload: dict, host: str = "127.0.0.1"):
    conn = HTTPConnection(host, port, timeout=10)
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.request("POST", path, body=body,
                     headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        raw = res.read()
        try:
            return res.status, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return res.status, raw
    finally:
        conn.close()


@contextmanager
def temp_cordis_root(story: dict | None = None, story_name: str = "story_fixture.json"):
    """临时模组根：RED 模组的副本（+ 可选剧本文件），供 AppState/Cordis 使用。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "modules"
        dest = root / RED_MODULE_ID
        dest.mkdir(parents=True)
        (dest / "module.json").write_text(
            json.dumps(red_module_data(), ensure_ascii=False), encoding="utf-8")
        if story is not None:
            (dest / story_name).write_text(
                json.dumps(story, ensure_ascii=False), encoding="utf-8")
        yield root


class WebServerCase(unittest.TestCase):
    """真实 HTTP 服务器夹具（``AppState`` + ``make_server``，端口 0）。

    三个必须保留的细节（移植前 ``test_web`` / ``test_story`` 各写了一份）：
    清空 LLM 环境变量、``chdir`` 进临时目录让 ``saves/`` 落进 scratch、
    daemon 线程 ``serve_forever`` + 退出时 ``shutdown``/恢复 cwd 与环境。
    """

    cordis_root: Path | None = None        # 子类可覆盖（默认临时根）
    story: dict | None = None              # 子类可给出剧本文件
    unlocks_path: str = "saves/story_endings.json"

    def setUp(self):
        from ctrcg.web.server import AppState, make_server
        self._saved_env = strip_llm_env()
        self._saved_cwd = os.getcwd()
        self._td = tempfile.TemporaryDirectory()
        os.chdir(self._td.name)
        Path("saves").mkdir(exist_ok=True)
        if self.cordis_root is not None:
            self._root_cm = None
            root = Path(self.cordis_root)
        else:
            self._root_cm = temp_cordis_root(self.story)
            root = self._root_cm.__enter__()
        self.cordis_root_path = root
        self.app = AppState(cordis_root=str(root), unlocks_path=self.unlocks_path)
        self.server = make_server(self.app, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.reset()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if getattr(self, "_root_cm", None) is not None:
            self._root_cm.__exit__(None, None, None)
        os.chdir(self._saved_cwd)
        restore_env(self._saved_env)
        self._td.cleanup()

    # ---- 便捷入口
    def get(self, path: str):
        return http_get(self.port, path)

    def post(self, path: str, payload: dict):
        return http_post(self.port, path, payload)

    def start_game(self, **overrides) -> tuple[int, dict]:
        """用 RED 建卡口径开一局（默认取模组第一个预设角色）。"""
        assert self.app is not None
        mod = self.app.cordis.ensure(RED_MODULE_ID)[0]
        key = list(mod.pregens)[0]
        spec = {"module_id": RED_MODULE_ID, "pregen": key, "name": "测试者",
                "seed": 7}
        spec.update(overrides)
        return self.post("/api/start", spec)


# ================================================================ 剧本夹具
def story_data(story_id: str = "red-fixture", endings_total: int = 4) -> dict:
    """一份通过校验的 RED 剧本（互动剧）：4 结局、2 主角、旗标门控、检定分支。"""
    return {
        "id": story_id,
        "title": "雨夜交割 · 互动剧本",
        "en_title": "Rain Handoff",
        "synopsis": "你替中间人跑一趟腿，代价由你选。",
        "start": "n_start",
        "death_ending": "end_dead",
        "endings_total": endings_total,
        "heroes": [
            {"id": "k", "name": "Kyle", "title": "独行侠", "hp": 45,
             "role": "solo", "role_rank": 4,
             "stats": {"int": 5, "ref": 7, "dex": 6, "tech": 4, "cool": 5,
                       "will": 6, "luck": 5, "move": 5, "body": 7, "emp": 5},
             "skills": {"perception": 6, "handgun": 6, "persuasion": 4},
             "desc": "左脸一道旧疤。"},
            {"id": "b", "name": "Bella", "title": "技术师", "hp": 35,
             "role": "tech", "role_rank": 4,
             "stats": {"int": 7, "ref": 5, "dex": 5, "tech": 8, "cool": 5,
                       "will": 5, "luck": 5, "move": 4, "body": 5, "emp": 6},
             "skills": {"electronics_security": 6, "perception": 5},
             "desc": "指尖有机油味。"},
        ],
        "nodes": {
            "n_start": {
                "chapter": "第一幕 · 巷口",
                "text": "雨把霓虹泡成油膜。箱子在你脚边。",
                "choices": [
                    {"text": "先看箱子", "set": {"know_chest": "1"},
                     "goto": "n_chest"},
                    {"text": "直接走", "goto": "n_walk"},
                    {"text": "撬开印刷厂后门",
                     "check": {"skill": "electronics_security", "dv": 15},
                     "success": {"goto": "n_chest", "set": {"know_chest": "1"}},
                     "fail": {"goto": "n_walk", "damage": 5}},
                ],
            },
            "n_chest": {
                "text": "假钞。纸张是海伍德旧印刷厂的。",
                "next": "n_walk",
            },
            "n_walk": {
                "text": "有人跟上了你。",
                "choices": [
                    {"text": "拔枪", "check": {"skill": "handgun", "dv": 15},
                     "success": {"goto": "end_good"},
                     "fail": {"goto": "end_dead", "damage": 40}},
                    {"text": "装作没看见", "goto": "n_quiet"},
                ],
            },
            "n_quiet": {
                "text": "你继续走，脚步声跟了一路。",
                "branch": [{"require": {"flags": ["know_chest"]},
                            "goto": "end_wise"}],
                "next": "end_plain",
            },
            "end_good": {"ending": True, "title": "打穿了雨幕", "rank": "A",
                         "text": "你活着走到了交割点。"},
            "end_wise": {"ending": True, "title": "知道的代价", "rank": "B",
                         "text": "你知道得太多，也活了下来。"},
            "end_plain": {"ending": True, "title": "路人", "rank": "C",
                          "text": "你什么也没问，什么也没得到。"},
            "end_dead": {"ending": True, "title": "倒在霓虹里", "rank": "D",
                         "text": "雨把你的血冲进了下水道。"},
        },
    }


def story_service_data(**kw) -> dict:
    """一份故意损坏的剧本（start 悬空）——校验器必须拒绝。"""
    data = story_data(**kw)
    data["start"] = "n_missing"
    return data


__all__ = [
    "TESTS_DIR", "ROOT", "MODULES_DIR", "RED_MODULE_ID", "RED_MODULE_PATH",
    "LLM_ENV_VARS", "strip_llm_env", "restore_env",
    "ScriptRng", "red_module_data", "red_module", "red_pregen_keys", "red_pregen",
    "fixture_module_data", "fixture_module",
    "DEFAULT_STATS", "DEFAULT_SKILLS", "make_pc", "make_npc", "make_companions",
    "make_state", "make_agent_session", "RollGateUI", "CountUI", "ScriptedUI",
    "DM_MARK", "NPC_MARK", "message_system", "is_fresh_run",
    "http_get", "http_post", "temp_cordis_root", "WebServerCase",
    "story_data", "story_service_data",
]
