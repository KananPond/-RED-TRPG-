"""本地 HTTP 服务：浏览器前端 + 游戏引擎 + Cordis 模组注册表。

模组：GET/POST /api/modules*（列表/导入/AI生成/删除），start 时按 module_id 原子加载。
设置：GET/POST /api/settings/llm（供应商预设，OpenAI 兼容协议），密钥只存在
      本服务进程内存或环境变量中，不落盘、不回显。
AI PC：GET/POST /api/settings/aipc（AI 代打开关 + 玩家 persona，非敏感叙事文本），
      独立端点持久化到 saves/llm_config.json 的 "aipc" 键——不走 apply_llm_settings
      （其缺密钥时早退不落盘），无 LLM 时开关同样可保存（docs/aipc-design.md §4.1）。
用量：GET /api/usage、POST /api/usage/clear（TokenMeter 持久化到 saves/）。
"""

from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..campaign import module_data as M
from ..campaign.cordis import Cordis
from ..campaign.loader import Module
from ..config import LLM_PROVIDERS, LLMConfig
from ..game import state as G
from ..game.session import GameSession
from ..harness.usage import TokenMeter
from ..rules import data as D
from ..rules.character import Character
from ..rules.dice import roll_scores
from ..ui.charcreate import RECOMMENDED
from ..ui.webui import AbortGame, WebUI
from .charbuild import build_aipc_card, build_character

STATIC_DIR = Path(__file__).parent / "static"
SAVE_FILE = "saves/game.json"
LLM_SETTINGS_FILE = "saves/llm_config.json"   # 仅存非敏感字段（provider/base_url/model + aipc）

_GEN_SYSTEM = """你是资深的 D&D 5e 模组设计师，为"被遗忘的大陆"（被遗忘的国度，剑湾一带）创作跑团模组。
你要输出一个完整的模组 JSON，供跑团引擎直接加载运行。只输出 JSON 本身，不要任何解释或代码围栏。

模组 JSON 规范（必须严格遵守）：
{
  "id": "小写字母数字连字符（1-48字符）",
  "title": "模组中文名", "en_title": "English Title",
  "setting": "舞台设定（1-2句，被遗忘的大陆/剑湾）",
  "synopsis": "剧情梗概（3-4句）",
  "party_level_start": 1, "expected_end_level": 3,
  "act_titles": {"1": "第一幕标题", "2": "..."},
  "epilogue": "结局叙述（多行\\n分隔）",
  "npcs": {"npc_id": {"name": "名字", "race": "种族", "desc": "描述与动机"}},
  "companions": [                          // 可选；缺省时引擎使用默认三人组
    {"id": "ally", "name": "名字", "race": "BG3种族key（见下方种族表）",
     "klass": "BG3职业key（12职业之一）", "background": "BG3出身key（见下方出身表）",
     "fighting_style": "archery|defense|dueling|",
     "scores": {"str":15,"dex":13,"con":14,"int":10,"wis":12,"cha":8},
     "skills": ["职业技能key"], "persona": "人设", "voice": "语气"}],
  "encounters": { "e_xxx": {"title": "名", "dark": false, "intro": "开战叙述",
      "monsters": [["goblin", 3]], "loot": {"gold": 30, "items": {"healing_potion": 1}}} },
  "scenes": [ {"id": "s1", "act": 1, "location": "地点",
      "intro": "进场叙述（2-4句）", "explore": "可做的事",
      "skills": [{"skill": "perception", "dc": 12, "success": "成功结果", "fail": "失败结果"}],
      "encounter": "e_xxx 或 null", "next": "下一场景id 或 null(最终场景)",
      "milestone": 0, "level_to": null,
      "outro": null 或 "战斗后叙述"} , ...],
  "custom_monsters": { "my_monster": {"name": "名", "xp": 200, "hp": "4d8+4", "ac": 14,
      "speed": 30, "abilities": {"str":14,"dex":14,"con":12,"int":8,"wis":10,"cha":8},
      "traits": ["特性中文名（可含：施法者/突袭/群袭战术）"],
      "attacks": [{"weapon": "scimitar|shortbow|longbow|greataxe|bite|slam|morningstar|dagger|mace|quarterstaff",
                   "bonus": 4, "extra": "+2", "dmg": "1d6"}],
      "darkvision": 60} }
}

硬性约束：
1. 场景链必须从第一个场景经 next 连贯走到唯一的 null 终点，无环、无孤场景。
2. 场景的 encounter 必须指向 encounters 中存在的 id；怪物 id 必须是内置表
   （SRD 生物：goblin, skeleton, zombie, wolf, giant_badger, riding_horse,
   bandit, hobgoblin, worg, gnoll, black_bear, duergar, giant_spider, ghoul,
   imp, hook_horror, ettercap, bandit_captain, ogre, ash_mage, phase_spider,
   redcap, owlbear, minotaur, wight, bulette, cambion）或 custom_monsters 中
   定义的 id（自定义怪物字段规范见根目录 MODULE_SPEC.md：支持 dtype、
   spell_level、multiattack、onhit 等完整机制字段）。
3. 技能 key 只能取：athletics/acrobatics/sleight_of_hand/stealth/arcana/history/
   investigation/nature/religion/animal_handling/insight/medicine/perception/
   survival/deception/intimidation/performance/persuasion；DC 5-30。
4. 经验预算：全部怪物 XP + milestone 总和按 4 人平分后应达到 900（升3级），约 1200-2500 为佳。
5. 战斗难度递进；至少一场 boss 战；intro/outro 用具体、有画面感的中文。
6. id 用英文，正文全用中文。规模：2-3 幕、6-10 个场景、4-7 场遭遇。

种族表（BG3，同伴 race 字段只能取这些 key）：human, high_elf, wood_elf,
drow_lolth, drow_seldarine, half_elf_high, half_elf_wood, half_elf_drow,
dwarf_shield, dwarf_gold, duergar, lightfoot_halfling, strongheart_halfling,
forest_gnome, rock_gnome, deep_gnome, tiefling_asmodeus, tiefling_mephisto,
tiefling_zariel, half_orc, githyanki, dragonborn_red, dragonborn_gold,
dragonborn_black, dragonborn_blue, dragonborn_green, dragonborn_white,
dragonborn_silver, dragonborn_brass, dragonborn_bronze, dragonborn_copper。
出身表（BG3，background 字段只能取这些 key）：acolyte, charlatan, criminal,
entertainer, folk_hero, guild_artisan, noble, outlander, sage, soldier, urchin。"""


class AppState:
    def __init__(self, cordis_root: str | Path = "modules"):
        self.ui: WebUI | None = None
        self.session: GameSession | None = None
        self.thread: threading.Thread | None = None
        self.cordis = Cordis(cordis_root)
        self.meter = TokenMeter("saves/token_usage.json")
        self._runtime_key: str | None = None       # 仅内存，不落盘
        self._persisted: dict = self._load_llm_settings()
        self.aipc_prefs: dict = (                  # AI PC 偏好（开关/persona，非敏感）
            self._persisted["aipc"] if isinstance(self._persisted.get("aipc"), dict)
            else {})
        self.lock = threading.Lock()
        self.generation = 0                        # 对局代际：每次 start +1
        self.llm_cfg = self._resolve_llm_config()

    # ------------------------------------------------------------ LLM 配置
    def _load_llm_settings(self) -> dict:
        try:
            data = json.loads(Path(LLM_SETTINGS_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_llm_settings(self, cfg: LLMConfig | None = None) -> None:
        """持久化非敏感配置：provider/base_url/model + aipc 偏好（无任何密钥字段）。"""
        payload = {k: self._persisted[k] for k in ("provider", "base_url", "model")
                   if k in self._persisted}
        if cfg is not None:
            payload.update({"provider": cfg.provider, "base_url": cfg.base_url,
                            "model": cfg.model})
        payload["aipc"] = dict(self.aipc_prefs)
        try:
            Path(LLM_SETTINGS_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(LLM_SETTINGS_FILE).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _resolve_llm_config(self) -> LLMConfig | None:
        """优先级：环境变量 > 已保存的非敏感配置（密钥取对应环境变量）。"""
        provider = self._persisted.get("provider", "deepseek")
        cfg = LLMConfig.from_env(provider)
        if cfg is None and provider != "deepseek":
            cfg = LLMConfig.from_env("deepseek")
        if cfg is None:
            cfg = LLMConfig.from_env("deepseek")
        if cfg is not None:
            # 应用已保存的 base_url/model 覆盖（非敏感字段）
            if self._persisted.get("base_url"):
                cfg.base_url = str(self._persisted["base_url"]).rstrip("/")
            if self._persisted.get("model"):
                cfg.model = str(self._persisted["model"])
            if self._runtime_key:
                cfg.api_key = self._runtime_key
        return cfg

    def apply_llm_settings(self, spec: dict) -> tuple[bool, str | None, LLMConfig | None]:
        provider = str(spec.get("provider", "deepseek"))
        if provider not in LLM_PROVIDERS:
            return False, "未知供应商", None
        preset = LLM_PROVIDERS[provider]
        base_url = str(spec.get("base_url") or preset["base_url"]).strip().rstrip("/")
        model = str(spec.get("model") or (preset["models"][0] if preset["models"] else "")).strip()
        key = str(spec.get("api_key") or "").strip()
        if not key:
            key = self._runtime_key or ""
        if not key:
            for env in preset["key_envs"]:
                import os
                key = os.environ.get(env, "").strip()
                if key:
                    break
        if provider == "custom" and not str(spec.get("base_url") or "").strip():
            return False, "自定义供应商必须填写 API 地址", None
        if not key:
            return False, "缺少 API 密钥：请在上方填写，或设置对应环境变量", None
        from ..harness.adapter import OpenAICompatibleAdapter
        try:
            OpenAICompatibleAdapter(base_url=base_url, api_key=key, max_retries=1)
        except ValueError as e:
            return False, f"端点校验失败：{e}", None
        cfg = LLMConfig(base_url=base_url, api_key=key, model=model, provider=provider)
        if spec.get("api_key"):
            self._runtime_key = key          # 仅内存
        self._persisted = {"provider": provider, "base_url": base_url, "model": model}
        self._save_llm_settings(cfg)
        self.llm_cfg = cfg
        return True, None, cfg

    def apply_aipc_settings(self, spec: dict) -> tuple[bool, str | None]:
        """AI PC 偏好持久化（aipc-design §4.1，rev3 ④）。

        独立于 apply_llm_settings——后者缺密钥时提前 return 且不落盘，而开关必须
        在无 LLM 时也可保存（对局启动时再按 llm_cfg 降级为人类操控）。
        persona 为非敏感叙事文本（≤1000 字符，超长截断），无密钥面。"""
        if not isinstance(spec, dict):
            spec = {}
        self.aipc_prefs = {"enabled": bool(spec.get("enabled")),
                           "persona": str(spec.get("persona") or "").strip()[:1000]}
        self._save_llm_settings()
        return True, None

    def aipc_settings_view(self) -> dict:
        return {"enabled": bool(self.aipc_prefs.get("enabled")),
                "persona": str(self.aipc_prefs.get("persona") or ""),
                "llm_active": self.llm_cfg is not None}

    def llm_settings_view(self) -> dict:
        cfg = self.llm_cfg
        return {
            "llm_active": cfg is not None,
            "provider": cfg.provider if cfg else self._persisted.get("provider", "deepseek"),
            "base_url": cfg.base_url if cfg else "",
            "model": cfg.model if cfg else "",
            "key_source": ("runtime" if self._runtime_key else
                           "env" if cfg else "none"),
            # AI PC 开关随视图下发（持久化走 POST /api/settings/aipc 独立端点）
            "aipc": {"enabled": bool(self.aipc_prefs.get("enabled")),
                     "persona": str(self.aipc_prefs.get("persona") or "")},
        }

    # ------------------------------------------------------------ 对局
    def start(self, spec: dict) -> tuple[bool, str | None, dict]:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False, "已有一局在进行，请先重置", {}
        module = None
        try:
            if spec.get("continue"):
                save_path = str(spec.get("save") or SAVE_FILE)
                try:
                    raw = json.loads(Path(save_path).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    return False, f"存档读取失败：{e}", {}
                module_id = str(raw.get("module_id") or "starfall")
                module, errs, _w = self.cordis.ensure(module_id)
                if module is None:
                    return False, f"存档所需模组缺失：{module_id}", {}
                state = G.load_game(save_path, module=module)
            else:
                module_id = str(spec.get("module_id") or "blue-watch")
                module, errs, warns = self.cordis.ensure(module_id)
                if module is None:
                    return False, "；".join(errs) or f"模组加载失败：{module_id}", {}
                import random
                rng = (random.Random(spec["seed"]) if spec.get("seed") is not None
                       else random.Random())
                # 同一 rng 供属性掷骰与对局使用：同 seed 完全可复现
                player = build_character(spec, rng=rng)
                if module.party_level_start > player.level:
                    player.level_up_to(module.party_level_start)
                companions = build_companions(module, state_level=player.level)
                state = G.GameState(player, companions, rng, module=module)
                # AI PC（aipc-design §4.1）：开关以开局 spec 优先，缺省回退设置页
                # 持久化偏好；续档走 load_game，以存档 aipc.enabled 为准，不在此覆盖。
                state.aipc_enabled = bool(spec.get(
                    "aipc_enabled", self.aipc_prefs.get("enabled")))
                state.aipc_card = build_aipc_card(spec, name=player.name)
                persona_text = str(self.aipc_prefs.get("persona") or "").strip()
                if persona_text:
                    # Persona 的 dict 序列形态（字段同 st.card.Persona.to_dict）；
                    # st 就绪后由 build_session 接缝经 Persona.from_dict 重建
                    state.persona = {"name": player.name, "description": persona_text,
                                     "position": "in_prompt", "depth": 2,
                                     "role": "system"}
        except (ValueError, KeyError, FileNotFoundError, TypeError) as e:
            return False, str(e), {}

        ui = WebUI()
        session = build_session(state, ui, self.llm_cfg, module=module,
                                meter=self.meter)
        with self.lock:
            self.generation += 1
        self.ui, self.session = ui, session

        def run():
            try:
                session.run()
                ui._emit("game_over", reason="ended",
                         epilogue=session.state.epilogue_snapshot(),
                         dice_stats=ui.dice_stats())
            except AbortGame:
                pass
            except Exception as e:  # noqa: BLE001 —— 错误必须反馈到前端
                ui._emit("game_over", reason="error", error=f"{type(e).__name__}: {e}")

        self.thread = threading.Thread(target=run, daemon=True, name="game-loop")
        self.thread.start()
        return True, None, self.snapshot()

    def reset(self) -> None:
        with self.lock:
            ui, session = self.ui, self.session
            self.session = None
            self.ui = None            # 置空：新连接不再回放已结束对局的历史
        if session is not None and ui is not None:
            ui.abort()

    def snapshot(self) -> dict:
        cfg = self.llm_cfg
        save_exists = (Path(SAVE_FILE).exists() if Path(SAVE_FILE).parent.exists()
                       else False)
        base = {"started": False, "llm_active": cfg is not None,
                "model": cfg.model if cfg else "",
                "provider": cfg.provider if cfg else "",
                "save_exists": save_exists, "running": False,
                "generation": self.generation}
        if self.session is None:
            return base
        st = self.session.state
        scene = self.session.module.scene_by_id(st.scene_id) or {}
        enc = self.session.encounter
        base.update({
            "started": True,
            "running": bool(self.thread and self.thread.is_alive()),
            "module_id": self.session.module.id,
            "module_title": self.session.module.title,
            "act": scene.get("act", 1),
            "act_title": self.session.module.act_titles.get(scene.get("act", 1), ""),
            "location": scene.get("location", ""),
            "gold": st.player.gold,
            "flags": st.flags,
            "encounter_title": enc.title if enc else None,
            "party": [{
                "name": c.name,
                "klass": M.D_NAME.get(c.klass, c.klass),
                "klass_key": c.klass,
                "race": c.race,
                "level": c.level, "hp": c.hp_cur, "hp_max": c.hp_max,
                "ac": c.ac(),
                "conditions": sorted(_cn(c.conditions)),
                "slots": {str(k): v for k, v in c.spell_slots().items()},
                "is_player": c is st.player,
            } for c in st.party()],
        })
        # AI PC 模式（§4.1）：enabled=控制器已挂载（真正接管）；requested=开关已开
        # 但本局降级为人类操控（LLM 未配置/组件未就绪/名字冲突），前端据此说明
        base["aipc"] = {
            "enabled": getattr(self.session, "aipc", None) is not None,
            "requested": bool(getattr(st, "aipc_enabled", False)),
            "player": st.player.name,
        }
        return base


def _cn(conds) -> list[str]:
    return [M.COND_CN.get(c, c) for c in conds]


def build_companions(module: Module, state_level: int = 1) -> list[Character]:
    """同伴由模组决定；模组未定义时使用默认三人组（见 MODULE_SPEC.md）。"""
    comps = module.companions or M.default_companions()
    out = []
    for comp in comps:
        ch = Character.create(
            name=comp["name"], race=comp["race"], klass=comp["klass"],
            background=comp["background"], scores=comp["scores"],
            skill_choices=comp["skills"], fighting_style=comp.get("fighting_style", ""),
            level=state_level)
        ch.apply_default_choices()
        out.append(ch)
    return out


def build_session(state: G.GameState, ui: WebUI, llm_cfg: LLMConfig | None,
                  module: Module | None = None,
                  meter: TokenMeter | None = None) -> GameSession:
    """按 LLM 配置构建 AI 模式会话，否则剧本模式。

    AI PC（aipc-design §0/§4.1）：仅当 llm_cfg 非 None 且 state.aipc_enabled 时
    才挂 AIPCController；开关关闭时全部新分支短路，行为与基线完全一致。
    本阶段接缝只做参数与降级通路——ctrcg.st / ctrcg.game.aipc 由后续集成落地，
    未就绪（ImportError）、attach 返回 None（玩家名与同伴冲突，§2.1）或
    卡/persona 数据无效时，回退人类操控并在 UI 说明。"""
    module = module or state.module
    if llm_cfg is None:
        if getattr(state, "aipc_enabled", False):
            _aipc_note(ui, "（AI 代打未启用：尚未接入 LLM，本局由你亲自操控。"
                           "开关已保存，接入 LLM 后新开对局生效。）")
        return GameSession(state, ui, loop=None, module=module, meter=meter)
    from ..game.agents import make_dm_agent, make_pc_agent
    from ..harness.adapter import OpenAICompatibleAdapter
    from ..harness.loop import DefaultAgentLoop, TruncateCompactor

    adapter = OpenAICompatibleAdapter(
        base_url=llm_cfg.base_url, api_key=llm_cfg.api_key,
        timeout=llm_cfg.request_timeout, max_retries=llm_cfg.max_retries)
    loop = DefaultAgentLoop(registry=None, adapter=adapter,
                            compactor=TruncateCompactor(token_limit=12000))
    dm_agent = make_dm_agent(llm_cfg.model, module)
    pc_agents = {}
    for comp, ch in zip(module.companions, state.companions):
        slots_hint = "法术位：" + ("、".join(f"{lv}环×{n}" for lv, n in ch.spell_slots().items())
                                   or "无")
        pc_agents[ch.name] = make_pc_agent(comp, ch.level, ch.weapons,
                                           slots_hint, llm_cfg.model,
                                           user=state.player.name)
    session = GameSession(state, ui, loop=loop, dm_agent=dm_agent, pc_agents=pc_agents,
                          module=module, meter=meter)
    _maybe_attach_aipc(session, state, ui, llm_cfg, loop)
    return session


def _aipc_note(ui, text: str) -> None:
    """AI PC 启用/降级说明行（UI 事件流；发送失败不阻塞开局）。"""
    try:
        ui.system_line(text)
    except Exception:  # noqa: BLE001
        pass


def _maybe_attach_aipc(session: GameSession, state: G.GameState, ui,
                       llm_cfg: LLMConfig, loop) -> None:
    """AI PC agent 构建接缝（aipc-design §2.1/§4.1，签名约定处）。

    开关开启且 LLM 就绪时按设计文档签名挂载：
        AIPCController.attach(session, loop, llm_cfg.model, card, persona) -> AIPCController | None
    attach 在内部注册第 4 名同伴（session.pc_agents[玩家名]）、复用
    session.pc_session(玩家名) 并赋值 session.aipc；返回 None=玩家名与同伴冲突。
    persona/card 接受对象或 dict 序列形态（本阶段持久化通路先行的暂存形态），
    在此统一重建；任一步失败都降级为人类操控并说明，绝不阻塞开局。"""
    if not getattr(state, "aipc_enabled", False):
        return
    try:
        from ..game.aipc import AIPCController
        from ..st.card import CharacterCard, Persona, card_from_player
    except ImportError:
        _aipc_note(ui, "（AI 代打未启用：AI PC 组件尚未就绪，本局由你亲自操控。）")
        return
    try:
        card = state.aipc_card
        if isinstance(card, dict):
            card = CharacterCard.from_dict(card)
        if card is None:
            card = card_from_player(state.player)
        persona = state.persona
        if isinstance(persona, dict):
            persona = Persona.from_dict(persona)
        if persona is None:
            persona = Persona(name=state.player.name)
    except Exception as e:  # noqa: BLE001 —— 卡/persona 数据无效时降级
        _aipc_note(ui, f"（AI 代打未启用：角色卡或 persona 无效"
                       f"（{type(e).__name__}），本局由你亲自操控。）")
        return
    aipc = AIPCController.attach(session, loop, llm_cfg.model, card, persona)
    if aipc is None:
        _aipc_note(ui, "（AI 代打未启用：玩家角色名与同伴重名，本局由你亲自操控。）")
        return
    _aipc_note(ui, f"（AI 代打已启用：{state.player.name} 将由 AI 扮演。）")


def meta_payload(app: AppState) -> dict:
    modules = []
    for entry in app.cordis.scan():
        mod, errs, warns = app.cordis.ensure(entry["id"])
        if mod is None:
            modules.append({"id": entry["id"], "title": entry["id"],
                            "source": entry["source"], "broken": True,
                            "errors": errs})
            continue
        s = mod.summary()
        s["broken"] = False
        modules.append(s)
    return {
        "title": M.MODULE["title"], "en_title": M.MODULE["en_title"],
        "setting": M.MODULE["setting"], "synopsis": M.MODULE["synopsis"],
        "llm": app.llm_settings_view(),
        "providers": [{"key": k, "name": v["name"], "base_url": v["base_url"],
                       "models": v["models"],
                       "key_envs": v["key_envs"],
                       "default": bool(v.get("default"))}
                      for k, v in LLM_PROVIDERS.items()],
        "modules": modules,
        "races": [{"key": k, "name": v["name"], "family": v.get("family", ""),
                   "traits": v["traits"], "skills": v.get("skills", 0),
                   "bonuses": v.get("default_bonuses", {}),
                   "darkvision": v.get("darkvision", 0)} for k, v in D.RACES.items()],
        "classes": [{"key": k, "name": v["name"], "hit_die": v["hit_die"],
                     "skill_count": v["skill_count"],
                     "skill_choices": v["skill_choices"],
                     "has_style": k == "fighter",
                     "armor": v["armor"], "shield": v["shield"]}
                    for k, v in D.CLASSES.items()],
        "backgrounds": [{"key": k, "name": D.BACKGROUNDS[k]["name"],
                         "desc": D.BACKGROUNDS[k]["desc"],
                         "skills": D.BACKGROUNDS[k]["skills"]}
                        for k in D.BACKGROUNDS],
        "skills": [{"key": k, "name": v["name"], "ability": D.ABILITIES[v["ability"]]}
                   for k, v in D.SKILLS.items()],
        "styles": [{"key": k, "name": n} for k, n in STYLE_MAP.items()],
        "dice_rule": "4d6去最低 × 6",
        "recommended": RECOMMENDED,
        "abilities": [{"key": k, "name": n} for k, n in D.ABILITIES.items()],
    }


STYLE_MAP = {"archery": "箭术（远程攻击+2）", "defense": "防御（AC+1）",
             "dueling": "对决（单手近战伤害+2）"}


# ---------------------------------------------------------------- AI 生成
def generate_module(app: AppState, spec: dict) -> tuple[Module | None, list[str], list[str]]:
    """用当前配置的 LLM 按规范生成一个原子模组并安装。"""
    cfg = app.llm_cfg
    if cfg is None:
        return None, ["尚未配置 LLM：请先在「设置 → API 配置」中接入供应商"], []
    from ..harness.adapter import OpenAICompatibleAdapter
    theme = str(spec.get("theme") or "").strip()
    hints = str(spec.get("hints") or "").strip()
    if not theme:
        return None, ["请填写模组主题（如：月夜狼人村庄的三幕恐怖故事）"], []
    user_prompt = (f"请创作一个全新的原创模组。主题：{theme}\n"
                   + (f"附加要求：{hints}\n" if hints else "")
                   + "记住：只输出模组 JSON 本身，符合上述规范的全部硬性约束。")
    adapter = OpenAICompatibleAdapter(base_url=cfg.base_url, api_key=cfg.api_key,
                                      timeout=180.0, max_retries=2)
    resp = adapter.chat(model=cfg.model,
                        messages=[{"role": "system", "content": _GEN_SYSTEM},
                                  {"role": "user", "content": user_prompt}],
                        tools=None, temperature=0.9, max_tokens=8000)
    if app.meter is not None and resp.usage:
        app.meter.record("generator", cfg.model, resp.usage)
    text = resp.content or ""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, ["模型未返回 JSON，请重试或调整主题"], [text[:200]]
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        return None, [f"生成的 JSON 无法解析：{e}"], [text[:200]]
    return app.cordis.install(data, source="generated")


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    app: AppState  # 由 make_server 注入
    server_version = "CTRCG/1.4"

    # ------------------------------------------------------------ 工具
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    # ------------------------------------------------------------ 静态页
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            return self._file("index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            types = {".css": "text/css; charset=utf-8",
                     ".js": "text/javascript; charset=utf-8",
                     ".svg": "image/svg+xml", ".png": "image/png"}
            ext = ("." + name.rsplit(".", 1)[-1]) if "." in name else ""
            if "/" in name or ".." in name or ext not in types:
                return self._json({"error": "not found"}, 404)
            return self._file(name, types[ext])
        if path == "/api/stream":
            return self._stream()
        if path == "/api/meta":
            return self._json(meta_payload(self.app))
        if path == "/api/roll_scores":
            # 服务端代掷属性骰（4d6 去最低 × 6），保证结果可信
            return self._json({"scores": roll_scores(random.Random())})
        if path == "/api/state":
            return self._json(self.app.snapshot())
        if path == "/api/pending":
            ui = self.app.ui
            return self._json({"pending": ui.pending_view() if ui else None})
        if path == "/api/usage":
            return self._json(self.app.meter.summary())
        if path == "/api/saves":
            saves = []
            sdir = Path("saves")
            if sdir.exists():
                for f in sorted(sdir.glob("*.json")):
                    if f.name in ("token_usage.json", "llm_config.json"):
                        continue
                    try:
                        raw = json.loads(f.read_text(encoding="utf-8"))
                        saves.append({"file": str(f),
                                      "module_id": raw.get("module_id", "starfall"),
                                      "scene_id": raw.get("scene_id", ""),
                                      "ts": round(f.stat().st_mtime)})
                    except (OSError, json.JSONDecodeError):
                        continue
            saves.sort(key=lambda s: -s["ts"])
            return self._json({"saves": saves})
        if path == "/api/settings/llm":
            view = self.app.llm_settings_view()
            view["providers"] = [
                {"key": k, "name": v["name"], "base_url": v["base_url"],
                 "models": v["models"], "key_envs": v["key_envs"],
                 "default": bool(v.get("default"))}
                for k, v in LLM_PROVIDERS.items()]
            return self._json(view)
        if path == "/api/settings/aipc":
            return self._json(self.app.aipc_settings_view())
        return self._json({"error": "not found"}, 404)

    def _file(self, name: str, ctype: str) -> None:
        path = STATIC_DIR / name
        if not path.exists():
            return self._json({"error": "not found"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------ SSE
    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        ui = self.app.ui
        if ui is None:
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
            return
        q = ui.subscribe()
        gen = self.app.generation
        try:
            history = list(ui.history)
            for ev in history:
                self._sse(ev)
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
            last_id = history[-1]["id"] if history else 0
            idle_checks = 0
            while True:
                if self.app.generation != gen or self.app.ui is not ui:
                    # 对局已更替/重置：断开旧流，浏览器 EventSource 会自动重连
                    break
                try:
                    ev = q.get(timeout=3)
                except queue.Empty:
                    idle_checks += 1
                    if idle_checks % 5 == 0:          # 约每 15 秒一次保活 ping
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    continue
                if ev.get("id", 0) <= last_id:
                    continue
                self._sse(ev)
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass
        finally:
            ui.unsubscribe(q)

    def _sse(self, ev: dict) -> None:
        data = json.dumps(ev, ensure_ascii=False)
        self.wfile.write(f"id: {ev.get('id', 0)}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    # ------------------------------------------------------------ 动作
    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if path == "/api/start":
            ok, err, snap = self.app.start(body)
            return self._json({"ok": ok, "error": err, "state": snap},
                              200 if ok else 400)
        if path == "/api/answer":
            ui = self.app.ui
            if ui is None:
                return self._json({"ok": False, "error": "对局未开始"}, 409)
            text = body.get("text")
            # AI PC 对局旁听指令（aipc-design §2.8/§4.1）：无 pending 且控制器已
            # 挂载时，自由文本转投 ui.post_command（webui 落地前该分支不可达）
            if (body.get("index") is None and isinstance(text, str) and text.strip()
                    and ui.pending_view() is None
                    and getattr(self.app.session, "aipc", None) is not None
                    and hasattr(ui, "post_command")):
                ui.post_command(text.strip())
                return self._json({"ok": True})
            ok, err = ui.resolve(index=body.get("index"), text=text)
            return self._json({"ok": ok, "error": err}, 200 if ok else 409)
        if path == "/api/reset":
            self.app.reset()
            return self._json({"ok": True})
        if path == "/api/settings/llm":
            ok, err, cfg = self.app.apply_llm_settings(body)
            return self._json({"ok": ok, "error": err,
                               "settings": self.app.llm_settings_view()},
                              200 if ok else 400)
        if path == "/api/settings/aipc":
            ok, err = self.app.apply_aipc_settings(body)
            return self._json({"ok": ok, "error": err,
                               "aipc": self.app.aipc_settings_view()},
                              200 if ok else 400)
        if path == "/api/llm/test":
            cfg = self.app.llm_cfg
            if cfg is None:
                return self._json({"ok": False, "error": "尚未配置 LLM"}, 400)
            from ..harness.adapter import OpenAICompatibleAdapter
            adapter = OpenAICompatibleAdapter(cfg.base_url, cfg.api_key,
                                              timeout=30.0, max_retries=1)
            try:
                resp = adapter.chat(cfg.model,
                                    [{"role": "user", "content": "回复：OK"}],
                                    None, 0.0, 16)
                if self.app.meter is not None and resp.usage:
                    self.app.meter.record("test", cfg.model, resp.usage)
                return self._json({"ok": True, "reply": (resp.content or "").strip()[:60]})
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": str(e)}, 400)
        if path == "/api/usage/clear":
            self.app.meter.clear()
            return self._json({"ok": True})
        if path == "/api/modules/import":
            mod, errors, warnings = self.app.cordis.install(body.get("module") or {},
                                                            source="imported")
            return self._json({"ok": mod is not None, "errors": errors,
                               "warnings": warnings,
                               "module": mod.summary() if mod else None},
                              200 if mod is not None else 400)
        if path == "/api/modules/generate":
            started = time.time()
            mod, errors, warnings = generate_module(self.app, body)
            return self._json({"ok": mod is not None, "errors": errors,
                               "warnings": warnings, "seconds": round(time.time() - started, 1),
                               "module": mod.summary() if mod else None},
                              200 if mod is not None else 400)
        if path == "/api/modules/delete":
            ok, err = self.app.cordis.delete(str(body.get("id") or ""))
            return self._json({"ok": ok, "error": err}, 200 if ok else 400)
        return self._json({"error": "not found"}, 404)


def make_server(app: AppState, port: int = 8500) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
