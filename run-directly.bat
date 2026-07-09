@echo off
chcp 65001 >nul
title LlamaCppLauncher - Direct Run
color 0B

echo ============================================
echo   LlamaCppLauncher v3.0 - Direct Run Mode
echo ============================================
echo.

:: Auto-detect project folder (handle nested folder from ZIP extraction)
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%"

if not exist "%PROJECT_DIR%main.py" (
    if exist "%PROJECT_DIR%llama-launcher\main.py" (
        set "PROJECT_DIR=%PROJECT_DIR%llama-launcher\"
        echo [i] Found subfolder: llama-launcher\
    )
)

if not exist "%PROJECT_DIR%main.py" (
    if exist "%PROJECT_DIR%llama-launcher\llama-launcher\main.py" (
        set "PROJECT_DIR=%PROJECT_DIR%llama-launcher\llama-launcher\"
        echo [i] Found nested folder: llama-launcher\llama-launcher\
    )
)

if not exist "%PROJECT_DIR%main.py" (
    echo [X] Cannot find main.py!
    echo     Please make sure this .bat is inside the llama-launcher folder.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"

python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python not found!
    echo.
    echo Please install Python 3.10+ first:
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [1/2] Checking dependencies...
pip install -q ttkbootstrap psutil pywin32 wmi 2>nul
echo [OK] Dependencies ready
echo.

echo [2/2] Starting LlamaCppLauncher...
echo     Working dir: %CD%
echo     Press Ctrl+C to close
echo.
python main.py

if errorlevel 1 (
    echo.
    echo [X] Program exited with error
echo     Exit code: %errorlevel%
    pause
)
