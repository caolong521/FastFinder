@echo off
cd /d "%~dp0"

rem Normal desktop launch: prefer pythonw so FastFinder does not leave a black console window open.
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "%~dp0main.py"
    exit /b 0
)

rem Fallback when pythonw is not available.
python "%~dp0main.py"
if errorlevel 1 pause
