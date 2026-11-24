@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "%~dp0Launch_Case_OCR.vbs" (
  echo MFCase launcher missing. Extract a complete package.
  exit /b 2
)
"%SystemRoot%\System32\wscript.exe" "%~dp0Launch_Case_OCR.vbs"
exit /b %errorlevel%
