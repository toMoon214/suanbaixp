@echo off
setlocal
echo ========================================
echo   300英雄 占卜自动化 - 打包工具
echo ========================================
echo.

REM ---- 环境自检 ----
python -c "import sys; print('Python', sys.version.split()[0])"
python -c "import cv2, numpy, mss, pyautogui, tkinter" 2>nul
if errorlevel 1 (
    echo [错误] 依赖不完整。
    echo        请先执行: python -m pip install -r requirements.txt
    pause
    exit /b 1
)

python -m pip install pyinstaller >nul 2>&1

echo.
echo 正在打包（首次或依赖变更时约需 1-3 分钟）...
echo.

REM 图标可选：存在 icon.ico 才带上，否则用默认图标
set ICON_ARG=
if exist "icon.ico" set ICON_ARG=--icon icon.ico

python -m PyInstaller --onefile --windowed ^
    --name "酸败好运星盘" ^
    --add-data "temp;temp" ^
    --uac-admin %ICON_ARG% ^
    --version-file version_info.py ^
    --hidden-import mss ^
    --hidden-import mss.windows ^
    --noconfirm ^
    --clean ^
    gui.py

echo.
if exist "dist\酸败好运星盘.exe" (
    for %%A in ("dist\酸败好运星盘.exe") do set SIZE=%%~zA
    echo ========================================
    echo   打包成功!
    echo   输出: dist\酸败好运星盘.exe  ^(%SIZE% 字节^)
    echo ========================================
    if %SIZE% LSS 10000000 (
        echo [警告] 文件偏小，可能打包不完整，请检查上方日志
    )
) else (
    echo ========================================
    echo   打包失败，请检查上方错误信息
    echo ========================================
)
pause
