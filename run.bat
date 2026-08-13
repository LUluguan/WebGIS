@echo off
REM ============================================
REM  广东降雨洪涝 WebGIS - 一键启动
REM  依赖: 首次使用请先运行 setup.bat
REM ============================================
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo [提示] 未找到虚拟环境, 请先运行 setup.bat
  pause & exit /b 1
)
call .venv\Scripts\activate.bat
if "%FLOOD_PORT%"=="" set FLOOD_PORT=8001
echo 启动服务 http://127.0.0.1:%FLOOD_PORT%/   (Ctrl+C 停止)
start "" "http://127.0.0.1:%FLOOD_PORT%/"
python -m uvicorn app:app --host 127.0.0.1 --port %FLOOD_PORT%
pause
