#!/usr/bin/env sh
# CTRCG 单人 AI 跑团 —— macOS / Linux 启动器
# 服务运行期间请保持本窗口开启；Ctrl+C 停止服务。
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3.10+。"
  exit 1
fi
python3 main.py --web
