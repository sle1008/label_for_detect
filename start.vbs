' Silent launcher: no terminal window at all (double-click this file).
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir

pythonw = "pythonw.exe"
If Not fso.FileExists(fso.BuildPath(appDir, pythonw)) Then
    pythonw = "python.exe"
End If

shell.Run """" & pythonw & """ """ & appDir & "\main.py""", 0, False
