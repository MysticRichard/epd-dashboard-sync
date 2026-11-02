Option Explicit

Dim shell, powershell, runner, repoRoot, commandLine
Set shell = CreateObject("WScript.Shell")

runner = WScript.Arguments(0)
repoRoot = WScript.Arguments(1)
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
commandLine = Quote(powershell) & " -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File " & Quote(runner) & " -RepoRoot " & Quote(repoRoot)
shell.Run commandLine, 0, True

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
