@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0Create_Desktop_Shortcut.vbs" (
  echo ERROR: Shortcut creator is missing.
  pause
  exit /b 1
)
cscript //nologo "%~dp0Create_Desktop_Shortcut.vbs"
if errorlevel 1 (
  echo.
  echo Windows could not create the desktop shortcut.
  echo You can still start the program by double-clicking RUN_CASE_OCR.bat.
  pause
  exit /b 1
)
echo.
echo Desktop shortcut created or repaired: M.F. Case Number OCR
timeout /t 2 >nul
