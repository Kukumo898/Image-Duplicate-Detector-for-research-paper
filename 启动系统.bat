@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先双击「安装依赖.bat」。
    pause
    exit /b 1
)
echo 图鉴 · 科研图像核验
echo 本机地址：http://127.0.0.1:8765
echo 关闭此窗口将停止服务；数据保留在 data 文件夹。
".venv\Scripts\python.exe" run.py
pause

