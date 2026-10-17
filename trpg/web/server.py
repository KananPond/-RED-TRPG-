"""本地 HTTP 服务：浏览器前端 + 游戏引擎 + Cordis 模组注册表。

模组：GET/POST /api/modules*（列表/导入/AI生成/删除），start 时按 module_id 原子加载。
设置：GET/POST /api/settings/llm（供应商预设，OpenAI 兼容协议），密钥只存在
      本服务进程内存或环境变量中，不落盘、不回显。
AI PC：GET/POST /api/settings/aipc（AI 代打开关 + 玩家 persona，非敏感叙事文本），
      独立端点持久化到 saves/llm_config.json 的 "aipc" 键——不走 apply_llm_settings
      （其缺密钥时早退不落盘），无 LLM 时开关同样可保存（docs/aipc-design.md §4.1）。
用量：GET /api/usage（明细 + 按日聚合 + GitHub 式热力图）、POST /api/usage/clear。
余额：GET /api/balance（供应商账户余额，只读、带 60 秒缓存，?refresh=1 强制刷新）。
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..campaign import module_data as M
from ..campaign.cordis import Cordis
from ..campaign.loader import Module
from ..config import LLM_PROVIDERS, LLMConfig
from ..game import state as G
from .. import __version__
from ..game.session import GameSession
from ..game.story import (StorySession, ending_ids, iter_story_files,
                          load_story_file, load_unlocks, record_unlock)
from ..harness.balance import fetch_balance
from ..harness.adapter import normalize_base_url
from ..harness.usage import TokenMeter
from ..rules import data as D
from ..rules.combat import METERS_PER_CELL
from ..ui.charcreate import (build_from_pregen, default_companion_keys,
                             pregens_of)
from ..ui.webui import AbortGame, WebUI
from .charbuild import build_aipc_card, build_character, companion_ids

STATIC_DIR = Path(__file__).parent / "static"
SAVE_FILE = "saves/game.json"
DEFAULT_MODULE_ID = "cpred-easy-mode"
LLM_SETTINGS_FILE = "saves/llm_config.json"   # 仅存非敏感字段（provider/base_url/model + aipc）
UNLOCKS_FILE = "saves/story_endings.json"     # 剧本模式结局图鉴（非敏感）

_GEN_SYSTEM = """你是资深的《赛博朋克 RED》（Cyberpunk RED）模组设计师，为"夜之城 · 2045 血染之时"创作跑团模组。
你要输出一个完整的模组 JSON，供跑团引擎直接加载运行。只输出 JSON 本身，不要任何解释或代码围栏。

模组 JSON 规范（必须严格遵守）：
{
  "id": "小写字母数字连字符（1-48字符）",
  "title": "模组中文名", "en_title": "English Title",
  "setting": "舞台设定（1-2句：夜之城 2045 年，哪个区、什么气候与权力格局）",
  "synopsis": "剧情梗概（3-4句）",
  "act_titles": {"1": "第一幕标题", "2": "..."},
  "epilogue": "结局叙述（多行\\n分隔）",
  "pregens": {                              // 必填，5 个预设角色：玩家挑其中一个扮演
    "solo_x": {"name": "名字", "handle": "街头绰号", "role": "职业 key（见下）",
      "role_rank": 4, "role_ability": "职业特殊能力名",
      "stats": {"int":6,"ref":7,"dex":6,"tech":4,"cool":6,"will":6,"luck":6,"move":5,"body":6,"emp":5},
      "hp": 40,                             // = 10 + 5 × ⌈(体格 + 意志力) ÷ 2⌉
      "skill_levels": {"handgun":6,"athletics":3},
      "armor": "护甲 key（见下）", "weapons": ["武器 key（见下）"],
      "cyberware": ["义体 key（见下）"], "gear": {"ammo": 20},
      "eurobucks": 0, "persona": "人设", "voice": "语气", "desc": "外貌与来历"}},
  "default_companions": ["medtech_y", "tech_z"],   // 必填：预设法里的 key
  "encounters": { "e_xxx": {"title": "名", "intro": "开战叙述",
      "enemies": [["corrupt_cop", 3]], "loot": {"eurobucks": 100, "items": {"ammo": 20}},
      "spawns": {"corrupt_cop": [[9,2],[9,5],[8,3]]}}},
  "scenes": [ {"id": "s1", "act": 1, "location": "地点（如：海伍德工业区 · 废弃仓库）",
      "intro": "进场叙述（2-4句）", "explore": "可做的事",
      "checks": [{"skill": "perception", "dv": 15, "success": "成功结果", "fail": "失败结果"}],
      "encounter": "e_xxx 或 null", "next": "下一场景id 或 null(最终场景)",
      "ip": 40, "shop": false, "outro": "", "map": {"grid": [11, 8], "label": "地图名",
        "obstacles": [[5,3]], "party_spawn": [1,4]}}, ...],
  "custom_npcs": { "my_npc": {"name": "名", "stats": {"ref":6,"dex":6,"body":6},
      "skills": {"handgun": 6}, "hp": 35, "armor": 7, "armor_name": "凯夫拉",
      "weapons": ["heavy_pistol"], "notes": "战术与动机"}},
  "npcs": {"npc_id": {"name": "名", "role": "中间人/警察/记者…", "desc": "描述与动机",
      "persona": "人设", "voice": "语气"}}
}

硬性约束：
1. 场景链必须从第一个场景经 next 连贯走到唯一的 null 终点，无环、无孤场景。
2. 场景的 encounter 必须指向 encounters 中存在的 id；敌人 id 必须是内置表
   （corrupt_cop, houston, bike_ganger, lazlo）或 custom_npcs 中定义的 id。
3. 检定写成 checks，skill 只能取下方技能白名单的 key，dv 只能取 13/15/17/21/24
   （日常/困难/专业/英雄/不可思议），也可用 9-30 的任意值。
4. pregens 必须 5 个，职业互不重复；default_companions 取其中 2-3 个。
   属性 2-8，十项齐全；PW = 10 + 5 × ⌈(体格 + 意志力) ÷ 2⌉；技能等级 0-10。
5. 改进点 ip 是离开场景时给队伍的奖励，单场上限 400 以内；不要使用经验值或等级。
6. 战斗难度递进；至少一场 boss 战；intro/outro 用具体、有画面感的中文。
7. id 用英文，正文用中文；**人名一律写英文**（预设角色、NPC、敌人专名都用英文原名或拉丁转写，不要音译成中文），但**地名与类型标签保持中文**（夜之城/海伍德；腐败警察/摩托增援/线人）。规模：2-3 幕、6-10 个场景、4-7 场遭遇。
   赛博朋克基调：巨型公司、街头帮派、义体、霓虹与雨夜；不写魔法与法术。

职业 key（10 个）：solo（独行侠/枪手）、netrunner（网络行者/黑客）、tech（技术师）、
medtech（医疗师/义医）、media（媒体人/记者）、cop（警察）、corporate（公司人）、
fixer（中间人/掮客）、nomad（游牧民）、rockerboy（摇滚客）。
护甲 key：leather(皮衣 SP4), kevlar(凯夫拉 SP7), light_armorjack(轻型防弹夹克 SP11),
medium_armorjack(中型防弹衣 SP12), heavy_armorjack(重型防弹衣 SP13),
bulletproof_shield(防弹盾 SP10)。
武器 key：brawling, light_melee, medium_melee, heavy_melee, very_heavy_melee,
medium_pistol, heavy_pistol, very_heavy_pistol, shotgun, assault_rifle。
义体 key：internal_agent, pain_editor, cyberaudio, cyberoptics, neural_link,
subdermal_armor, muscle_enhance, cyberarm, tool_hand。
技能白名单（41 项）：accounting, acting, athletics, brawling, bribery, bureaucracy,
business, composition, conceal_reveal, concentration, conversation, criminology,
cryptography, dancing, deduction, drive_land_vehicle, education,
electronics_security, evasion, first_aid, forgery, handgun, human_perception,
interrogation, library_search, local_expert, melee_weapon, paramedic, perception,
personal_grooming, persuasion, photography_film, pick_lock, pick_pocket,
play_instrument, resist_torture_drugs, shoulder_arms, stealth, streetwise,
tactics, tracking。"""


class AppState:
    def __init__(self, cordis_root: str | Path = "modules",
                 unlocks_path: str | Path = UNLOCKS_FILE):
        self.ui: WebUI | None = None
        self.session: GameSession | None = None
        self.story: StorySession | None = None    # 剧本模式对局（互斥于 session）
        self.story_meta: dict = {}                # 当前剧本的模块级信息（id/module_id/数据）
        self.thread: threading.Thread | None = None
        self.cordis = Cordis(cordis_root)
        self.meter = TokenMeter("saves/token_usage.json")
        self.balance_cache: dict = {"ts": 0.0, "data": None}   # 余额：60 秒内存缓存
        self.balance_ttl = 60.0
        self.unlocks_path = unlocks_path
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
        """持久化非敏感配置：provider/base_url/model/thinking + aipc 偏好
        （无任何密钥字段）。"""
        payload = {k: self._persisted[k]
                   for k in ("provider", "base_url", "model", "thinking", "api_style")
                   if k in self._persisted}
        if cfg is not None:
            payload.update({"provider": cfg.provider, "base_url": cfg.base_url,
                            "model": cfg.model, "thinking": cfg.thinking,
                            "api_style": cfg.api_style})
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
            # 应用已保存的非敏感覆盖（base_url/model/思考开关）
            if self._persisted.get("base_url"):
                cfg.base_url = normalize_base_url(str(self._persisted["base_url"]))
            if self._persisted.get("model"):
                cfg.model = str(self._persisted["model"])
            if self._persisted.get("thinking") in ("on", "off"):
                cfg.thinking = str(self._persisted["thinking"])
            if self._persisted.get("api_style") in ("auto", "responses", "chat"):
                cfg.api_style = str(self._persisted["api_style"])
            if self._runtime_key:
                cfg.api_key = self._runtime_key
        return cfg

    def _resolve_key(self, preset: dict, spec_key: str = "") -> str:
        """密钥优先级：本次提交 > 运行时内存 > 供应商环境变量。"""
        key = str(spec_key or "").strip() or (self._runtime_key or "")
        if key:
            return key
        for env in preset["key_envs"]:
            value = os.environ.get(env, "").strip()
            if value:
                return value
        return ""

    def _build_adapter(self, base_url: str, api_key: str, provider: str,
                       thinking: str = "", effort: str = "",
                       timeout: float = 90.0, max_retries: int = 3):
        """校验用适配器（不发起请求）：端点合法性由构造期完成。"""
        from ..harness.adapter import OpenAICompatibleAdapter
        preset = LLM_PROVIDERS.get(provider) or {}
        supports = bool(preset.get("supports_thinking"))
        return OpenAICompatibleAdapter(
            base_url=base_url, api_key=api_key, timeout=timeout,
            max_retries=max_retries,
            # 只有声明支持思考开关的供应商才下发，避免老接口被未知参数顶回 400
            thinking=(thinking or "") if supports else "",
            reasoning_effort=(effort or "") if supports else "")

    def apply_llm_settings(self, spec: dict) -> tuple[bool, str | None, LLMConfig | None]:
        provider = str(spec.get("provider", "deepseek"))
        if provider not in LLM_PROVIDERS:
            return False, "未知供应商", None
        preset = LLM_PROVIDERS[provider]
        raw_base = str(spec.get("base_url") or preset["base_url"])
        base_url = normalize_base_url(raw_base)
        model = str(spec.get("model") or (preset["models"][0] if preset["models"] else "")).strip()
        if provider == "custom" and not base_url:
            return False, "自定义供应商必须填写 API 地址", None
        key = self._resolve_key(preset, spec.get("api_key") or "")
        if not key:
            return False, "缺少 API 密钥：请在上方填写，或设置对应环境变量", None
        thinking = str(spec.get("thinking") or "").strip().lower()
        if thinking not in ("on", "off"):
            thinking = str(preset.get("thinking_default") or "")
        effort = str(spec.get("reasoning_effort") or "").strip().lower()
        style = str(spec.get("api_style") or "").strip().lower()
        if style not in ("auto", "responses", "chat"):
            style = str(self._persisted.get("api_style") or "auto")
        try:
            self._build_adapter(base_url, key, provider, thinking, effort, max_retries=1)
        except ValueError as e:
            return False, f"端点校验失败：{e}", None
        cfg = LLMConfig(base_url=base_url, api_key=key, model=model,
                        provider=provider, thinking=thinking,
                        reasoning_effort=effort, api_style=style)
        if spec.get("api_key"):
            self._runtime_key = key          # 仅内存
        self._persisted = {"provider": provider, "base_url": base_url,
                           "model": model, "thinking": thinking,
                           "api_style": style}
        self._save_llm_settings(cfg)
        self.llm_cfg = cfg
        self.balance_cache = {"ts": 0.0, "data": None}   # 换供应商后余额缓存失效
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
        # 没有密钥时 cfg 为 None，但已保存的 base/model/thinking 仍要回显，
        # 否则重启服务后界面会把用户填过的地址与模型清空（密钥本就不落盘）
        provider = cfg.provider if cfg else self._persisted.get("provider", "deepseek")
        preset = LLM_PROVIDERS.get(provider) or {}
        return {
            "llm_active": cfg is not None,
            "provider": provider,
            "base_url": (cfg.base_url if cfg
                         else normalize_base_url(str(self._persisted.get("base_url")
                                                     or preset.get("base_url") or ""))),
            "model": (cfg.model if cfg
                      else str(self._persisted.get("model")
                               or (preset.get("models") or [""])[0])),
            "thinking": (cfg.thinking if cfg
                         else str(self._persisted.get("thinking")
                                  or preset.get("thinking_default") or "")),
            "api_style": (cfg.api_style if cfg
                          else str(self._persisted.get("api_style") or "auto")),
            "use_responses": (cfg.uses_responses if cfg
                              else bool(preset.get("responses"))),
            "reasoning_effort": cfg.reasoning_effort if cfg else "",
            "key_source": ("runtime" if self._runtime_key else
                           "env" if cfg else "none"),
            # AI PC 开关随视图下发（持久化走 POST /api/settings/aipc 独立端点）
            "aipc": {"enabled": bool(self.aipc_prefs.get("enabled")),
                     "persona": str(self.aipc_prefs.get("persona") or "")},
        }

    # ------------------------------------------------------------ 模型列表
    def list_models(self, spec: dict | None = None) -> dict:
        """用**当前密钥**向供应商现拉模型列表（GET {base}/models）。

        这是「模型名不再写死」的落点：预设只是默认值，真实可用型号由账号决定。
        """
        spec = spec or {}
        provider = str(spec.get("provider") or (self.llm_cfg.provider if self.llm_cfg
                                                else self._persisted.get("provider", "deepseek")))
        preset = LLM_PROVIDERS.get(provider)
        if preset is None:
            return {"ok": False, "models": [], "error": "未知供应商",
                    "hint": "请先在「设置 → API 配置」里选一个供应商。"}
        current = self.llm_cfg
        base_url = normalize_base_url(str(
            spec.get("base_url")
            or (current.base_url if current and current.provider == provider else "")
            or preset["base_url"]))
        key = self._resolve_key(preset, spec.get("api_key") or "")
        if not base_url:
            return {"ok": False, "models": [], "provider": provider,
                    "error": "供应商没有可用的 API 地址",
                    "hint": "自定义供应商需要自己填写地址（填到 /v1 为止）。"}
        if not key:
            return {"ok": False, "models": [], "provider": provider,
                    "error": "缺少 API 密钥",
                    "hint": "先填入密钥再拉取；密钥只存在本服务内存里。"}
        try:
            adapter = self._build_adapter(base_url, key, provider,
                                          timeout=20.0, max_retries=1)
            models = adapter.list_models()
        except ValueError as e:
            return {"ok": False, "models": [], "provider": provider,
                    "error": f"端点校验失败：{e}"}
        except RuntimeError as e:
            return {"ok": False, "models": [], "provider": provider, "error": str(e)}
        except Exception as e:  # noqa: BLE001 —— HTTP/解析等各类失败统一回报
            status = getattr(e, "code", None)
            from ..harness.adapter import _status_hint
            detail = str(getattr(e, "reason", e))
            lowered = detail.lower()
            network = any(k in lowered for k in ("tunnel", "getaddrinfo", "timed out",
                                                 "connection refused", "ssl",
                                                 "urlerror", "proxy"))
            return {"ok": False, "models": [], "provider": provider,
                    "error": f"拉取失败（{type(e).__name__}）：{detail}",
                    "status": status,
                    "hint": ("网络不通：请求没到供应商，检查代理（HTTP_PROXY / "
                             "HTTPS_PROXY）或换网络后再试。") if network else
                            (_status_hint(status) if status else
                             "检查网络与 API 地址；也可以用「测试连接」单独验证密钥。")}
        return {"ok": True, "models": models, "count": len(models),
                "provider": provider, "base_url": base_url,
                "hint": "" if models else
                        "供应商返回了空列表：确认该账号已开通模型权限。"}

    # ------------------------------------------------------------ 账户余额
    def balance_view(self, refresh: bool = False) -> dict:
        """账户余额（只读）。60 秒内存缓存：界面轮询不会把余额接口打爆。"""
        now = time.time()
        cached = self.balance_cache.get("data")
        if (not refresh and cached is not None
                and now - float(self.balance_cache.get("ts") or 0) < self.balance_ttl):
            return {**cached, "cached": True}
        data = fetch_balance(self.llm_cfg)
        data = {**data, "cached": False, "fetched_at": round(now, 3)}
        self.balance_cache = {"ts": now, "data": data}
        return data

    # ------------------------------------------------------------ 对局
    def start(self, spec: dict) -> tuple[bool, str | None, dict]:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False, "已有一局在进行，请先重置", {}
        self.story = None
        self.story_meta = {}
        module = None
        try:
            if spec.get("continue"):
                save_path = str(spec.get("save") or SAVE_FILE)
                try:
                    raw = json.loads(Path(save_path).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    return False, f"存档读取失败：{e}", {}
                module_id = str(raw.get("module_id") or DEFAULT_MODULE_ID)
                module, errs, _w = self.cordis.ensure(module_id)
                if module is None:
                    return False, f"存档所需模组缺失：{module_id}", {}
                state = G.load_game(save_path, module=module)
            else:
                module_id = str(spec.get("module_id") or DEFAULT_MODULE_ID)
                module, errs, warns = self.cordis.ensure(module_id)
                if module is None:
                    return False, "；".join(errs) or f"模组加载失败：{module_id}", {}
                import random
                rng = (random.Random(spec["seed"]) if spec.get("seed") is not None
                       else random.Random())
                # RED 建卡：从模组预设角色里挑一个，队友同样取自预设（无掷骰/等级）
                player = build_character(spec, module, rng=rng)
                companions = build_companions(module, spec)
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

    # ------------------------------------------------------------ 剧本模式（互动剧）
    def stories_payload(self) -> dict:
        """剧本列表：modules/<id>/story_*.json + 结局图鉴解锁状态。"""
        unlocks = load_unlocks(self.unlocks_path)
        stories = []
        errors: list[str] = []
        for module_id, path in iter_story_files(self.cordis.root):
            story, errs = load_story_file(path)
            if story is None:
                errors.extend(errs)
                continue
            sid = str(story.get("id", ""))
            endings = [{"id": str(e), "title": str(story["nodes"][e].get("title", e)),
                        "rank": str(story["nodes"][e].get("rank", ""))}
                       for e in ending_ids(story)]
            mod = self.cordis.ensure(module_id)[0]
            stories.append({
                "story_id": sid,
                "module_id": module_id,
                "module_title": mod.title if mod else module_id,
                "title": story.get("title", ""),
                "en_title": story.get("en_title", ""),
                "synopsis": story.get("synopsis", ""),
                "endings_total": int(story.get("endings_total", len(endings))),
                "endings": endings,
                "unlocked": [str(e) for e in unlocks.get(sid, []) if isinstance(e, str)],
                "heroes": [{"index": i, "id": h.get("id", ""), "name": h.get("name", ""),
                            "title": h.get("title", ""), "hp": int(h.get("hp", 10)),
                            "desc": h.get("desc", "")}
                           for i, h in enumerate(story.get("heroes", []))],
            })
        return {"stories": stories, "errors": errors}

    def start_story(self, spec: dict) -> tuple[bool, str | None, dict]:
        """开始一局剧本演绎（与跑团对局共用 UI 槽与事件流，互斥）。"""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False, "已有一局在进行，请先重置", {}
        self.session = None
        self.story = None
        self.story_meta = {}
        story_id = str(spec.get("story_id") or "")
        target = None
        target_module_id = ""
        errors: list[str] = []
        for module_id, path in iter_story_files(self.cordis.root):
            data, errs = load_story_file(path)
            if data is None:
                errors.extend(errs)
                continue
            if str(data.get("id", "")) == story_id:
                target = data
                target_module_id = module_id
                break
        if target is None:
            return False, f"剧本不存在或已损坏：{story_id}", {}
        try:
            hero_index = int(spec.get("hero_index", 0))
        except (TypeError, ValueError):
            return False, "主角序号非法", {}
        if not 0 <= hero_index < len(target.get("heroes", [])):
            return False, f"主角序号越界：{hero_index}", {}
        import random
        rng = (random.Random(spec["seed"]) if spec.get("seed") is not None
               else random.Random())
        ui = WebUI()
        session = StorySession(target, ui, rng=rng)
        session.set_hero(hero_index)
        mod = self.cordis.ensure(target_module_id)[0]
        self.story_meta = {"story_id": story_id,
                           "module_id": target_module_id,
                           "module_title": mod.title if mod else "",
                           "story": target}
        with self.lock:
            self.generation += 1
        self.ui, self.story = ui, session

        def run():
            try:
                ending = session.run()
                unlocked = record_unlock(story_id, str(ending.get("id", "")),
                                         self.unlocks_path)
                ui._emit("game_over", reason="story_ended",
                         ending={"id": str(ending.get("id", "")),
                                 "title": str(ending.get("title", "")),
                                 "rank": str(ending.get("rank", "")),
                                 "text": str(ending.get("text", ""))},
                         unlocked=unlocked,
                         endings_total=int(target.get("endings_total", 0)),
                         dice_stats=ui.dice_stats())
            except AbortGame:
                pass
            except Exception as e:  # noqa: BLE001 —— 错误必须反馈到前端
                ui._emit("game_over", reason="error",
                         error=f"{type(e).__name__}: {e}")

        self.thread = threading.Thread(target=run, daemon=True, name="story-loop")
        self.thread.start()
        return True, None, self.snapshot()

    def reset(self) -> None:
        with self.lock:
            ui, session = self.ui, self.session
            self.session = None
            self.story = None
            self.story_meta = {}
            self.ui = None            # 置空：新连接不再回放已结束对局的历史
        if ui is not None:
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
        if self.story is not None:
            # 剧本模式：轻量状态（无队伍/地图/欧元，HP 由 board 事件与快照下发）
            story_data = self.story_meta.get("story", {})
            hero = self.story.hero
            base.update({
                "started": True,
                "running": bool(self.thread and self.thread.is_alive()),
                "mode": "story",
                "module_id": self.story_meta.get("module_id", ""),
                "module_title": self.story_meta.get("module_title", ""),
                "act": 0, "act_title": "", "location": story_data.get("title", ""),
                "eurobucks": None, "flags": {}, "party": [],
                "encounter_title": None, "map": None,
                "story": {"id": self.story_meta.get("story_id", ""),
                          "title": story_data.get("title", ""),
                          "hero": {"name": hero.get("name", ""),
                                   "title": hero.get("title", ""),
                                   "hp": self.story.hp,
                                   "hp_max": self.story.hp_max}},
                # 与跑团快照同口径：最后一条 ask 兜底（刷新页面后重建选项，
                # 不必只依赖 SSE 历史重放）
                "pending": _last_ask(self.ui),
            })
            return base
        if self.session is None:
            return base
        st = self.session.state
        scene = self.session.module.scene_by_id(st.scene_id) or {}
        enc = self.session.encounter
        party = st.party()
        eddies = sum(int(getattr(c, "eurobucks", 0)) for c in party)
        base.update({
            "started": True,
            "running": bool(self.thread and self.thread.is_alive()),
            "module_id": self.session.module.id,
            "module_title": self.session.module.title,
            "act": scene.get("act", 1),
            "act_title": self.session.module.act_titles.get(scene.get("act", 1), ""),
            "location": scene.get("location", ""),
            "eurobucks": eddies,
            "flags": st.flags,
            "encounter_title": enc.title if enc else None,
            # 战斗标记：顶栏「保存」据此提示"本回合开始时落盘"（战斗期指令槽
            # 由 session.player_combat_turn 在回合开头 poll）
            "in_encounter": enc is not None,
            "party": [_member_payload(c, c is st.player) for c in party],
            # 快照兜底：刷新页面后重建行动条（待答问题 + 行动经济）
            "pending": _last_ask(self.ui),
        })
        # AI PC 模式（§4.1）：enabled=控制器已挂载（真正接管）；requested=开关已开
        # 但本局降级为人类操控（LLM 未配置/组件未就绪/名字冲突），前端据此说明
        base["aipc"] = {
            "enabled": getattr(self.session, "aipc", None) is not None,
            "requested": bool(getattr(st, "aipc_enabled", False)),
            "player": st.player.name,
        }
        # 战术地图快照（docs/battlemap-design.md §4）：SSE map 事件的重放兜底；
        # 战斗中带上掩体实时耐久（VTT 掩体块/耐久条的数据源）
        base["map"] = st.map_brief(self.session.encounter)
        return base


def _last_ask(ui) -> dict | None:
    """事件流里最后一条 ``ask``（刷新页面后重建行动条的兜底数据）。"""
    if ui is None:
        return None
    for ev in reversed(getattr(ui, "history", []) or []):
        if ev.get("t") == "ask":
            return {"kind": ev.get("kind"), "prompt": ev.get("prompt", ""),
                    "options": list(ev.get("options") or [])}
    return None


def _cn(conds) -> list[str]:
    """状态 key → 中文（RED 状态在 rules.character 里以英文 key 表示）。"""
    table = getattr(M, "COND_CN", None) or {}
    fallback = {"controlled": "被控制", "prone": "倒地", "unconscious": "昏迷",
                "dead": "死亡", "stabilized": "已稳定",
                "crit_stabilized": "急救固定"}
    return [table.get(c, fallback.get(c, c)) for c in conds]


def _member_payload(c, is_player: bool) -> dict:
    """队伍面板载荷（PORT_SPEC §9）：RED 口径，无 AC / 等级 / 法术位。"""
    return {
        "name": c.name,
        "handle": getattr(c, "handle", ""),
        "role": getattr(c, "role", ""),
        "role_name": c.role_name if hasattr(c, "role_name") else "",
        "role_rank": getattr(c, "role_rank", 0),
        "role_ability": getattr(c, "role_ability", ""),
        "hp_cur": c.hp_cur, "hp_max": c.hp_max,
        "hp": c.hp_cur,                       # 兼容旧字段名
        "seriously_wounded": c.seriously_wounded_threshold,
        "wound": c.wound_state(), "wound_name": c.wound_name(),
        "sp": c.armor_at("torso"), "armor_name": c.armor_name(),
        "luck_cur": c.luck_cur, "luck_max": c.stat("luck"),
        "humanity_cur": c.humanity_cur, "humanity_max": c.humanity_max,
        "eurobucks": int(getattr(c, "eurobucks", 0)),
        "ip": int(getattr(c, "ip", 0)),
        "reputation": int(getattr(c, "reputation", 0)),
        "conditions": sorted(_cn(c.conditions)),
        "crit_injuries": [str(i.get("name", "")) for i in c.crit_injuries],
        "cyberware": [D.CYBERWARE[x]["name"] for x in c.cyberware
                      if x in D.CYBERWARE],
        "weapons": [D.WEAPONS[w]["name"] for w in c.weapons if w in D.WEAPONS],
        # 武器明细：VTT 悬停射程判定与作战卷宗用（kind 用于查射程表）
        "weapon_details": [{"key": w, "name": D.WEAPONS[w]["name"],
                            "kind": D.WEAPONS[w].get("kind", ""),
                            "dmg": D.WEAPONS[w].get("dmg", ""),
                            "rof": int(D.WEAPONS[w].get("rof", 1) or 1),
                            "ammo": c.ammo.get(w) if hasattr(c, "ammo") else None}
                           for w in c.weapons if w in D.WEAPONS],
        "skills": {k: int(v) for k, v in
                   (getattr(c, "skill_levels", {}) or {}).items()},
        "stats": {k: c.stat(k) for k in D.STAT_ORDER},
        # 人生之路（轻松模式建卡时投骰的履历，第 29-34 页）
        "lifepath": c.lifepath_lines() if hasattr(c, "lifepath_lines") else [],
        "is_player": is_player,
    }


def build_companions(module: Module, spec: dict) -> list:
    """AI 队友取自模组的预设角色表（玩家未另选时用模组的默认队友）。"""
    from ..rules.character import Character
    pregens = pregens_of(module)
    if not pregens:
        return []
    player_key = str(spec.get("pregen") or "")
    return [Character.from_pregen(k, pregens[k], kind="companion")
            for k in companion_ids(module, spec, player_key)]


def _adapter_for(cfg: LLMConfig, timeout: float | None = None,
                 max_retries: int | None = None):
    """按 LLMConfig 构建适配器（协议 + 思考模式 + 强度）。

    统一入口：对局 / 测试连接 / 模组生成都走这里，避免各处漏传——漏传时
    DeepSeek V4 会按默认（思考开启）响应，而带 tools 的第二轮请求少了思维链
    就会 400。

    协议见 ``LLMConfig.uses_responses``：auto 时供应商支持 Responses API 就走
    ``POST {base}/responses``，否则退回 ``/chat/completions``；auto 下 Responses
    适配器遇 404 会自行降级，显式指定协议时则直接报错（便于排查）。
    """
    from ..harness.adapter import OpenAICompatibleAdapter
    from ..harness.responses import OpenAIResponsesAdapter
    preset = LLM_PROVIDERS.get(cfg.provider) or {}
    supports = bool(preset.get("supports_thinking"))
    kwargs = dict(
        base_url=cfg.base_url, api_key=cfg.api_key,
        timeout=timeout if timeout is not None else cfg.request_timeout,
        max_retries=max_retries if max_retries is not None else cfg.max_retries,
        thinking=(cfg.thinking or "") if supports else "",
        reasoning_effort=(cfg.reasoning_effort or "") if supports else "")
    if not cfg.uses_responses:
        from ..harness.adapter import OpenAICompatibleAdapter as _Chat
        return _Chat(**kwargs)
    auto = (cfg.api_style or "auto").strip().lower() not in (
        "responses", "response", "openai-responses")
    return OpenAIResponsesAdapter(
        fallback_chat=OpenAICompatibleAdapter(**kwargs) if auto else None, **kwargs)


def build_session(state: G.GameState, ui: WebUI, llm_cfg: LLMConfig | None,
                  module: Module | None = None,
                  meter: TokenMeter | None = None) -> GameSession:
    """按 LLM 配置构建 AI 模式会话，否则剧本模式。

    AI PC（aipc-design §0/§4.1）：仅当 llm_cfg 非 None 且 state.aipc_enabled 时
    才挂 AIPCController；开关关闭时全部新分支短路，行为与基线完全一致。
    本阶段接缝只做参数与降级通路——trpg.st / trpg.game.aipc 由后续集成落地，
    未就绪（ImportError）、attach 返回 None（玩家名与同伴冲突，§2.1）或
    卡/persona 数据无效时，回退人类操控并在 UI 说明。"""
    module = module or state.module
    if llm_cfg is None:
        if getattr(state, "aipc_enabled", False):
            _aipc_note(ui, "（AI 代打未启用：尚未接入 LLM，本局由你亲自操控。"
                           "开关已保存，接入 LLM 后新开对局生效。）")
        return GameSession(state, ui, loop=None, module=module, meter=meter)
    from ..game.agents import make_dm_agent, make_pc_agent
    from ..harness.loop import DefaultAgentLoop, TruncateCompactor

    adapter = _adapter_for(llm_cfg)
    loop = DefaultAgentLoop(registry=None, adapter=adapter,
                            compactor=TruncateCompactor(token_limit=12000))
    dm_agent = make_dm_agent(llm_cfg.model, module)
    pc_agents = {}
    # RED：队友即模组预设角色；agent 卡素材直接取 pregen（含 persona/voice/desc）
    pregens = pregens_of(module)
    for ch in state.companions:
        pregen = _pregen_for(pregens, ch)
        kit = "、".join([D.WEAPONS[w]["name"] for w in ch.weapons if w in D.WEAPONS]
                        + ([ch.armor_name()] if ch.armor_key else [])) or "徒手"
        try:
            pc_agents[ch.name] = make_pc_agent(pregen, ch.role_rank, ch.weapons,
                                               kit, llm_cfg.model,
                                               user=state.player.name)
        except TypeError as e:  # 队友 agent 组装线签名变动时不阻塞开局
            _aipc_note(ui, f"（队友 {ch.name} 的 AI 未挂载：{e}）")
    session = GameSession(state, ui, loop=loop, dm_agent=dm_agent, pc_agents=pc_agents,
                          module=module, meter=meter)
    _maybe_attach_aipc(session, state, ui, llm_cfg, loop)
    return session


def _pregen_for(pregens: dict, ch) -> dict:
    """在模组预设法里找回该角色对应的 pregen（供队友 agent 卡使用）。"""
    for pregen in pregens.values():
        if str(pregen.get("name") or "") == ch.name:
            return pregen
    return {"id": ch.name, "name": ch.name, "role": ch.role,
            "persona": ch.persona, "voice": ch.voice, "desc": ch.desc}


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


def _pregen_view(key: str, pregen: dict) -> dict:
    """预设角色视图（建卡界面用：职业/十项属性/PW·重伤阈值/技能/装备）。"""
    stats = {k: int((pregen.get("stats") or {}).get(k, D.STAT_MIN))
             for k in D.STAT_ORDER}
    hp = int(pregen.get("hp") or
             (10 + 5 * ((stats.get("body", 0) + stats.get("will", 0) + 1) // 2)))
    role = str(pregen.get("role") or "solo")
    armor = pregen.get("armor")
    return {
        "key": key,
        "name": str(pregen.get("name") or key),
        "handle": str(pregen.get("handle") or ""),
        "role": role,
        "role_name": (D.ROLES.get(role) or {}).get("name", role),
        "role_rank": int(pregen.get("role_rank", 4)),
        "role_ability": str(pregen.get("role_ability") or ""),
        "stats": stats,
        "hp": hp,
        "seriously_wounded": max(1, -(-hp // 2)),
        "luck": stats.get("luck", 0),
        "humanity": stats.get("emp", 0) * D.HUMANITY_PER_EMP,
        "skills": {k: int(v) for k, v in (pregen.get("skill_levels") or {}).items()},
        "armor": armor or "", "armor_name": (D.ARMORS.get(armor) or {}).get("name", ""),
        "sp": int((D.ARMORS.get(armor) or {}).get("sp", 0)),
        "weapons": [D.WEAPONS[w]["name"] for w in (pregen.get("weapons") or [])
                    if w in D.WEAPONS],
        "cyberware": [D.CYBERWARE[c]["name"] for c in (pregen.get("cyberware") or [])
                      if c in D.CYBERWARE],
        "gear": [D.GEAR[g]["name"] for g in (pregen.get("gear") or {}) if g in D.GEAR],
        "eurobucks": int(pregen.get("eurobucks", 0)),
        "persona": str(pregen.get("persona") or ""),
        "voice": str(pregen.get("voice") or ""),
        "desc": str(pregen.get("desc") or ""),
    }


def _provider_view(key: str, preset: dict) -> dict:
    """供应商预设 → 界面载荷（含文档链接、思考支持与模型列表接口）。"""
    return {
        "key": key,
        "name": preset["name"],
        "base_url": preset["base_url"],
        "models": list(preset.get("models") or []),
        "key_envs": list(preset.get("key_envs") or []),
        "default": bool(preset.get("default")),
        "supports_thinking": bool(preset.get("supports_thinking")),
        "thinking_default": str(preset.get("thinking_default") or ""),
        "responses": bool(preset.get("responses")),
        "models_path": str(preset.get("models_path") or "/models"),
        "docs": str(preset.get("docs") or ""),
        "note": str(preset.get("note") or ""),
    }


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
        pregens = pregens_of(mod)
        s["pregens"] = [_pregen_view(k, pregens[k]) for k in pregens
                        if isinstance(pregens[k], dict)]
        s["default_companions"] = [k for k in default_companion_keys(mod)
                                   if k in pregens]
        modules.append(s)
    return {
        "ruleset": "cyberpunk-red",
        "title": M.MODULE["title"], "en_title": M.MODULE["en_title"],
        "setting": M.MODULE["setting"], "synopsis": M.MODULE["synopsis"],
        "llm": app.llm_settings_view(),
        "providers": [_provider_view(k, v) for k, v in LLM_PROVIDERS.items()],
        "modules": modules,
        # RED 数据面（PORT_SPEC §10）
        "stats": [{"key": k, "name": D.STATS[k]["name"], "abbr": D.STATS[k]["abbr"],
                   "desc": D.STATS[k]["desc"]} for k in D.STAT_ORDER],
        "skills": [{"key": k, "name": v["name"], "stat": v["stat"],
                    "x2": bool(v.get("x2")), "desc": v.get("desc", "")}
                   for k, v in D.SKILLS.items()],
        "roles": [{"key": k, "name": v["name"], "en": v.get("en", ""),
                   "nickname": v.get("nickname", ""), "desc": v["desc"],
                   "ability": v["ability"]["name"],
                   "ability_desc": v["ability"]["desc"]}
                  for k, v in D.ROLES.items()],
        "difficulty": [{"dv": dv, "name": v["name"], "desc": v["desc"]}
                       for dv, v in sorted(D.DIFFICULTY.items())],
        # 掩体材质 → 耐久（第 21 页表）：VTT 画掩体的单一数据源，
        # 前端不重复硬编码，避免与 rules/data.py 漂移
        "cover_materials": [{"material": k, "hp": int(v)}
                            for k, v in D.COVER.items()],
        # 远程射程带（第 19 页表）：VTT 悬停提示「这个距离打不打得到」
        "range_bands": [{"lo": int(lo), "hi": int(hi)}
                        for lo, hi in D.RANGE_BANDS],
        "ranged_dv": {k: list(v) for k, v in D.RANGED_DV.items()},
        "meters_per_cell": METERS_PER_CELL,
        "weapons": [{"key": k, "name": v["name"], "kind": v.get("kind", ""),
                     "dmg": v.get("dmg", ""), "rof": v.get("rof", 0),
                     "ammo": v.get("ammo"), "cost": v.get("cost", 0),
                     "skill": v.get("skill", ""),
                     "skill_name": (D.SKILLS.get(v.get("skill")) or {}).get("name", ""),
                     "kind_name": D.WEAPON_TYPES.get(v.get("kind"), "")}
                    for k, v in D.WEAPONS.items()],
        "armors": [{"key": k, "name": v["name"], "sp": v["sp"],
                    "cost": v.get("cost", 0), "notes": v.get("notes", "")}
                   for k, v in D.ARMORS.items()],
        "gear": [{"key": k, "name": v["name"], "cost": v.get("cost", 0),
                  "notes": v.get("notes", "")} for k, v in D.GEAR.items()],
        "cyberware": [{"key": k, "name": v["name"], "humanity": v.get("humanity", 0),
                       "cost": v.get("cost", 0), "text": v.get("text", "")}
                      for k, v in D.CYBERWARE.items()],
        "wounds": [{"key": k, "name": D.WOUND_STATES[k]["name"],
                    "check_penalty": D.WOUND_STATES[k].get("check_penalty", 0),
                    "desc": D.WOUND_STATES[k].get("desc", "")}
                   for k in D.WOUND_ORDER],
        "dice_rule": "1d10 + 属性 + 技能 vs 难度值 DV（掷出 10 暴击追加、掷出 1 暴击失败追加）",
        "currency": {"name": D.CURRENCY, "abbr": D.CURRENCY_ABBR},
        "ip_rule": {"skill_base": D.IP_SKILL_BASE, "stat_base": D.IP_STAT_BASE,
                    "role_base": D.IP_ROLE_BASE, "skill_max": D.SKILL_MAX},
    }


# ---------------------------------------------------------------- AI 生成
def generate_module(app: AppState, spec: dict) -> tuple[Module | None, list[str], list[str]]:
    """用当前配置的 LLM 按规范生成一个原子模组并安装。"""
    cfg = app.llm_cfg
    if cfg is None:
        return None, ["尚未配置 LLM：请先在「设置 → API 配置」中接入供应商"], []
    from ..harness.adapter import OpenAICompatibleAdapter  # noqa: F401 —— 保持后端兼容
    theme = str(spec.get("theme") or "").strip()
    hints = str(spec.get("hints") or "").strip()
    if not theme:
        return None, ["请填写模组主题（如：月夜狼人村庄的三幕恐怖故事）"], []
    user_prompt = (f"请创作一个全新的原创模组。主题：{theme}\n"
                   + (f"附加要求：{hints}\n" if hints else "")
                   + "记住：只输出模组 JSON 本身，符合上述规范的全部硬性约束。")
    adapter = _adapter_for(cfg, timeout=180.0, max_retries=2)
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
    server_version = "TRPG/" + ".".join(__version__.split(".")[:2])

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
        if path.startswith("/api/asset/"):
            return self._asset(path[len("/api/asset/"):])
        if path == "/api/stream":
            return self._stream()
        if path == "/api/meta":
            return self._json(meta_payload(self.app))
        if path == "/api/state":
            return self._json(self.app.snapshot())
        if path == "/api/stories":
            return self._json(self.app.stories_payload())
        if path == "/api/pending":
            ui = self.app.ui
            return self._json({"pending": ui.pending_view() if ui else None})
        if path == "/api/usage":
            query = urllib.parse.parse_qs(self.path.split("?", 1)[-1])
            try:
                days = int((query.get("days") or ["371"])[0])
            except ValueError:
                days = 371
            return self._json(self.app.meter.summary(days=max(7, min(days, 400))))
        if path == "/api/balance":
            query = urllib.parse.parse_qs(self.path.split("?", 1)[-1])
            refresh = (query.get("refresh") or ["0"])[0] not in ("", "0", "false")
            return self._json(self.app.balance_view(refresh=refresh))
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
                                      "module_id": raw.get("module_id", DEFAULT_MODULE_ID),
                                      "scene_id": raw.get("scene_id", ""),
                                      "ts": round(f.stat().st_mtime)})
                    except (OSError, json.JSONDecodeError):
                        continue
            saves.sort(key=lambda s: -s["ts"])
            return self._json({"saves": saves})
        if path == "/api/settings/llm":
            view = self.app.llm_settings_view()
            view["providers"] = [_provider_view(k, v)
                                 for k, v in LLM_PROVIDERS.items()]
            return self._json(view)
        if path == "/api/models":
            query = urllib.parse.parse_qs(self.path.split("?", 1)[-1])
            spec = {key: (vals[0] if vals else "")
                    for key, vals in query.items()
                    if key in ("provider", "base_url")}
            return self._json(self.app.list_models(spec))
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

    _ASSET_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp",
                    ".svg": "image/svg+xml"}

    def _asset(self, rel: str) -> None:
        """模组资产（地图/棋子图）：/api/asset/<module_id>/<子路径>。

        只服务 modules/<module_id>/ 下的图片；路径净化禁止上跳与绝对路径，
        扩展名白名单（与 loader._check_asset_path 同一约定）。"""
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        if len(parts) < 2 or any("\\" in p or ":" in p for p in parts):
            return self._json({"error": "not found"}, 404)
        module_id, *rest = parts
        if not all(c.isalnum() or c in "-_" for c in module_id):
            return self._json({"error": "not found"}, 404)
        ext = ("." + rest[-1].rsplit(".", 1)[-1]) if "." in rest[-1] else ""
        if ext.lower() not in self._ASSET_TYPES:
            return self._json({"error": "not found"}, 404)
        target = (Path("modules") / module_id / Path(*rest)).resolve()
        root = (Path("modules") / module_id).resolve()
        if root not in target.parents or not target.exists():
            return self._json({"error": "not found"}, 404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self._ASSET_TYPES[ext.lower()])
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
        if path == "/api/story/start":
            ok, err, snap = self.app.start_story(body)
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
        if path == "/api/command":
            # 桌面交互指令通道（点图移动等，docs/battlemap-design.md §5）：
            # 与 AI PC 旁听指令共用 post_command 槽；战斗回合由引擎按需 poll
            ui = self.app.ui
            if ui is None or not hasattr(ui, "post_command"):
                return self._json({"ok": False, "error": "对局未开始"}, 409)
            text = str(body.get("text") or "").strip()
            if not text:
                return self._json({"ok": False, "error": "指令为空"}, 400)
            ui.post_command(text)
            return self._json({"ok": True})
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
                return self._json({"ok": False, "error": "尚未配置 LLM",
                                   "hint": "先在「API 配置」里选供应商、填密钥并保存。"}, 400)
            adapter = _adapter_for(cfg, timeout=30.0, max_retries=1)
            from ..harness.responses import OpenAIResponsesAdapter
            style = "responses" if isinstance(adapter, OpenAIResponsesAdapter) else "chat"
            try:
                resp = adapter.chat(cfg.model,
                                    [{"role": "user", "content": "回复：OK"}],
                                    None, 0.0, 16)
                if self.app.meter is not None and resp.usage:
                    self.app.meter.record("test", cfg.model, resp.usage)
                # auto 模式下 Responses 404 会自动降级，如实回报实际通道
                fell_back = bool(getattr(adapter, "fell_back", False))
                return self._json({"ok": True,
                                   "reply": (resp.content or "").strip()[:60],
                                   "model": cfg.model,
                                   "api_style": "chat" if fell_back else style,
                                   "fallback": fell_back,
                                   "reasoning": bool(resp.reasoning)})
            except Exception as e:  # noqa: BLE001 —— 失败原因要原样交给界面
                text = str(e)
                hint = ""
                lowered = text.lower()
                if any(k in lowered for k in ("tunnel", "urlerror", "getaddrinfo",
                                             "timed out", "connection refused",
                                             "ssl", "proxy")):
                    hint = ("网络不通：该请求根本没到供应商。检查本机代理设置"
                            "（HTTP_PROXY / HTTPS_PROXY）、公司网关，或换一个网络再试。")
                elif "401" in text or "认证" in text:
                    hint = "密钥不对：到供应商控制台重新生成，粘贴时别带空格。"
                elif "402" in text or "余额" in text:
                    hint = "账户余额不足：充值后重试，或换一个供应商。"
                elif "404" in text:
                    hint = ("API 地址写错了：DeepSeek 用 https://api.deepseek.com，"
                            "智谱用 .../api/paas/v4，Kimi 用 .../v1。"
                            "可点「拉取模型列表」验证地址与密钥。")
                elif "400" in text or "模型名" in text:
                    hint = ("多为模型名不存在或参数不被支持：点「拉取模型列表」"
                            "挑一个账号可用的模型；若开了思考模式，先关掉再试。")
                    if style == "responses":
                        hint += (" 若协议选的是 Responses，可切回「自动」或 "
                                 "Chat Completions 再试一次。")
                return self._json({"ok": False, "error": text, "hint": hint,
                                   "api_style": style}, 400)
        if path == "/api/models":
            return self._json(self.app.list_models(body))
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
