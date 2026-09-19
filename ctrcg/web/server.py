"""本地 HTTP 服务：浏览器前端 + 游戏引擎 + Cordis 模组注册表。

模组：GET/POST /api/modules*（列表/导入/AI生成/删除），start 时按 module_id 原子加载。
设置：GET/POST /api/settings/llm（供应商预设，OpenAI 兼容协议），密钥只存在
      本服务进程内存或环境变量中，不落盘、不回显。
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
from .charbuild import build_character

STATIC_DIR = Path(__file__).parent / "static"
SAVE_FILE = "saves/game.json"
LLM_SETTINGS_FILE = "saves/llm_config.json"   # 仅存非敏感字段（provider/base_url/model）

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

    def _save_llm_settings(self, cfg: LLMConfig) -> None:
        payload = {"provider": cfg.provider, "base_url": cfg.base_url,
                   "model": cfg.model}
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

    def llm_settings_view(self) -> dict:
        cfg = self.llm_cfg
        return {
            "llm_active": cfg is not None,
            "provider": cfg.provider if cfg else self._persisted.get("provider", "deepseek"),
            "base_url": cfg.base_url if cfg else "",
            "model": cfg.model if cfg else "",
            "key_source": ("runtime" if self._runtime_key else
                           "env" if cfg else "none"),
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
                module_id = str(spec.get("module_id") or "starfall")
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
    """按 LLM 配置构建 AI 模式会话，否则剧本模式。"""
    module = module or state.module
    if llm_cfg is None:
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
                                           slots_hint, llm_cfg.model)
    return GameSession(state, ui, loop=loop, dm_agent=dm_agent, pc_agents=pc_agents,
                       module=module, meter=meter)


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
    server_version = "CTRCG/1.1"

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
        if self.app.ui is None:
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
            return
        q = self.app.ui.subscribe()
        gen = self.app.generation
        try:
            history = list(self.app.ui.history)
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
            self.app.ui.unsubscribe(q)

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
            ok, err = ui.resolve(index=body.get("index"), text=body.get("text"))
            return self._json({"ok": ok, "error": err}, 200 if ok else 409)
        if path == "/api/reset":
            self.app.reset()
            return self._json({"ok": True})
        if path == "/api/settings/llm":
            ok, err, cfg = self.app.apply_llm_settings(body)
            return self._json({"ok": ok, "error": err,
                               "settings": self.app.llm_settings_view()},
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
