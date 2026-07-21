@echo off
REM Saitenka Windows installer launcher. Chocolatey-first (needs admin); winget as fallback.
REM   Double-click, or right-click -> "Run as administrator".
REM   Pass flags through, e.g.:   install-windows.cmd -Dev      install-windows.cmd -DryRun
setlocal
net session >nul 2>&1
if errorlevel 1 (
  echo [saitenka][warn] Not elevated - Chocolatey needs admin. For the full install,
  echo                  close this and RIGHT-CLICK install-windows.cmd -^> "Run as administrator".
  echo.
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
echo.
echo [saitenka] Finished. Press any key to close.
pause >nul
