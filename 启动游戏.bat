@echo off
rem TRPG 单人 AI 跑团 —— 双击启动网页版
rem 服务运行期间请保持本窗口开启；关闭窗口即停止服务。
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 python，请先安装 Python 3.10+ 并勾选 "Add to PATH"。
  pause
  exit /b 1
)
python main.py --web
echo.
echo 服务已退出。若是报错退出，请截屏上方信息。
pause
