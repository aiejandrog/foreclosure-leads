' Silent send_server.py launcher - no console window.
' Used by the Startup folder entry + the DealflowSendServer scheduled task.
'
' REPO RESOLUTION - this is the bug that kept autostart broken (found 2026-08-07).
' install_send_server_autostart.bat COPIES this file into the Startup folder. The old line
'     repo = fso.GetParentFolderName(WScript.ScriptFullName)
' therefore resolved 'repo' to the STARTUP FOLDER once copied, looked for
' <Startup>\send_server.py, failed the FileExists check on line ~42, and did WScript.Quit 1 -
' silently, with no window, no dialog and no log entry, at every single logon. A console-launched
' `python send_server.py` always worked, which is why this went unnoticed: the bridge was only ever
' up when someone started it by hand.
' Fix: the installer now writes an explicit REPO_PATH into the Startup copy. When that path is
' present and valid it wins; otherwise fall back to our own folder so running this file directly
' out of the repo still works.
Option Explicit
Dim sh, fso, repo, pyw, script, logf, REPO_PATH
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

REPO_PATH = ""   ' <-- installer rewrites this line in the Startup copy. Leave empty in the repo.

If REPO_PATH <> "" And fso.FileExists(REPO_PATH & "\send_server.py") Then
  repo = REPO_PATH
Else
  repo = fso.GetParentFolderName(WScript.ScriptFullName)
End If
script = repo & "\send_server.py"
logf = repo & "\send_server.log"

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
  Dim tmp, ts
  tmp = sh.ExpandEnvironmentStrings("%TEMP%") & "\df_send_pyw.txt"
  sh.Run "cmd /c where pythonw > """ & tmp & """ 2>nul", 0, True
  If fso.FileExists(tmp) Then
    Set ts = fso.OpenTextFile(tmp, 1)
    If Not ts.AtEndOfStream Then pyw = Trim(ts.ReadLine)
    ts.Close
    fso.DeleteFile tmp, True
  End If
End If

If pyw = "" Or Not fso.FileExists(pyw) Then
  If fso.FileExists("C:\Program Files\Python311\pythonw.exe") Then
    pyw = "C:\Program Files\Python311\pythonw.exe"
  End If
End If

' Never fail silently again. The original code quit here with no trace, which is exactly why a
' permanently-broken autostart looked identical to a working one.
If Not fso.FileExists(script) Then
  On Error Resume Next
  Dim ts2
  Set ts2 = fso.OpenTextFile(fso.GetParentFolderName(WScript.ScriptFullName) & "\send_server_autostart_error.log", 8, True)
  ts2.WriteLine Now & " ERROR: send_server.py not found at """ & script & """ (repo resolved to """ & repo & """). Re-run install_send_server_autostart.bat."
  ts2.Close
  On Error Goto 0
  WScript.Quit 1
End If
If pyw = "" Or Not fso.FileExists(pyw) Then
  On Error Resume Next
  Set ts = fso.OpenTextFile(logf, 8, True)
  ts.WriteLine Now & " ERROR: pythonw.exe not found - install Python or fix PATH"
  ts.Close
  On Error Goto 0
  WScript.Quit 1
End If

sh.CurrentDirectory = repo
' 0 = hidden window, False = don't wait
sh.Run """" & pyw & """ """ & script & """", 0, False
