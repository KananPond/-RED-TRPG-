"""CTRCG 入口：单人 AI 跑团《星坠矿脉的阴影》。

用法：
  python main.py --web             # 网页版（推荐）：开始页含 模组/存档/帮助/设置
  python main.py                   # 终端版：有 LLM 配置则 AI 模式，否则剧本模式
  python main.py --offline         # 强制剧本模式（离线）
  python main.py --demo            # 自动演示（离线，机器人全程代打）
  python main.py --module <id>     # 指定模组（modules/ 目录，Cordis 原子加载）
  python main.py --new / --load PATH / --seed N / --check-llm
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
from ctrcg.rules.character import Character
from ctrcg.ui.cli import AutoUI, ConsoleUI
from ctrcg.ui.charcreate import create_player_console


def load_module(cordis_dir: str, module_id: str | None) -> Module:
    registry = Cordis(cordis_dir)
    mid = module_id or "starfall"
    mod, errors, _warns = registry.ensure(mid)
    if mod is None:
        if mid == "starfall":
            mod = default_fallback_module()
        else:
            print(f"模组加载失败：{'；'.join(errors)}", file=sys.stderr)
            sys.exit(1)
    return mod


def build_companions(module: Module, rng: random.Random, level: int = 1) -> list[Character]:
    """队友由模组决定；模组未定义同伴时使用默认三人组。"""
    from ctrcg.campaign import module_data as MD
    out = []
    for comp in (module.companions or MD.default_companions()):
        ch = Character.create(
            name=comp["name"], race=comp["race"], klass=comp["klass"],
            background=comp["background"], scores=comp["scores"],
            skill_choices=comp["skills"], fighting_style=comp.get("fighting_style", ""),
            level=level)
        ch.apply_default_choices()
        out.append(ch)
    return out


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
        else "剧本模式（离线）"
    print(f"\n《星坠矿脉的阴影》网页版 · {mode}")
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
    parser.add_argument("--offline", action="store_true", help="强制剧本模式（离线）")
    parser.add_argument("--demo", action="store_true", help="自动演示（离线，AutoUI 全程代打）")
    parser.add_argument("--new", action="store_true", help="忽略存档，新建角色")
    parser.add_argument("--load", metavar="PATH", help="从指定存档开始")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--check-llm", action="store_true")
    parser.add_argument("--web", action="store_true", help="启动本地网页版（推荐）")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--module", metavar="ID", default=None,
                        help="模组 id（modules/ 目录，默认 starfall）")
    args = parser.parse_args(argv)

    cfg = LLMConfig.from_env()
    if args.check_llm:
        if cfg is None:
            print("未配置 LLM 环境变量（CTRCG_LLM_BASE_URL / CTRCG_LLM_API_KEY）。")
            return 1
        return check_llm(cfg)

    if args.web:
        return run_web(args, cfg)

    module = load_module("modules", args.module)
    rng = random.Random(args.seed)

    if args.demo:
        ui = AutoUI()
        start = module.party_level_start
        player = Character.create(
            name="凯尔", race="human", klass="fighter", background="soldier",
            scores={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
            skill_choices=["athletics", "intimidation"], fighting_style="defense",
            level=start)
        state = G.GameState(player, build_companions(module, rng, start), rng,
                            module=module)
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
        player = create_player_console(ui, rng)
        if module.party_level_start > player.level:
            player.level_up_to(module.party_level_start)
            ui.system_line(f"《{module.title}》以 {module.party_level_start} 级开局。")
        state = G.GameState(player, build_companions(module, rng, player.level),
                            rng, module=module)
        ui.system_line(f"同伴入队：{'、'.join(c.name for c in state.companions)}")

    if args.offline or cfg is None:
        if cfg is None:
            ui.system_line("未检测到 LLM 配置（CTRCG_LLM_BASE_URL / CTRCG_LLM_API_KEY），"
                           "进入剧本模式：DM 按模组推进，AI 同伴使用战斗直觉。")
        session = GameSession(state, ui, loop=None, module=module)
    else:
        loop, dm_agent = build_ai_stack(cfg, module)
        from ctrcg.game.agents import make_pc_agent
        pc_agents = {}
        for comp, ch in zip(module.companions, state.companions):
            slots_hint = "法术位：" + (
                "、".join(f"{lv}环×{n}" for lv, n in ch.spell_slots().items()) or "无（1级游侠）")
            pc_agents[ch.name] = make_pc_agent(
                comp, ch.level, ch.weapons, slots_hint, cfg.model)
        session = GameSession(state, ui, loop=loop, dm_agent=dm_agent,
                              pc_agents=pc_agents, module=module)
    session.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n（冒险暂停——随时回来继续。）")
        sys.exit(130)
