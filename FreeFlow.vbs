' FreeFlow windowless launcher: runs the app in the background with no console window.
' Optional arguments are passed through, e.g.:  FreeFlow.vbs --quit   |   --restart   |   --autostart on
Set sh = CreateObject("WScript.Shell")
root = Left(WScript.ScriptFullName, Len(WScript.ScriptFullName) - Len(WScript.ScriptName))
args = ""
For Each a In WScript.Arguments
    args = args & " """ & a & """"
Next
sh.Run """" & root & ".venv\Scripts\pythonw.exe"" """ & root & "FreeFlow.pyw""" & args, 0, False
