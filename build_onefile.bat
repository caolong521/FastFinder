@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo FastFinder - Optional Single EXE Build
echo WARNING: startup is slower than ONEDIR.
echo ========================================
echo.

python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    python -m pip install -r requirements-build.txt
    if errorlevel 1 goto :failed
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python -m PyInstaller --noconfirm --clean FastFinder_onefile.spec
if errorlevel 1 goto :failed

echo.
echo Build complete: %CD%\dist\FastFinder.exe
explorer "%CD%\dist"
exit /b 0

:failed
echo [ERROR] Build failed.
pause
exit /b 1
