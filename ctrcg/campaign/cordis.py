"""Cordis 式模组注册表 —— 按检索到的 cordiverse/cordis 语义实现的 Python 变体。

参考（检索于 2026-09）：
- deepseek-harness 官方教程 docs/cordis-tutorial：插件即函数、由加载器挂载；
  插件卸载时其登记的副作用自动撤销；配置校验"坏输入大声失败"。
- Understanding Cordis（dev.to）：每次挂载在 Context 树的子分支中进行；
  Fiber 三态 PENDING（等待缺失的依赖服务）→ ACTIVE（副作用运行中）→
  DISPOSED（清理栈执行完毕）；"每个副作用默认可逆"（cleanup 栈按注册
  相反顺序回滚）；依赖的服务消失时，依赖方自动被处置而非崩溃。
- dshbox/cordis-rs：context-based plugin framework，显式依赖注入与
  生命周期托管清理。

本实现的对应物（单进程游戏的最小子集）：
- Context：共享上下文 = 服务注册表（provide/require）+ 广播事件总线；
- Fiber：每个模组一个运行态管理器，状态机与清理栈同上；
- 模组作为插件激活时，把 Module 实例注册为服务 "module/<id>"，
  disposer 负责在卸载时撤销注册（LIFO 回滚）；
- module.json 的可选字段 requires: [其他模组id] 声明依赖：
  依赖未就绪 → 该模组停在 PENDING；依赖被安装 → 自动激活（PENDING→ACTIVE）；
  依赖被卸载 → 依赖方自动处置（连坐卸载）。
- mtime 热重载即 Cordis 的 hot-swap：校验通过才替换，失败保留旧实例。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .loader import Module

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")

# 内置模组 id（随发行版附带，禁止经 UI 删除）
BUILTIN_IDS = {"starfall", "saltmist", "mistgate"}

PENDING = "pending"      # 已挂载，等待依赖服务
ACTIVE = "active"        # 副作用运行中
DISPOSED = "disposed"    # 清理栈已回滚完毕


class Context:
    """共享上下文：服务注册表 + 广播事件总线（对齐 Cordis 的 Context）。"""

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._listeners: list = []          # fn(kind, payload)

    # ---- 服务（对齐 provide / require）
    def provide(self, key: str, value: object) -> None:
        self._services[key] = value

    def require(self, key: str) -> object | None:
        return self._services.get(key)

    # ---- 事件（广播分发）
    def on(self, fn) -> None:
        self._listeners.append(fn)

    def emit(self, kind: str, payload: object) -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, payload)
            except Exception:  # noqa: BLE001 —— 监听器不得阻塞上下文
                pass


class Fiber:
    """单个插件（模组）的运行态：PENDING → ACTIVE → DISPOSED。

    所有经 on_dispose() 登记的清理回调在卸载时按注册的相反顺序执行
    （Cordis："every side effect must be reversible by default"）。
    """

    def __init__(self, ctx: Context, module_id: str):
        self.ctx = ctx
        self.id = module_id
        self.state = PENDING
        self.module: Module | None = None
        self.requires: list[str] = []
        self._cleanups: list = []

    def on_dispose(self, fn) -> None:
        self._cleanups.append(fn)

    def dispose(self) -> None:
        if self.state == DISPOSED:
            return
        self.state = DISPOSED
        for fn in reversed(self._cleanups):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        self._cleanups.clear()
        self.module = None


class Cordis:
    """模组加载器：把 modules/ 目录下的原子模组作为插件挂载到 Context。"""

    def __init__(self, root: str | Path = "modules"):
        self.root = Path(root)
        self.ctx = Context()
        self._fibers: dict[str, Fiber] = {}

    # ------------------------------------------------------------ 事件
    def on(self, fn) -> None:
        self.ctx.on(fn)

    def _emit(self, kind: str, module_id: str) -> None:
        self.ctx.emit(kind, module_id)

    # ------------------------------------------------------------ 发现
    def scan(self) -> list[dict]:
        """扫描 modules/ 下的全部模组目录（每个目录一个 module.json）。"""
        found: list[dict] = []
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or not _ID_RE.match(d.name):
                continue
            manifest = d / "module.json"
            if not manifest.exists():
                continue
            source = "builtin" if d.name in BUILTIN_IDS else \
                ("generated" if d.name.startswith("generated-") else "imported")
            found.append({"id": d.name, "dir": str(d), "source": source,
                          "mtime": manifest.stat().st_mtime})
        return found

    # ------------------------------------------------------------ 挂载
    def _build(self, module_id: str) -> tuple[Module | None, list[str], list[str]]:
        if not _ID_RE.match(module_id):
            return None, ["非法的模组 id"], []
        manifest = self.root / module_id / "module.json"
        if not manifest.exists():
            return None, [f"模组不存在：{module_id}"], []
        source = "builtin" if module_id in BUILTIN_IDS else \
            ("generated" if module_id.startswith("generated-") else "imported")
        mod, errors, warnings = Module.from_file(manifest)
        if mod is None:
            return None, errors, warnings
        if mod.id != module_id:
            errors = errors + [f"模组内部 id（{mod.id}）与目录名（{module_id}）不一致"]
            return None, errors, warnings
        mod.source = source
        return mod, [], warnings

    def _ready(self, module_id: str) -> bool:
        f = self._fibers.get(module_id)
        return f is not None and f.state == ACTIVE

    def _activate(self, fiber: Fiber, mod: Module, _promoting: set | None = None) -> None:
        """激活一个就绪的插件：注册副作用并登记 disposer（LIFO 回滚）。"""
        service_key = f"module/{fiber.id}"
        fiber.ctx.provide(service_key, mod)
        fiber.on_dispose(lambda k=service_key: self.ctx._services.pop(k, None))
        fiber.module = mod
        fiber.state = ACTIVE
        self._emit("loaded", fiber.id)
        # 依赖就绪 → 等待中的插件自动激活（Cordis：PENDING → ACTIVE）
        promoting = _promoting if _promoting is not None else {fiber.id}
        for fid, f in list(self._fibers.items()):
            if f.state == PENDING and fid not in promoting \
                    and all(self._ready(r) for r in f.requires):
                m2, _errs, _w = self._build(fid)
                if m2 is None:
                    continue
                promoting.add(fid)
                self._activate(f, m2, promoting)

    def load(self, module_id: str) -> tuple[Module | None, list[str], list[str]]:
        """载入并挂载一个模组。依赖未就绪时停在 PENDING（大声失败）。"""
        mod, errors, warnings = self._build(module_id)
        if mod is None:
            return None, errors, warnings
        if module_id in self._fibers:          # 重复载入：先卸旧实例（热替换）
            self._discard(module_id)
        fiber = Fiber(self.ctx, module_id)
        fiber.requires = list(mod.requires)
        self._fibers[module_id] = fiber
        missing = [r for r in fiber.requires if not self._ready(r)]
        if missing:
            fiber.state = PENDING
            return None, [f"模组 {module_id} 等待依赖（PENDING）：{'、'.join(missing)}"], warnings
        self._activate(fiber, mod)
        return mod, [], warnings

    def ensure(self, module_id: str) -> tuple[Module | None, list[str], list[str]]:
        """优先返回已激活实例；文件有更新时 hot-swap（校验通过才替换）。"""
        manifest = self.root / module_id / "module.json"
        fiber = self._fibers.get(module_id)
        if fiber is not None and fiber.state == ACTIVE and manifest.exists():
            if manifest.stat().st_mtime <= getattr(fiber.module, "_loaded_at", 0):
                return fiber.module, [], fiber.module.warnings
            mod, errors, warnings = self._build(module_id)
            if mod is None:                    # 新版本非法：保留旧实例
                return fiber.module, errors, warnings
            missing = [r for r in mod.requires if not self._ready(r)]
            if missing:
                return fiber.module, [f"热重载被拒（依赖未就绪）：{'、'.join(missing)}"], warnings
            self._discard(module_id)           # hot-swap：仅替换本插件，不动依赖方
        mod, errors, warnings = self.load(module_id)
        if mod is not None:
            mod._loaded_at = manifest.stat().st_mtime  # noqa: SLF001
        return mod, errors, warnings

    def _discard(self, module_id: str) -> None:
        """仅处置本插件（不清依赖方），供热替换与内部清理使用。"""
        fiber = self._fibers.pop(module_id, None)
        if fiber is not None:
            fiber.dispose()

    def unload(self, module_id: str) -> tuple[bool, str | None]:
        """卸载一个模组。服务消失 → 依赖它的插件被自动处置（连坐）。"""
        if module_id == "starfall":
            return False, "内置模组不可卸载"
        fiber = self._fibers.get(module_id)
        if fiber is None:
            return True, None
        for fid, f in list(self._fibers.items()):
            if f.state == ACTIVE and module_id in f.requires:
                self.unload(fid)
        for fid, f in list(self._fibers.items()):
            if f.state == PENDING and module_id in f.requires:
                self._discard(fid)
        fiber = self._fibers.get(module_id)
        if fiber is not None:
            fiber.dispose()          # 保留 DISPOSED 状态可观测（Cordis Fiber 语义）
        self._emit("unloaded", module_id)
        return True, None

    def delete(self, module_id: str) -> tuple[bool, str | None]:
        if module_id in BUILTIN_IDS:
            return False, "内置模组不可删除"
        ok, err = self.unload(module_id)
        if err:
            return ok, err
        d = self.root / module_id
        if d.exists():
            shutil.rmtree(d)
        self._emit("deleted", module_id)
        return True, None

    # ------------------------------------------------------------ 安装
    def install(self, data: dict, source: str = "imported",
                module_id: str | None = None) -> tuple[Module | None, list[str], list[str]]:
        """安装一个模组 JSON（人工导入 / AI 生成），校验通过即落盘并挂载。"""
        from .loader import validate_module
        errors, warnings = validate_module(data)
        if errors:
            return None, errors, warnings
        mid = module_id or str(data["id"])
        if not _ID_RE.match(mid):
            return None, [f"模组 id 非法：{mid}"], []
        if source == "generated":
            mid = f"generated-{mid}" if not mid.startswith("generated-") else mid
        elif source == "imported" and mid == "starfall":
            return None, ["不能覆盖内置模组，请改用其他 id"], []
        data["id"] = mid
        d = self.root / mid
        d.mkdir(parents=True, exist_ok=True)
        (d / "module.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        mod, errors, warnings = self.load(mid)
        if mod is None:
            return None, errors, warnings
        mod.source = source
        mod.warnings = warnings
        self._emit("installed", mid)
        return mod, [], warnings

    # ------------------------------------------------------------ 视图
    def active_ids(self) -> list[str]:
        return [fid for fid, f in self._fibers.items() if f.state == ACTIVE]

    def fiber_states(self) -> dict[str, str]:
        """调试视图：各插件当前的 Fiber 状态（pending/active/disposed）。"""
        return {fid: f.state for fid, f in sorted(self._fibers.items())}


def default_fallback_module():
    """modules/ 目录缺失时的兜底：直接从代码内置数据构造。"""
    from . import module_data as M
    data = {
        "id": "starfall", "title": M.MODULE["title"],
        "en_title": M.MODULE["en_title"], "setting": M.MODULE["setting"],
        "synopsis": M.MODULE["synopsis"],
        "party_level_start": M.MODULE["party_level_start"],
        "expected_end_level": M.MODULE["expected_end_level"],
        "act_titles": {str(k): v for k, v in M.ACT_TITLES.items()},
        "epilogue": M.EPILOGUE, "npcs": M.NPCS, "companions": M.COMPANIONS,
        "encounters": M.ENCOUNTERS, "scenes": M.SCENES,
        "custom_monsters": {},
    }
    return Module(data, source="builtin")
