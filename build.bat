@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   闪光弹模拟 - 自动编译脚本 (--onedir 模式)
echo ============================================
echo.

REM ---------- 检查 Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.x 并添加到 PATH。
    pause
    exit /b 1
)

echo [1/4] 安装依赖库...
pip install pynput pycaw pystray pillow pywin32 pyinstaller
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络或权限。
    pause
    exit /b 1
)

REM ---------- 定位 pywin32 系统 DLL ----------
echo.
echo [2/4] 定位 pywin32 系统 DLL...
set "PYWIN32_DLL="

REM 方法一：直接通过 Python 定位 pywintypes 模块所在目录
for /f "delims=" %%i in ('python -c "import pywintypes, os; print(os.path.dirname(pywintypes.__file__))" 2^>nul') do set "PYWIN32_DLL=%%i"

if defined PYWIN32_DLL (
    if exist "!PYWIN32_DLL!\pywintypes*.dll" (
        echo      通过 pywintypes 模块找到 DLL: !PYWIN32_DLL!
        goto :dll_found
    )
)

REM 方法二：在 site-packages 下搜索常见目录
for /f "delims=" %%i in ('python -c "import site; print(site.getsitepackages()[0])"') do set "SITE_PACKAGES=%%i"

for %%d in ("%SITE_PACKAGES%" "%SITE_PACKAGES%\win32" "%SITE_PACKAGES%\win32\lib" "%SITE_PACKAGES%\pywin32_system32") do (
    if exist "%%~d\pywintypes*.dll" (
        set "PYWIN32_DLL=%%~d"
        echo      在 site-packages 找到 DLL: !PYWIN32_DLL!
        goto :dll_found
    )
)

REM 方法三：Python 安装根目录
for /f "delims=" %%i in ('python -c "import sys; print(sys.prefix)"') do set "PYTHON_PREFIX=%%i"

for %%d in ("%PYTHON_PREFIX%" "%PYTHON_PREFIX%\DLLs" "%PYTHON_PREFIX%\Lib\site-packages\win32") do (
    if exist "%%~d\pywintypes*.dll" (
        set "PYWIN32_DLL=%%~d"
        echo      在 Python 目录找到 DLL: !PYWIN32_DLL!
        goto :dll_found
    )
)

REM 方法四：尝试运行 pywin32_postinstall 修复，然后重新定位
echo [警告] 未找到 pywin32 DLL，尝试运行 pywin32 修复脚本...
python -m pywin32_postinstall -install >nul 2>nul

for /f "delims=" %%i in ('python -c "import pywintypes, os; print(os.path.dirname(pywintypes.__file__))" 2^>nul') do set "PYWIN32_DLL=%%i"
if defined PYWIN32_DLL (
    if exist "!PYWIN32_DLL!\pywintypes*.dll" (
        echo      修复后找到 DLL: !PYWIN32_DLL!
        goto :dll_found
    )
)

echo.
echo [错误] 仍然找不到 pywin32 DLL。
echo         请运行以下命令并把输出发给开发者：
echo           python -c "import pywintypes, os; print(os.path.dirname(pywintypes.__file__))"
echo           dir /s /b "%SITE_PACKAGES%\*pywintypes*.dll"
pause
exit /b 1

:dll_found
echo      DLL 目录内容：
dir /b "!PYWIN32_DLL!\pywintypes*.dll" 2>nul
dir /b "!PYWIN32_DLL!\pythoncom*.dll" 2>nul

REM ---------- 检查音效文件 ----------
echo.
echo [3/4] 检查音效文件...
if not exist "flashlight.wav" (
    echo [错误] 当前目录下未找到 flashlight.wav，请将其放入本目录。
    pause
    exit /b 1
)
echo      音效文件已找到。

REM ---------- 打包 ----------
echo.
echo [4/4] 开始打包...
pyinstaller --onedir --windowed --name FlashLight ^
    --add-binary "!PYWIN32_DLL!\pywintypes*.dll;." ^
    --add-binary "!PYWIN32_DLL!\pythoncom*.dll;." ^
    --add-data "flashlight.wav;." ^
    --hidden-import pythoncom ^
    --hidden-import pywintypes ^
    FlashLight.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请检查上方输出信息。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   编译完成！
echo   生成的文件夹位于: dist\FlashLight\
echo   请将整个 FlashLight 文件夹压缩后分发。
echo ============================================
echo.
pause
endlocal
