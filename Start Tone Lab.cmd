@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Tone Lab needs its local Python environment. See README.md.
    pause
    exit /b 1
)
start "Tone Lab" ".venv\Scripts\pythonw.exe" -m tone_lab clip
