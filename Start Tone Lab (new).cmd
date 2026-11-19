@echo off
rem The rewritten desk (lab/). The old one still starts from "Start Tone Lab.cmd".
cd /d "%~dp0"
start "Tone Lab" ".venv\Scripts\pythonw.exe" -m lab
