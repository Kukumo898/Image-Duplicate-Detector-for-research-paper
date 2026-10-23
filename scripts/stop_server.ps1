$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$pythonProcesses = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")
$ownedProcesses = @($pythonProcesses | Where-Object {
    $_.CommandLine -and ($_.ExecutablePath -eq $taskPython -or $_.CommandLine.Contains('"' + $taskPython + '"')) -and
    $_.CommandLine -match '(?:^|\s)run\.py(?:\s|$)'
})
if ($ownedProcesses.Count -eq 0) {
    Write-Host 'No running server belonging to this project was found.'
    exit 0
}
$ownedIds = @($ownedProcesses | ForEach-Object { $_.ProcessId })
$ownedChildren = @($pythonProcesses | Where-Object { $ownedIds -contains $_.ParentProcessId })
foreach ($ownedProcess in @($ownedChildren) + @($ownedProcesses)) {
    Stop-Process -Id $ownedProcess.ProcessId -ErrorAction SilentlyContinue
}
Write-Host 'Local server stopped. Stored files and review records are retained.'
Write-Host 'Any in-progress scan is incomplete and must be submitted again.'

