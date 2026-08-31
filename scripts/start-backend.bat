@echo off
chcp 65001 >nul
cd /d "%~dp0..\backend"
echo [Comfy Service] 启动后端 http://127.0.0.1:8000 ...
python run.py
pause
