@echo off
chcp 65001 >nul
echo [Comfy Service] 启动 MiniMax H3 ComfyUI 节点 http://127.0.0.1:8189 ...
cd /d "D:\MiniMax-H3\ComfyUI"
.\venv\Scripts\python.exe main.py --port 8189 --listen 127.0.0.1
pause
