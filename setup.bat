@echo off
REM ============================================
REM  广东降雨洪涝 WebGIS - 一键安装依赖
REM  首次使用: 双击本文件; 之后直接运行 run.bat
REM ============================================
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] 检查 Python ...
where python >nul 2>nul || (echo [错误] 未找到 python, 请先安装 Python 3.10+ 并加入 PATH & pause & exit /b 1)
python -c "import sys; assert sys.version_info >= (3,10), '需 Python 3.10+'; print('    Python', sys.version.split()[0])" || (pause & exit /b 1)
echo [2/4] 创建虚拟环境 .venv ...
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
echo [3/4] 安装 Web/地理依赖 ...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo [4/4] 安装 torch CPU 版 ...
pip install torch --index-url https://download.pytorch.org/whl/cpu
echo.
echo 安装完成! 请运行 run.bat 启动服务。
pause
