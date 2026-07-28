' Silent Diligence API launcher — no console window.
' Used by Startup shortcut + install_diligence_autostart.bat
Option Explicit
Dim sh, fso, repo, pyw, script, logf
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
repo = fso.GetParentFolderName(WScript.ScriptFullName)
script = repo & "\diligence_server.py"
logf = repo & "\diligence_server.log"

pyw = ""
On Error Resume Next
pyw = sh.RegRead("HKLM\SOFTWARE\Python\PythonCore\3.11\InstallPath\") & "pythonw.exe"
If Err.Number <> 0 Or Not fso.FileExists(pyw) Then
  Err.Clear
  pyw = sh.RegRead("HKLM\SOFTWARE\Python\PythonCore\3.12\InstallPath\") & "pythonw.exe"
End If
If Err.Number <> 0 Or Not fso.FileExists(pyw) Then
  Err.Clear
  pyw = sh.RegRead("HKLM\SOFTWARE\Python\PythonCore\3.10\InstallPath\") & "pythonw.exe"
End If
On Error Goto 0

If pyw = "" Or Not fso.FileExists(pyw) Then
  ' Fall back to PATH lookup via cmd where
  Dim tmp, ts
  tmp = sh.ExpandEnvironmentStrings("%TEMP%") & "\df_pyw.txt"
  sh.Run "cmd /c where pythonw > """ & tmp & """ 2>nul", 0, True
  If fso.FileExists(tmp) Then
    Set ts = fso.OpenTextFile(tmp, 1)
    If Not ts.AtEndOfStream Then pyw = Trim(ts.ReadLine)
    ts.Close
    fso.DeleteFile tmp, True
  End If
End If

If pyw = "" Or Not fso.FileExists(pyw) Then
  ' Last resort: Program Files Python311
  If fso.FileExists("C:\Program Files\Python311\pythonw.exe") Then
    pyw = "C:\Program Files\Python311\pythonw.exe"
  End If
End If

If Not fso.FileExists(script) Then WScript.Quit 1
If pyw = "" Or Not fso.FileExists(pyw) Then
  On Error Resume Next
  Set ts = fso.OpenTextFile(logf, 8, True)
  ts.WriteLine Now & " ERROR: pythonw.exe not found — install Python or fix PATH"
  ts.Close
  On Error Goto 0
  WScript.Quit 1
End If

sh.CurrentDirectory = repo
' 0 = hidden window, False = don't wait
sh.Run """" & pyw & """ """ & script & """", 0, False
