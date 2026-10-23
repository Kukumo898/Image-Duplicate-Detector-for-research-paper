param([int]$ProcessId)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$p = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $ProcessId)
if ($null -eq $p) { throw 'Process absent' }
$p.CommandLine | ConvertTo-Json -Compress
