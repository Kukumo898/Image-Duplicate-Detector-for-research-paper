@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在停止本项目的本地服务，进行中的检测将中断；已存数据保留。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_server.ps1"
pause

