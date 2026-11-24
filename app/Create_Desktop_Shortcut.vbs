Option Explicit
Dim shell, fs, folder, shortcut, path, desktop, launcher, icon, wscriptExe
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
launcher = fs.BuildPath(folder, "Launch_Case_OCR.vbs")
If Not fs.FileExists(launcher) Then
  WScript.Echo "ERROR: Missing Launch_Case_OCR.vbs in " & folder
  WScript.Quit 2
End If
desktop = shell.SpecialFolders("Desktop")
path = fs.BuildPath(desktop, "M.F. Case Number OCR.lnk")
wscriptExe = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\wscript.exe"
Set shortcut = shell.CreateShortcut(path)
shortcut.TargetPath = wscriptExe
shortcut.Arguments = Chr(34) & launcher & Chr(34)
shortcut.WorkingDirectory = folder
icon = fs.BuildPath(folder, "MF.ico")
If fs.FileExists(icon) Then shortcut.IconLocation = icon & ",0"
shortcut.Description = "M.F. Case Number OCR & PDF Renamer"
shortcut.WindowStyle = 1
On Error Resume Next
shortcut.Save
If Err.Number <> 0 Then
  WScript.Echo "Shortcut creation failed: " & Err.Description
  WScript.Quit 3
End If
On Error GoTo 0
If Not fs.FileExists(path) Then
  WScript.Echo "Shortcut not created: " & path
  WScript.Quit 4
End If
WScript.Echo "Created: " & path
