@echo off
chcp 65001 >nul
title PartsPilot 汽配工作台
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [!] 没找到 Python，请先安装 Python 3.10 以上版本（安装时勾选 Add to PATH）
    echo     下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo 正在检查依赖（第一次会稍慢）...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check

echo 正在启动，浏览器马上自动打开...
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8704"
python run.py
pause
