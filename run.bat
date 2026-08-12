@echo off
REM 广东降雨洪涝 WebGIS 一键启动
REM 首次运行请先: pip install -r requirements.txt  (torch 另用 pytorch cpu 源)
chcp 65001 >nul
cd /d "%~dp0"
echo 启动服务 http://127.0.0.1:8001/  (Ctrl+C 停止)
python -m uvicorn app:app --host 127.0.0.1 --port 8001
pause
