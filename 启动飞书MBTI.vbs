Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
python = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(python) Then
  MsgBox "Please run install.ps1 first.", 48, "Feishu MBTI"
Else
  shell.Run Chr(34) & python & Chr(34) & " -m feishu_mbti.app --auto", 1, False
End If
