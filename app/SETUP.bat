@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title M.F. Case Number OCR - Full Installer

echo ============================================================
echo M.F. CASE NUMBER OCR - FULL INSTALLER
echo Python 3.11 + OCR libraries + verified application V5.3.8
echo ============================================================
echo.
echo This setup installs and verifies Python 3.11 directly.
echo It does NOT rely on the Windows "py" launcher.
echo Internet access is required.
echo Your existing PDF folders and learning data are not deleted.
echo.

set "APP_HOME=%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
set "PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe"

if exist "%PYTHON_EXE%" goto python_found

echo Python 3.11 was not found at the expected installation path.
echo Downloading the official Python 3.11.9 installer...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%PY_INSTALLER%'"
if errorlevel 1 goto python_download_failed
if not exist "%PY_INSTALLER%" goto python_download_failed

for %%F in ("%PY_INSTALLER%") do if %%~zF LSS 5000000 goto python_download_failed

echo.
echo Installing Python 3.11.9 into:
echo %LOCALAPPDATA%\Programs\Python\Python311
echo.
"%PY_INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%LOCALAPPDATA%\Programs\Python\Python311" PrependPath=0 Include_pip=1 Include_launcher=0 Include_tcltk=1 Include_test=0 Shortcuts=0
if errorlevel 1 goto python_install_failed

:python_found
if not exist "%PYTHON_EXE%" goto python_install_failed

echo.
echo Verifying the actual Python executable...
"%PYTHON_EXE%" --version
if errorlevel 1 goto python_install_failed

"%PYTHON_EXE%" -c "import sys; assert sys.version_info[:2] == (3,11), sys.version; print('Verified Python 3.11:',sys.executable)"
if errorlevel 1 goto python_install_failed

echo.
echo Verifying bundled V5.3.8 application files...
"%PYTHON_EXE%" "%~dp0verify_package.py"
if errorlevel 1 goto release_failed

echo.
echo Repairing pip...
"%PYTHON_EXE%" -m ensurepip --upgrade
if errorlevel 1 goto pip_failed

"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto pip_failed

echo.
echo Installing CPU PyTorch and torchvision...
"%PYTHON_EXE%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto dependency_failed

echo.
echo Installing PDF, OCR, image, and handwriting libraries...
"%PYTHON_EXE%" -m pip install pymupdf easyocr pillow numpy transformers sentencepiece
if errorlevel 1 goto dependency_failed

echo.
echo.
echo Exact V5.3.8 application files are included in this package.
echo No older application fallback is included.
echo.
echo.
echo Verifying required Python libraries...
"%PYTHON_EXE%" -c "import torch, torchvision, transformers, easyocr, fitz, numpy, PIL; print('PyTorch:',torch.__version__); print('Transformers:',transformers.__version__); print('All required imports passed.')"
if errorlevel 1 goto dependency_failed

echo.
echo Creating or repairing the desktop shortcut...
if exist "%~dp0CREATE_DESKTOP_SHORTCUT.bat" call "%~dp0CREATE_DESKTOP_SHORTCUT.bat"
if errorlevel 1 echo WARNING: Shortcut creation failed. You can still use RUN_CASE_OCR.bat.

echo.
echo ============================================================
echo INSTALLATION COMPLETE - M.F. CASE NUMBER OCR V5.3.8
echo ============================================================
echo Python 3.11 is verified and the application update is installed.
echo Existing local OCR models are reused; restore missing model files before starting.
echo After models are prepared, normal OCR works without internet.
echo Use the same PDF root folder as before to retain saved review state.
echo.
choice /c YN /m "Start M.F. Case Number OCR now"
if errorlevel 2 exit /b 0
call "%~dp0RUN_CASE_OCR.bat"
exit /b 0

:release_failed
echo.
echo ERROR: Bundled V5.3.8 files failed integrity verification.
echo No older application version will be launched.
echo Extract a fresh copy of this ZIP and run SETUP.bat again.
goto failed

:python_download_failed
echo.
echo ERROR: The official Python installer could not be downloaded or is incomplete.
goto failed

:python_install_failed
echo.
echo ERROR: Python 3.11 installation or verification failed.
echo No libraries were installed and the program was not launched.
echo Check whether Windows security software blocked python.org.
goto failed

:pip_failed
echo.
echo ERROR: Python was installed, but pip could not be repaired.
goto failed

:dependency_failed
echo.
echo ERROR: A required Python library could not be installed or imported.
goto failed


:failed
echo.
echo Setup stopped. No further installation steps will run.
pause
exit /b 1
