@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
python -B tools\local_launcher.py --interactive
if errorlevel 1 pause
