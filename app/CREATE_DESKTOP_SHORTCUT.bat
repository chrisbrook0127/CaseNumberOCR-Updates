@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "%~dp0Create_Desktop_Shortcut.vbs" (
  echo ERROR: Create_Desktop_Shortcut.vbs is missing.
  exit /b 2
)
"%SystemRoot%\System32\cscript.exe" //nologo "%~dp0Create_Desktop_Shortcut.vbs"
exit /b %errorlevel%
