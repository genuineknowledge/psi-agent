Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")

' Run from the script's own directory so psi-agent.exe / haitun.ico / .env resolve.
strDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strDir

' Load .env (if present) into this process environment; the child inherits it.
strEnvPath = objFSO.BuildPath(strDir, ".env")
If objFSO.FileExists(strEnvPath) Then
    Set objEnv = objShell.Environment("Process")
    Set objFile = objFSO.OpenTextFile(strEnvPath, 1, False)
    Do Until objFile.AtEndOfStream
        strLine = Trim(objFile.ReadLine)
        If Len(strLine) > 0 And Left(strLine, 1) <> "#" Then
            intPos = InStr(strLine, "=")
            If intPos > 1 Then
                strKey = Trim(Left(strLine, intPos - 1))
                strVal = Trim(Mid(strLine, intPos + 1))
                strFirst = Left(strVal, 1)
                If Len(strVal) >= 2 And (strFirst = """" Or strFirst = "'") And Right(strVal, 1) = strFirst Then
                    strVal = Mid(strVal, 2, Len(strVal) - 2)
                End If
                If Len(strKey) > 0 Then objEnv(strKey) = strVal
            End If
        End If
    Loop
    objFile.Close
End If

' Prepend the bundled MSYS2 so psi-agent.exe (and its bash tool) finds bash/git/curl/ssh.
strMsysBin = objFSO.BuildPath(strDir, "msys64\usr\bin")
objShell.Environment("Process")("PATH") = strMsysBin & ";" & objShell.Environment("Process")("PATH")
' Keep bash -lc in the current working directory instead of cd-ing to $HOME.
objShell.Environment("Process")("CHERE_INVOKING") = "1"

objShell.Run "psi-agent.exe gateway --tray haitun.ico", 0, False
