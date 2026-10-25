@echo off
cd /d "%~dp0"
echo ========================================
echo   自动好运星盘 - 启动器
echo ========================================
echo.

REM ---- 锁定解释器：优先用本机已确认可用的 Python 3.14，避免 PATH 里其它 python 抢跑 ----
set "PY=python"
if exist "D:\Env\Python\Python314\python.exe" set "PY=D:\Env\Python\Python314\python.exe"

REM ---- 环境自检：依赖缺失时给出明确提示，而不是闪退 ----
"%PY%" -c "import sys; assert sys.version_info >= (3,10), '需要 Python 3.10 及以上'" 2>nul
if errorlevel 1 (
    echo [错误] 未找到可用的 Python，或版本低于 3.10。
    echo        请安装 Python 3.10+ 并勾选 "tcl/tk" 组件。
    echo.
    pause
    exit /b 1
)

"%PY%" -c "import tkinter" 2>nul
if errorlevel 1 (
    echo [错误] 当前 Python 缺少 tkinter（GUI 组件）。
    echo        请重新运行 Python 安装程序，勾选 "tcl/tk and IDLE"。
    echo.
    echo 当前解释器位置:
    "%PY%" -c "import sys; print('  ' + sys.executable)"
    echo.
    pause
    exit /b 1
)

"%PY%" -c "import cv2, numpy, mss" 2>nul
if errorlevel 1 (
    echo [提示] 依赖不完整，正在安装...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络或 pip 源。
        pause
        exit /b 1
    )
)

REM ---- 游戏以管理员运行时，本程序必须同样提权，否则 UIPI 会拦截点击 ----
net session >nul 2>&1
if errorlevel 1 (
    echo [权限] 当前不是管理员，正在请求提权...
    echo        游戏一般以管理员运行，不提权会导致「点了没反应」。
    echo.
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile ^
        -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    if errorlevel 1 (
        echo [错误] 提权被取消或失败。
        echo        请手动右键本文件，选择「以管理员身份运行」。
        pause
    )
    exit /b
)

echo 启动中...
"%PY%" gui.py
if errorlevel 1 (
    echo.
    echo [错误] 程序异常退出，上方为错误信息。
    pause
)
