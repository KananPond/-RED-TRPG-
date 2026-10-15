"""构建发行包：TRPG-<版本>.zip（源码 + 启动器 + 文档，纯标准库开箱即玩）。

用法：python build_release.py    # 产物输出到 dist/
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from trpg import __version__

ROOT = Path(__file__).parent
DIST = ROOT / "dist"

# 随包附带的内容（其余为开发/测试用文件）
INCLUDE_DIRS = ["trpg", "modules"]
INCLUDE_FILES = ["main.py", "README.md", "LICENSE", "MODULE_SPEC.md",
                 "HARNESS.md", "CHANGELOG.md", "启动游戏.bat", "启动游戏.sh",
                 ".env.example"]
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".mimosa"}
EXCLUDE_SUFFIX = {".pyc"}


def _keep(p: Path) -> bool:
    return not (EXCLUDE_PARTS & set(p.parts) or p.suffix in EXCLUDE_SUFFIX)


def build() -> Path:
    DIST.mkdir(exist_ok=True)
    name = f"TRPG-{__version__}"
    target = DIST / f"{name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in INCLUDE_DIRS:
            for f in sorted((ROOT / d).rglob("*")):
                if f.is_file() and _keep(f):
                    z.write(f, f"{name}/{f.relative_to(ROOT)}")
        for f in INCLUDE_FILES:
            p = ROOT / f
            if p.exists():
                z.write(p, f"{name}/{f}")
        saves_dir = ROOT / "saves"
        if not saves_dir.exists():
            z.writestr(f"{name}/saves/.keep", "")
    size_kb = target.stat().st_size // 1024
    print(f"✦ 构建完成：{target}（{size_kb} KB）")
    return target


if __name__ == "__main__":
    build()
