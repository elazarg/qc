@echo off
cd /d "%~dp0"
if exist "Tone Lab.exe" (
    start "" "Tone Lab.exe"
    exit /b
)
if not exist ".venv\Scripts\pythonw.exe" (
    echo Tone Lab needs its local Python environment. See README.md.
    pause
    exit /b 1
)
start "Tone Lab" ".venv\Scripts\pythonw.exe" "desktop_launcher\start.pyw"
