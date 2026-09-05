# Meya Windows uninstaller
param([switch]$PurgeUserData)

$ErrorActionPreference = 'Stop'
$install = Join-Path $env:LOCALAPPDATA 'Programs\Meya'
$shortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Meya.lnk'
$userData = Join-Path $env:LOCALAPPDATA 'Meya'

Get-Process 'Meya.Windows' -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -like "*$install*asr_daemon.py*"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

if (Test-Path $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
if (Test-Path $install) { Remove-Item -LiteralPath $install -Recurse -Force }
if ($PurgeUserData -and (Test-Path $userData)) {
    Remove-Item -LiteralPath $userData -Recurse -Force
}
Write-Host 'Meya Windows was uninstalled. User data is preserved unless -PurgeUserData is set.'
