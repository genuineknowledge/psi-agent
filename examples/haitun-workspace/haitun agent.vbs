Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.Run "psi-agent.exe gateway --tray haitun.ico", 0, False
