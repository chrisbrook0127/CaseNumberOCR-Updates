Option Explicit
Dim shell, fso, folder, pyExe, scriptPath, iconFile, desk, linkPath, link, wscriptExe
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(folder, "case_number_ocr.py")
pyExe = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe"
iconFile = fso.BuildPath(folder, "MF.ico")

If Not fso.FileExists(scriptPath) Then
  MsgBox "Application file not found:" & vbCrLf & scriptPath, vbCritical, "M.F. Case Number OCR"
  WScript.Quit 2
End If
If Not fso.FileExists(pyExe) Then
  MsgBox "Python 3.11 windowless launcher was not found:" & vbCrLf & pyExe & vbCrLf & _
         "Run SETUP.bat to repair the Python installation.", vbCritical, "M.F. Case Number OCR"
  WScript.Quit 3
End If

' Rebuild the desktop shortcut to this quiet launcher.
On Error Resume Next
desk = shell.SpecialFolders("Desktop")
linkPath = fso.BuildPath(desk, "M.F. Case Number OCR.lnk")
Set link = shell.CreateShortcut(linkPath)
link.TargetPath = fso.BuildPath(folder, "Launch_Case_OCR.vbs")
link.WorkingDirectory = folder
If fso.FileExists(iconFile) Then link.IconLocation = iconFile & ",0"
link.Description = "M.F. Case Number OCR and PDF Renamer"
link.Save
On Error GoTo 0

shell.CurrentDirectory = folder
' Window style 0 hides the console completely.
shell.Run Chr(34) & pyExe & Chr(34) & " " & Chr(34) & scriptPath & Chr(34), 0, False
