import hashlib,sys,zipfile
from pathlib import Path
base=Path(sys.argv[1]); out=Path(sys.argv[2])
with zipfile.ZipFile(base) as z:
 d={Path(n).name:z.read(n) for n in z.namelist() if not n.endswith('/')}
a=d['case_number_ocr.py'].decode()
assert 'self.app_version = "3.3.4"' in a and '"Launch_Case_OCR.vbs"' in a
d['case_number_ocr.py']=a.replace('V3.3.4','V3.3.5').replace('3.3.4','3.3.5').encode()
d['RUN_CASE_OCR.bat']=b'''@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\ntitle M.F. Case Number OCR V3.3.5\r\nset "APP_SCRIPT=%~dp0case_number_ocr.py"\r\nif not exist "%APP_SCRIPT%" (\r\n echo ERROR: case_number_ocr.py was not found in:\r\n echo %~dp0\r\n echo The application folder may be incomplete.\r\n pause\r\n exit /b 1\r\n)\r\npy -3.11 "%APP_SCRIPT%"\r\nif errorlevel 1 (\r\n echo.\r\n echo M.F. Case Number OCR could not start.\r\n echo Check Python 3.11 and required OCR packages.\r\n pause\r\n exit /b 1\r\n)\r\n'''
d['Launch_Case_OCR.vbs']=b'''Option Explicit\r\nDim shell,fso,folder,bat,desk,linkPath,iconFile,wscriptExe,link\r\nSet shell=CreateObject("WScript.Shell")\r\nSet fso=CreateObject("Scripting.FileSystemObject")\r\nfolder=fso.GetParentFolderName(WScript.ScriptFullName)\r\nbat=fso.BuildPath(folder,"RUN_CASE_OCR.bat")\r\niconFile=fso.BuildPath(folder,"MF.ico")\r\nIf Not fso.FileExists(bat) Then\r\n MsgBox "Program launcher not found:" & vbCrLf & bat,vbCritical,"M.F. Case Number OCR"\r\n WScript.Quit 2\r\nEnd If\r\n' Rebuild the Desktop shortcut from this real install folder.\r\nOn Error Resume Next\r\ndesk=shell.SpecialFolders("Desktop")\r\nlinkPath=fso.BuildPath(desk,"M.F. Case Number OCR.lnk")\r\nwscriptExe=fso.BuildPath(shell.ExpandEnvironmentStrings("%WINDIR%"),"System32\\wscript.exe")\r\nIf fso.FileExists(wscriptExe) Then\r\n Set link=shell.CreateShortcut(linkPath)\r\n link.TargetPath=wscriptExe\r\n link.Arguments=Chr(34) & WScript.ScriptFullName & Chr(34)\r\n link.WorkingDirectory=folder\r\n If fso.FileExists(iconFile) Then link.IconLocation=iconFile & ",0"\r\n link.Description="M.F. Case Number OCR and PDF Renamer"\r\n link.Save\r\nEnd If\r\nOn Error GoTo 0\r\nshell.CurrentDirectory=folder\r\nshell.Run Chr(34) & bat & Chr(34),1,False\r\n'''
d['README.txt']=b'''M.F. CASE NUMBER OCR - VERSION 3.3.5\r\n\r\nIN-APP UPDATE\r\nOpen the program, click UPDATE, and approve the prompts. The app closes, applies this fix, repairs the Desktop shortcut, and restarts.\r\n\r\nV3.3.5 FIX\r\n- Repairs the Desktop shortcut on the new computer.\r\n- The shortcut points to the launcher inside the actual program folder.\r\n- The program no longer looks for case_number_ocr.py directly on the Desktop.\r\n- The launcher checks that the program file exists.\r\n- No manual file copying is needed.\r\n\r\nDATA\r\nThis update does not upload or replace PDFs, corrections, queue state, or local learning data. Original PDFs and export behavior are unchanged.\r\n'''
order=['case_number_ocr.py','updater_apply.py','RUN_CASE_OCR.bat','Launch_Case_OCR.vbs','README.txt']
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:
 for n in order:
  i=zipfile.ZipInfo(n,(2026,1,1,0,0,0));i.compress_type=zipfile.ZIP_STORED;i.create_system=3;i.external_attr=0o100644<<16;z.writestr(i,d[n])
with zipfile.ZipFile(out) as z: assert z.testzip() is None and z.namelist()==order
print(hashlib.sha256(out.read_bytes()).hexdigest())
