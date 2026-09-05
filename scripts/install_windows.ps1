# Meya Windows installer
param([switch]$NoBuild, [switch]$NoLaunch)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$publish = Join-Path $root 'dist\Meya.Windows'
$install = Join-Path $env:LOCALAPPDATA 'Programs\Meya'
$shortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Meya.lnk'

if (-not $NoBuild) {
    & (Join-Path $PSScriptRoot 'build_windows.ps1')
}
if (-not (Test-Path (Join-Path $publish 'Meya.Windows.exe'))) {
    throw 'Windows publish output is missing. Run build_windows.ps1 first.'
}
foreach ($required in @(
    (Join-Path $root '.venv\Scripts\python.exe'),
    (Join-Path $root 'models\paraformer\iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch\model.pt'),
    (Join-Path $root 'models\punctuation\iic--punc_ct-transformer_zh-cn-common-vocab272727-pytorch\model.pt')
)) {
    if (-not (Test-Path $required)) { throw "Runtime dependency is missing: $required" }
}

Get-Process 'Meya.Windows' -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -like "*$install*asr_daemon.py*"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

if (Test-Path $install) {
    Get-ChildItem -LiteralPath $install -Force | Remove-Item -Recurse -Force
}
New-Item -ItemType Directory -Path $install -Force | Out-Null
Copy-Item -Path (Join-Path $publish '*') -Destination $install -Recurse -Force
Get-ChildItem -LiteralPath $root -Filter '*.py' -File | Copy-Item -Destination $install -Force
Copy-Item -LiteralPath (Join-Path $root 'meya_core') -Destination $install -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root 'model-config.json') -Destination $install -Force
Copy-Item -LiteralPath (Join-Path $root '.venv') -Destination $install -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root 'models') -Destination $install -Recurse -Force
[IO.File]::WriteAllText((Join-Path $install 'project-root.txt'), $install, (New-Object Text.UTF8Encoding($false)))

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = Join-Path $install 'Meya.Windows.exe'
$link.WorkingDirectory = $install
$link.Description = 'Meya offline voice input for Windows'
$link.Save()

if (-not $NoLaunch) {
    Start-Process (Join-Path $install 'Meya.Windows.exe')
}
Write-Host "Meya installed to: $install"
