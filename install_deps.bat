@echo off
chcp 65001 >nul
echo ============================================
echo  LlamaCppLauncher v3.0 - 依赖安装
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python, 请先安装 Python 3.10+
    pause
    exit /b 1
)

echo [1/3] 升级 pip...
python -m pip install --upgrade pip

echo [2/3] 安装依赖...
pip install -r requirements.txt

echo [3/3] 安装 PyInstaller (打包用)...
pip install pyinstaller

echo.
echo ============================================
echo  安装完成!
echo  运行: python main.py
echo  打包: python build.py
echo ============================================
pause
