@echo off
setlocal
set "PY=python"
if defined WORLDBLOOM_BUILD_PYTHON set "PY=%WORLDBLOOM_BUILD_PYTHON%"
if "%~1"=="" (
  echo Usage: build_exe.cmd ^<output-directory^>
  exit /b 2
)
for %%I in ("%~1") do set "OUT=%%~fI"
for %%I in ("%~dp0..") do set ROOT=%%~fI
if not exist "%OUT%\build" mkdir "%OUT%\build"
rem Portable builds resolve the app folder next to the exe. Never bake a
rem developer checkout path into a distributable executable.

rem Two exes from the same app_desktop.py -- which mode a build runs in is
rem decided by its own filename (see _studio_mode() in app_desktop.py), so
rem WorldBloom-Studio.exe must keep "studio" in its --name.

"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name WorldBloom ^
  --collect-all webview --collect-all yaml --icon "%ROOT%\viewer\worldbloom.ico" ^
  --distpath "%OUT%\dist" --workpath "%OUT%\build" --specpath "%OUT%\build" ^
  "%ROOT%\viewer\app_desktop.py"
if errorlevel 1 (
  echo WorldBloom.exe build failed, skipping WorldBloom-Studio.exe
  exit /b 1
)

"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name WorldBloom-Studio ^
  --collect-all webview --collect-all yaml --icon "%ROOT%\viewer\worldbloom.ico" ^
  --distpath "%OUT%\dist" --workpath "%OUT%\build" --specpath "%OUT%\build" ^
  "%ROOT%\viewer\app_desktop.py"
if errorlevel 1 (
  echo WorldBloom-Studio.exe build failed
  exit /b 1
)
endlocal
