@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title M.F. Case Number OCR
set "APP_SCRIPT=%~dp0case_number_ocr.py"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%APP_SCRIPT%" (
 echo ERROR: case_number_ocr.py was not found in:
 echo %~dp0
 pause
 exit /b 1
)
if exist "%PYTHON_EXE%" goto run_app
where py >nul 2>nul
if not errorlevel 1 (
 py -3.11 --version >nul 2>nul
 if not errorlevel 1 (
  py -3.11 "%APP_SCRIPT%"
  goto finished
 )
)
echo ERROR: Python 3.11 was not found. Run SETUP.bat first.
pause
exit /b 1
:run_app
"%PYTHON_EXE%" "%APP_SCRIPT%"
:finished
if errorlevel 1 (
 echo.
 echo M.F. Case Number OCR could not start. Check Python 3.11 and required OCR packages.
 pause
 exit /b 1
)
exit /b 0
