@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title M.F. Case Number OCR - Setup V5.5.5
set "PYTHON_EXE="
set "APP_HOME=%~dp0"
echo M.F. CASE NUMBER OCR V5.5.5 - INSTALL / REPAIR
echo.
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYTHON_EXE (
  for /f "delims=" %%P in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
)
if not defined PYTHON_EXE (
  for /f "delims=" %%P in ('python -c "import sys;print(sys.executable if sys.version_info[:2]==(3,11) else '')" 2^>nul') do set "PYTHON_EXE=%%P"
)
if not defined PYTHON_EXE goto install_python
if not exist "!PYTHON_EXE!" goto install_python
"!PYTHON_EXE!" -c "import sys;assert sys.version_info[:2]==(3,11)"
if errorlevel 1 goto install_python
goto python_ok
:install_python
echo Python 3.11 not found; downloading official CPython installer...
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
set "PY_INSTALLER=%TEMP%\MFCase_python-3.11.9-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\MFCase_python-3.11.9-amd64.exe'"
if errorlevel 1 goto failed
if not exist "!PY_INSTALLER!" goto failed
"!PY_INSTALLER!" /quiet InstallAllUsers=0 TargetDir="%LOCALAPPDATA%\Programs\Python\Python311" PrependPath=0 Include_pip=1 Include_launcher=1 Include_tcltk=1 Include_test=0 Shortcuts=0
if errorlevel 1 goto failed
:python_ok
if not exist "!PYTHON_EXE!" goto failed
"!PYTHON_EXE!" -c "import sys; assert sys.version_info[:2] == (3,11),sys.version;print('Verified Python:',sys.executable)"
if errorlevel 1 goto failed
"!PYTHON_EXE!" "%~dp0verify_package.py"
if errorlevel 1 goto failed
"!PYTHON_EXE!" -c "import pathlib,sys;pathlib.Path('pythonw_path.txt').write_text(str(pathlib.Path(sys.executable).with_name('pythonw.exe')),encoding='utf-8')"
if errorlevel 1 goto failed
if not exist "%~dp0pythonw_path.txt" goto failed
REM Create the actual shortcut BEFORE dependency installation; it must not disappear if pip fails.
call "%~dp0CREATE_DESKTOP_SHORTCUT.bat"
if errorlevel 1 echo WARNING: Shortcut creation failed. Try Create_Desktop_Shortcut.vbs directly.
echo.
echo Checking required OCR dependencies...
"!PYTHON_EXE!" -c "import torch,torchvision,transformers,easyocr,pymupdf,numpy,PIL; print('OCR dependencies already installed')" >nul 2>nul
if not errorlevel 1 goto installed
echo Installing missing OCR libraries. This can take several minutes on first install.
"!PYTHON_EXE!" -m ensurepip --upgrade
if errorlevel 1 goto failed
"!PYTHON_EXE!" -m pip install --upgrade pip
if errorlevel 1 goto failed
"!PYTHON_EXE!" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto failed
"!PYTHON_EXE!" -m pip install "pymupdf<2.0" easyocr pillow numpy transformers sentencepiece
if errorlevel 1 goto failed
:installed
"!PYTHON_EXE!" -c "import torch,torchvision,transformers,easyocr,pymupdf,numpy,PIL; print('Dependencies OK')"
if errorlevel 1 goto failed
call "%~dp0CREATE_DESKTOP_SHORTCUT.bat"
echo.
echo Installation complete. Launching MFCase without a CMD window.
start "" "%SystemRoot%\System32\wscript.exe" "%~dp0Launch_Case_OCR.vbs"
exit /b 0
:failed
echo.
echo Installation stopped with an error; the application has not been started.
echo Previously saved case files, settings and training remain untouched.
echo If a shortcut exists, do not use it until dependencies are installed.
pause
exit /b 1
