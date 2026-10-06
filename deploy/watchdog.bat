@echo off
REM 演示守护: 服务退出后 3 秒自动重启(Ctrl+C 停止本脚本即停止守护)
REM 首次使用先 setup.bat; 端口被占用时先检查 8001
cd /d "%~dp0"
set PYTHONPATH=D:\Lib\site-packages
:loop
echo [%date% %time%] uvicorn 启动 (http://127.0.0.1:8001/)
python -m uvicorn app:app --host 127.0.0.1 --port 8001
echo [%date% %time%] 服务退出, 3 秒后自动重启...
timeout /t 3 /nobreak >nul
goto loop
