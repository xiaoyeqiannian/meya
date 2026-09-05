# Meya Windows Python bootstrap
param([switch]$ForceRecreate)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'

if ($ForceRecreate -and (Test-Path $venv)) {
    Remove-Item -LiteralPath $venv -Recurse -Force
}

if (-not (Test-Path $venvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv $venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $version = & python -c 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))'
        if ($version -ne '3.12') {
            throw "Python 3.12 is required; found Python $version"
        }
        & python -m venv $venv
    } else {
        throw 'Python 3.12 x64 was not found.'
    }
}

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements-windows.txt')
if ($LASTEXITCODE -ne 0) { throw 'Failed to install Windows Python dependencies.' }

Write-Host "Windows Python environment is ready: $venvPython"
Write-Host 'Next: scripts\download_paraformer_windows.ps1'
