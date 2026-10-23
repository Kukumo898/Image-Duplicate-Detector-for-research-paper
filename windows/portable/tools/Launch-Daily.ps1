param([Parameter(Mandatory=$true)][ValidateSet('start','stop','backup','restore','status')][string]$Action)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$release = Split-Path -Parent $PSScriptRoot
try {
    # This bootstrap ONLY uses the embedded Python shipped in this package.
    # Never discover a workspace venv, PATH Python, py launcher or Codex cache.
    $candidates = @((Join-Path $release 'runtime\python\python.exe'), (Join-Path $release 'runtime\python.exe'))
    $python = $null
    $probe = 'import sys,sqlite3,zipfile,xml.etree.ElementTree,fastapi,uvicorn,multipart,cv2,numpy,PIL,fitz; assert sys.version_info >= (3,12); print(sys.executable)'
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $result = @(& $candidate -B -X utf8 -c $probe 2>&1)
            if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
        } catch { }
    }
    if (-not $python) { throw '随包 Python 或依赖不完整。请重新解压完整的图鉴 ZIP；本入口不会借用外部 Python、联网下载或修改系统设置。' }
    $profile = Join-Path $release 'daily_profile.json'
    if ($env:LABIMAGE_PROFILE) { $profile = $env:LABIMAGE_PROFILE }
    $launcherArgs = @('-B','-X','utf8',(Join-Path $PSScriptRoot 'daily_launcher.py'),$Action,'--profile',$profile)
    if ($Action -eq 'restore') {
        $archive = $env:LABIMAGE_RESTORE_ARCHIVE
        if (-not $archive) {
            Add-Type -AssemblyName System.Windows.Forms
            $dialog = New-Object System.Windows.Forms.OpenFileDialog
            $dialog.Title = '选择图鉴备份 ZIP（恢复为新的独立软件文件夹，不覆盖旧资料）'
            $dialog.Filter = '图鉴备份 (*.zip)|*.zip'
            $dialog.InitialDirectory = Join-Path $release 'backups'
            if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host '已取消恢复。'; exit 0 }
            $archive = $dialog.FileName
        }
        $launcherArgs += @('--archive',$archive)
        if ($env:LABIMAGE_RESTORE_TARGET) { $launcherArgs += @('--target',$env:LABIMAGE_RESTORE_TARGET) }
    }
    & $python @launcherArgs
    $code = $LASTEXITCODE
    if ($code -ne 0) { Write-Host ('详细日志：' + (Join-Path $release 'launcher_errors.log')) -ForegroundColor Yellow }
    if ($env:LABIMAGE_NONINTERACTIVE -ne '1' -and ($code -ne 0 -or $Action -ne 'start')) { Read-Host '按回车关闭此窗口' | Out-Null }
    exit $code
} catch {
    Write-Host ('操作未完成：' + $_.Exception.Message) -ForegroundColor Red
    Add-Content -LiteralPath (Join-Path $release 'launcher_errors.log') -Value $_.Exception.ToString() -Encoding UTF8
    if ($env:LABIMAGE_NONINTERACTIVE -ne '1') { Read-Host '按回车关闭此窗口（错误不会闪退）' | Out-Null }
    exit 1
}
