@echo off
chcp 65001 >nul
cd /d "%~dp0..\frontend"
echo [Comfy Service] 启动前端 http://localhost:5273 ...
call npm run dev
pause
