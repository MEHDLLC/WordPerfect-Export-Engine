@echo off
rem Double-click this to open the interview in a browser.
rem It runs on this computer only; nothing is sent anywhere.
cd /d "%~dp0"

py -3 -m wpx serve --db firmconvert.db --templates Forms --out Letters %*
if errorlevel 9009 (
  rem No Python launcher on this machine: try python directly.
  python -m wpx serve --db firmconvert.db --templates Forms --out Letters %*
)

echo.
echo Letter Desk has stopped. You can close this window.
pause
