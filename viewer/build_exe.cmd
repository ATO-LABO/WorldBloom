@echo off
setlocal
set PY=python
set OUT=C:\Projects\WorldBloom-local
for %%I in ("%~dp0..") do set ROOT=%%~fI
if not exist "%OUT%\build" mkdir "%OUT%\build"
<nul set /p ="%ROOT%" > "%OUT%\build\default_root.txt"

rem Two exes from the same app_desktop.py -- which mode a build runs in is
rem decided by its own filename (see _studio_mode() in app_desktop.py), so
rem WorldBloom-Studio.exe must keep "studio" in its --name.

"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name WorldBloom ^
  --collect-all webview --collect-all yaml --icon "%ROOT%\viewer\worldbloom.ico" ^
  --add-data "%OUT%\build\default_root.txt;." ^
  --distpath "%OUT%\dist" --workpath "%OUT%\build" --specpath "%OUT%\build" ^
  "%ROOT%\viewer\app_desktop.py"
if errorlevel 1 (
  echo WorldBloom.exe build failed, skipping WorldBloom-Studio.exe
  exit /b 1
)

"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name WorldBloom-Studio ^
  --collect-all webview --collect-all yaml --icon "%ROOT%\viewer\worldbloom.ico" ^
  --add-data "%OUT%\build\default_root.txt;." ^
  --distpath "%OUT%\dist" --workpath "%OUT%\build" --specpath "%OUT%\build" ^
  "%ROOT%\viewer\app_desktop.py"
if errorlevel 1 (
  echo WorldBloom-Studio.exe build failed
  exit /b 1
)
endlocal
