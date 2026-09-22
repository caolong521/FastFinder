@echo off
cd /d "%~dp0"

echo FastFinder debug launcher
echo --------------------------
python "%~dp0main.py"

echo.
if errorlevel 1 (
    echo FastFinder exited with an error. Keep this window open and copy the error message.
) else (
    echo FastFinder exited normally.
)
pause
