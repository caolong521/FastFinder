@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo FastFinder - Fast Startup Windows Build
echo Recommended mode: ONEDIR
echo ========================================
echo.

python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo [1/3] Installing PyInstaller...
    python -m pip install -r requirements-build.txt
    if errorlevel 1 goto :failed
) else (
    echo [1/3] PyInstaller already installed.
)

echo [2/3] Cleaning old build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] Building FastFinder ONEDIR package...
python -m PyInstaller --noconfirm --clean FastFinder.spec
if errorlevel 1 goto :failed

echo.
echo ========================================
echo Build complete
echo EXE: %CD%\dist\FastFinder\FastFinder.exe
echo ========================================
echo.
echo NOTE: Publish the whole dist\FastFinder folder, not only FastFinder.exe.
explorer "%CD%\dist\FastFinder"
exit /b 0

:failed
echo.
echo [ERROR] Build failed. Inspect the messages above.
pause
exit /b 1
