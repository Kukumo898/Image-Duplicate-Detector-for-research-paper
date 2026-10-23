@echo off
chcp 65001 >nul
cd /d "%~dp0"
python --version
if errorlevel 1 (
    echo 请先安装 Python 3.12 或更高版本并勾选加入 PATH。
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto failure
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto failure
".venv\Scripts\python.exe" scripts\create_demo.py
if errorlevel 1 goto failure
echo 安装成功。请双击「启动系统.bat」。
pause
exit /b 0
:failure
echo 安装未完成，请保留上方错误信息。首次安装需要联网下载依赖。
pause
exit /b 1

