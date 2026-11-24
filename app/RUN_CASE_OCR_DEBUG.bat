@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%~dp0pythonw_path.txt" for /f "usebackq delims=" %%P in ("%~dp0pythonw_path.txt") do set "PYTHON_EXE=%%P"
set "PYTHON_EXE=%PYTHON_EXE:pythonw.exe=python.exe%"
if not exist "%PYTHON_EXE%" (
  for /f "delims=" %%P in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
)
if not exist "%PYTHON_EXE%" (
  echo Python 3.11 not found. Run SETUP.bat.
  pause
  exit /b 1
)
"%PYTHON_EXE%" "%~dp0case_number_ocr.py"
echo MFCase stopped. Exit code: %errorlevel%
pause
