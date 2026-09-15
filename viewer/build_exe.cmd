@echo off
setlocal
set PY=python
set OUT=C:\Projects\WorldBloom-local
for %%I in ("%~dp0..") do set ROOT=%%~fI
if not exist "%OUT%\build" mkdir "%OUT%\build"
<nul set /p ="%ROOT%" > "%OUT%\build\default_root.txt"
"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name WorldBloom ^
  --collect-all webview --collect-all yaml ^
  --add-data "%OUT%\build\default_root.txt;." ^
  --distpath "%OUT%\dist" --workpath "%OUT%\build" --specpath "%OUT%\build" ^
  "%ROOT%\viewer\app_desktop.py"
endlocal
