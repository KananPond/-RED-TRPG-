"""CTRCG 入口：单人 AI 跑团《赛博朋克 RED》（默认模组 cpred-easy-mode · 夜之城 2045）。

用法：
  python main.py --web             # 网页版（推荐）：开始页含 剧本模式/模组/存档/帮助/设置
  python main.py                   # 终端版：有 LLM 配置则 AI 模式，否则经典模式
  python main.py --offline         # 强制经典模式（离线）
  python main.py --story           # 剧本模式（互动剧）：分支选择 · 多结局
  python main.py --demo            # 自动演示（离线，机器人全程代打）
  python main.py --module <id>     # 指定模组（modules/ 目录，Cordis 原子加载）
  python main.py --new / --load PATH / --seed N / --check-llm

建卡口径：《赛博朋克 RED》轻松模式不掷骰——从模组的预设角色（pregens）里挑一个，
填上自己的名字/绰号，再点几名预设角色作 AI 队友。
"""

from __future__ import annotations

import argparse
import random
import sys

from ctrcg import __version__
from ctrcg.campaign.cordis import Cordis, default_fallback_module
from ctrcg.campaign.loader import Module
from ctrcg.config import LLMConfig
from ctrcg.game import state as G
from ctrcg.game.session import GameSession
from ctrcg.ui.cli import AutoUI, ConsoleUI
from ctrcg.ui.charcreate import (build_from_pregen, create_player_console,
                                 pregens_of)

MODULES_DIR = "modules"
# 默认模组：《赛博朋克 RED：轻松模式》(Cyberpunk RED: Easy Mode)
DEFAULT_MODULE_ID = "cpred-easy-mode"
DEFAULT_MODULE_TITLE = "赛博朋克 RED：轻松模式"


def load_module(cordis_dir: str, module_id: str | None) -> Module:
    registry = Cordis(cordis_dir)
    mid = module_id or DEFAULT_MODULE_ID
    mod, errors, _warns = registry.ensure(mid)
    if mod is None:
        if mid == DEFAULT_MODULE_ID:
            # 默认模组缺失（如全新环境只有代码）：回退到代码内置的 RED 兜底剧本，
            # 游戏永不硬失败
            print(f"默认模组《{DEFAULT_MODULE_TITLE}》加载失败：{'；'.join(errors)}\n"
                  "已回退到内置的 RED 兜底剧本。", file=sys.stderr)
            mod = default_fallback_module()
        else:
            print(f"模组加载失败：{'；'.join(errors)}", file=sys.stderr)
            sys.exit(1)
    return mod


def build_companions(module: Module, picks: list[str]) -> list:
    """按预设角色 key 装配 AI 队友。"""
    pregens = pregens_of(module)
    from ctrcg.rules.character import Character
    return [Character.from_pregen(k, pregens[k], kind="companion")
            for k in picks if k in pregens]


def make_pc_agents(cfg: LLMConfig, module: Module, state) -> dict:
    """按预设角色构建队友 Agent（RED：职业 rank 与随身装备进提示词）。"""
    from ctrcg.game.agents import make_pc_agent
    from ctrcg.rules import data as D
    pregens = pregens_of(module)
    agents = {}
    for ch in state.companions:
        pregen = next((p for p in pregens.values()
                       if str(p.get("name", "")) == ch.name),
                      {"id": ch.name, "name": ch.name, "role": ch.role,
                       "persona": ch.persona, "voice": ch.voice, "desc": ch.desc})
        kit = "、".join([D.WEAPONS[w]["name"] for w in ch.weapons if w in D.WEAPONS]
                        + ([ch.armor_name()] if ch.armor_key else [])) or "徒手"
        try:
            agents[ch.name] = make_pc_agent(pregen, ch.role_rank, ch.weapons, kit,
                                            cfg.model, user=state.player.name)
        except TypeError as e:  # 队友 agent 组装线签名变动时不阻塞开局
            print(f"（队友 {ch.name} 的 AI 未挂载：{e}）", file=sys.stderr)
    return agents


def build_ai_stack(cfg: LLMConfig, module: Module):
    from ctrcg.game.agents import make_dm_agent
    from ctrcg.harness.adapter import OpenAICompatibleAdapter
    from ctrcg.harness.loop import DefaultAgentLoop, TruncateCompactor
    adapter = OpenAICompatibleAdapter(
        base_url=cfg.base_url, api_key=cfg.api_key,
        timeout=cfg.request_timeout, max_retries=cfg.max_retries)
    loop = DefaultAgentLoop(
        registry=None, adapter=adapter,           # registry 按次注入
        compactor=TruncateCompactor(token_limit=12000))
    dm_agent = make_dm_agent(cfg.model, module)
    return loop, dm_agent


def check_llm(cfg: LLMConfig) -> int:
    from ctrcg.harness.adapter import OpenAICompatibleAdapter
    adapter = OpenAICompatibleAdapter(cfg.base_url, cfg.api_key,
                                      timeout=cfg.request_timeout, max_retries=1)
    try:
        resp = adapter.chat(cfg.model, [{"role": "user", "content": "回复：OK"}],
                            None, 0.0, 16)
        print("端点正常，模型回复：", (resp.content or "").strip()[:80])
        return 0
    except Exception as e:  # noqa: BLE001
        print("端点不可用：", e)
        return 1


def run_story_console(args, ui: ConsoleUI, rng: random.Random) -> int:
    """终端剧本模式（互动剧）：选剧本 → 选主角 → 分支演绎 → 结局卡。

    剧本随模组分发（modules/<id>/story_*.json），基于原版模组的世界观创作；
    结局图鉴持久化到 saves/story_endings.json。"""
    from ctrcg.game.story import (StorySession, ending_ids, load_stories,
                                  load_unlocks, record_unlock)
    stories, errors = load_stories(MODULES_DIR)
    if args.story_id:
        stories = [s for s in stories if str(s.get("id")) == args.story_id]
    for e in errors:
        ui.system_line(f"（剧本文件已跳过：{e}）")
    if not stories:
        ui.system_line("没有可用的剧本（modules/<模组>/story_*.json）。")
        return 1
    story = stories[0] if len(stories) == 1 else stories[ui.choose(
        "选择一个剧本",
        [f"《{s.get('title', s.get('id'))}》—— 模组《{s.get('_module_id', '?')}》"
         for s in stories])]
    heroes = story.get("heroes", [])
    hero_idx = 0 if len(heroes) == 1 else ui.choose(
        f"《{story.get('title', '')}》—— 选择你的主角",
        [f"{h.get('name', '?')}（{h.get('title', '')} · PW {h.get('hp', 10)}）"
         f"—— {h.get('desc', '')}" for h in heroes])
    session = StorySession(story, ui, rng=rng)
    session.set_hero(hero_idx)
    ending = session.run()
    story_id = str(story.get("id", ""))
    unlocked = record_unlock(story_id, str(ending.get("id", "")))
    nodes = story.get("nodes", {})
    total = int(story.get("endings_total", 0))
    names = [str(nodes[e].get("title", e)) for e in unlocked if e in nodes]
    ui.system_line("=" * 62)
    ui.system_line(f"—— 达成结局「{ending.get('title', '')}」"
                   f"（{ending.get('rank', '')}） ——")
    ui.raw(str(ending.get("text", "")))
    ui.system_line(f"结局图鉴：{len(unlocked)} / {total} 已解锁（{'、'.join(names)}）")
    ui.system_line("换一位主角、换一条路——同一个夜晚还有别的结局。")
    return 0


def run_web(args, cfg: LLMConfig | None) -> int:
    """启动本地网页版：浏览器前端 + 引擎 + DeepSeek 等 Agent。"""
    import threading
    import webbrowser
    from ctrcg.web.server import AppState, make_server

    app = AppState()
    if args.offline:
        app.llm_cfg = None
    # 端口被占用（如上局未关干净）时自动顺延，避免直接崩溃
    server, port = None, args.port
    for _ in range(10):
        try:
            server = make_server(app, port=port)
            break
        except OSError:
            print(f"端口 {port} 被占用，尝试 {port + 1} ……")
            port += 1
    if server is None:
        print(f"无法打开端口 {args.port}~{port}，请用 --port 指定其他端口。")
        return 1
    url = f"http://127.0.0.1:{port}"
    mode = f"AI 模式（{app.llm_cfg.provider} · {app.llm_cfg.model}）" if app.llm_cfg \
        else "经典模式（离线 · 含剧本模式互动剧）"
    print(f"CTRCG 网页版 · 默认模组《{DEFAULT_MODULE_TITLE}》 · {mode}")
    print(f"请用浏览器打开：{url}")
    print("（服务运行期间请保持本窗口开启，Ctrl+C 停止）\n")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        app.reset()
        print("\n（服务已停止。）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CTRCG 单人 AI 跑团")
    parser.add_argument("--offline", action="store_true", help="强制经典模式（离线）")
    parser.add_argument("--story", action="store_true",
                        help="剧本模式（互动剧）：分支选择 · 多结局")
    parser.add_argument("--story-id", metavar="ID", default=None,
                        help="直接指定剧本 id（默认列出全部可选）")
    parser.add_argument("--demo", action="store_true", help="自动演示（离线，AutoUI 全程代打）")
    parser.add_argument("--new", action="store_true", help="忽略存档，新建角色")
    parser.add_argument("--load", metavar="PATH", help="从指定存档开始")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--check-llm", action="store_true")
    parser.add_argument("--web", action="store_true", help="启动本地网页版（推荐）")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--module", metavar="ID", default=None,
                        help=f"模组 id（modules/ 目录，默认 {DEFAULT_MODULE_ID}）")
    args = parser.parse_args(argv)

    cfg = LLMConfig.from_env()
    if args.check_llm:
        if cfg is None:
            print("未配置 LLM 环境变量（CTRCG_LLM_BASE_URL / CTRCG_LLM_API_KEY）。")
            return 1
        return check_llm(cfg)

    if args.web:
        return run_web(args, cfg)

    if args.story:
        return run_story_console(args, ConsoleUI(), random.Random(args.seed))

    module = load_module(MODULES_DIR, args.module)
    rng = random.Random(args.seed)
    pregens = pregens_of(module)

    if args.demo:
        ui = AutoUI()
        if not pregens:
            print(f"模组《{module.title}》没有预设角色（pregens），无法演示。",
                  file=sys.stderr)
            return 1
        first = next(iter(pregens))
        player, companions = build_from_pregen(module, first)
        state = G.GameState(player, companions, rng, module=module)
        session = GameSession(state, ui, loop=None, module=module)
        session.run()
        return 0

    ui = ConsoleUI()

    # 载入或新建
    state = None
    if args.load:
        state = G.load_game(args.load, module=module)
        ui.system_line(f"已读取存档 {args.load}")
    elif not args.new:
        import os
        if os.path.exists("saves/game.json"):
            i = ui.choose("发现存档，是否继续上次的冒险？", ["读取存档", "新建角色"])
            if i == 0:
                state = G.load_game("saves/game.json", module=module)

    if state is None:
        player, picks = create_player_console(ui, rng, module)
        state = G.GameState(player, build_companions(module, picks), rng,
                            module=module)
        ui.system_line(f"《{module.title}》开局。"
                       + (f"同行队友：{'、'.join(c.name for c in state.companions)}"
                          if state.companions else "本局独自行动。"))

    if args.offline or cfg is None:
        if cfg is None:
            ui.system_line("未检测到 LLM 配置（CTRCG_LLM_BASE_URL / CTRCG_LLM_API_KEY），"
                           "进入经典模式：DM 按模组推进，AI 同伴使用战斗直觉。"
                           "（剧本模式互动剧见 --story / 网页版开始页。）")
        session = GameSession(state, ui, loop=None, module=module)
    else:
        loop, dm_agent = build_ai_stack(cfg, module)
        session = GameSession(state, ui, loop=loop, dm_agent=dm_agent,
                              pc_agents=make_pc_agents(cfg, module, state),
                              module=module)
    session.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n（冒险暂停——随时回来继续。）")
        sys.exit(130)
