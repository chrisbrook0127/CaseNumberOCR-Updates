Option Explicit
Dim shell, fso, folder, pyExe, scriptPath, cscriptExe, launcher, paths, file, key, value, text
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(folder, "case_number_ocr.py")
If Not fso.FileExists(scriptPath) Then
  MsgBox "MFCase application source is missing:" & vbCrLf & scriptPath & vbCrLf & _
         "Extract the complete ZIP or run SETUP.bat.", vbCritical, "MFCase startup"
  WScript.Quit 2
End If
' Self-repair shortcut even for upgrades installed through the Update button.
launcher = fso.BuildPath(folder, "Create_Desktop_Shortcut.vbs")
If fso.FileExists(launcher) Then
  On Error Resume Next
  shell.Run Chr(34) & shell.ExpandEnvironmentStrings("%SystemRoot%") & _
      "\System32\cscript.exe" & Chr(34) & " //nologo " & Chr(34) & launcher & Chr(34), 0, True
  On Error GoTo 0
Else
  On Error Resume Next
  Dim desktop, shortcut, linkPath
  desktop = shell.SpecialFolders("Desktop")
  linkPath = fso.BuildPath(desktop, "M.F. Case Number OCR.lnk")
  Set shortcut = shell.CreateShortcut(linkPath)
  shortcut.TargetPath = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\wscript.exe"
  shortcut.Arguments = Chr(34) & WScript.ScriptFullName & Chr(34)
  shortcut.WorkingDirectory = folder
  If fso.FileExists(fso.BuildPath(folder, "MF.ico")) Then shortcut.IconLocation = fso.BuildPath(folder, "MF.ico") & ",0"
  shortcut.Save
  On Error GoTo 0
End If
pyExe = ""
paths = Array( _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe", _
  shell.ExpandEnvironmentStrings("%ProgramFiles%") & "\Python311\pythonw.exe", _
  shell.ExpandEnvironmentStrings("%SystemDrive%") & "\Python311\pythonw.exe" _
)
file = fso.BuildPath(folder, "pythonw_path.txt")
If fso.FileExists(file) Then
  On Error Resume Next
  Dim stream
  Set stream = fso.OpenTextFile(file, 1, False)
  text = Trim(stream.ReadLine)
  stream.Close
  If fso.FileExists(text) Then pyExe = text
  On Error GoTo 0
End If
If pyExe = "" Then
  For Each value In paths
    If fso.FileExists(value) Then
      pyExe = value
      Exit For
    End If
  Next
End If
If pyExe = "" Then
  For Each key In Array( _
      "HKCU\Software\Python\PythonCore\3.11\InstallPath\ExecutablePath", _
      "HKLM\Software\Python\PythonCore\3.11\InstallPath\ExecutablePath")
    On Error Resume Next
    value = shell.RegRead(key)
    On Error GoTo 0
    If Not IsEmpty(value) Then
      text = fso.GetParentFolderName(CStr(value)) & "\pythonw.exe"
      If fso.FileExists(text) Then
        pyExe = text
        Exit For
      End If
    End If
  Next
End If
If pyExe = "" Then
  MsgBox "Python 3.11 was not found. Run SETUP.bat once." & vbCrLf & _
     "The desktop shortcut has been created; the app cannot start until setup finishes.", vbExclamation, "MFCase startup"
  WScript.Quit 3
End If
shell.CurrentDirectory = folder
' pythonw.exe runs entirely without a visible CMD window.
shell.Run Chr(34) & pyExe & Chr(34) & " " & Chr(34) & scriptPath & Chr(34), 0, False
