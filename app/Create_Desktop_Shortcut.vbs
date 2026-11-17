Option Explicit

Dim fso, shell, appFolder, desktopFolder, launcher, iconFile, wscriptExe, linkPath, link
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' Use the folder containing this script. This avoids hard-coded Desktop paths.
appFolder = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = fso.BuildPath(appFolder, "Launch_Case_OCR.vbs")
iconFile = fso.BuildPath(appFolder, "MF.ico")
wscriptExe = fso.BuildPath(shell.ExpandEnvironmentStrings("%WINDIR%"), "System32\wscript.exe")
desktopFolder = shell.SpecialFolders("Desktop")
linkPath = fso.BuildPath(desktopFolder, "M.F. Case Number OCR.lnk")

If Not fso.FileExists(launcher) Then
    WScript.Echo "ERROR: Launch_Case_OCR.vbs was not found beside this shortcut creator."
    WScript.Quit 2
End If
If Not fso.FileExists(wscriptExe) Then
    WScript.Echo "ERROR: Windows Script Host was not found."
    WScript.Quit 3
End If

Set link = shell.CreateShortcut(linkPath)
link.TargetPath = wscriptExe
link.Arguments = Chr(34) & launcher & Chr(34)
link.WorkingDirectory = appFolder
If fso.FileExists(iconFile) Then link.IconLocation = iconFile & ",0"
link.Description = "M.F. Case Number OCR and PDF Renamer"
link.Save

WScript.Echo "Shortcut created: " & linkPath
WScript.Quit 0
