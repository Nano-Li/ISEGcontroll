@echo off
echo ========================================
echo ISEG THQ 高压电源控制器 — 打包脚本
echo ========================================
echo.

REM 检查 Python 是否可用
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 未安装或未添加到 PATH
    pause
    exit /b 1
)

REM 安装依赖
echo [1/3] 安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] 依赖安装失败
    pause
    exit /b 1
)

REM 清理旧的构建
echo [2/3] 清理旧构建...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM 打包
echo [3/3] 打包为单一可执行文件...
pyinstaller --onefile --windowed --noconsole ^
    --name "ISEG_THQ_Controller" ^
    --add-data "protocol;protocol" ^
    --add-data "serial_comm;serial_comm" ^
    --add-data "core;core" ^
    --add-data "ui;ui" ^
    --add-data "data;data" ^
    --hidden-import serial ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import pyqtgraph ^
    --hidden-import numpy ^
    --hidden-import openpyxl ^
    main.py

if errorlevel 1 (
    echo [ERROR] 打包失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo 打包完成! 可执行文件位置:
echo   dist\ISEG_THQ_Controller.exe
echo ========================================
pause
