param([switch]$SkipMacPublish, [switch]$SkipUiSmoke)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$command = Get-Command dotnet -ErrorAction SilentlyContinue
$dotnet = if ($command) { $command.Source } else { Join-Path $env:LOCALAPPDATA 'Microsoft\dotnet\dotnet.exe' }
if (-not (Test-Path $dotnet)) {
    throw '.NET 8 SDK was not found.'
}

$tests = Join-Path $root 'tests\dotnet\Meya.Core.ContractTests\Meya.Core.ContractTests.csproj'
$desktop = Join-Path $root 'src\dotnet\Meya.Desktop\Meya.Desktop.csproj'
$testDll = Join-Path $root 'tests\dotnet\Meya.Core.ContractTests\bin\Release\net8.0\Meya.Core.ContractTests.dll'
$windowsOutput = Join-Path $root 'dist\Meya.Desktop.win-x64'
$macOutput = Join-Path $root 'dist\Meya.Desktop.osx-arm64'

& $dotnet build $tests -c Release
if ($LASTEXITCODE -ne 0) { throw 'Shared contract build failed.' }
& $dotnet $testDll $root
if ($LASTEXITCODE -ne 0) { throw 'Shared contracts failed.' }

& $dotnet publish $desktop -c Release -r win-x64 --self-contained true `
    '-p:PublishSingleFile=false' '-p:PublishTrimmed=false' -o $windowsOutput
if ($LASTEXITCODE -ne 0) { throw 'Windows Avalonia publish failed.' }

if (-not $SkipUiSmoke) {
    $process = Start-Process (Join-Path $windowsOutput 'Meya.Desktop.exe') `
        -ArgumentList '--overlay-smoke' -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Windows overlay smoke failed: $($process.ExitCode)" }
}

if (-not $SkipMacPublish) {
    & $dotnet publish $desktop -c Release -r osx-arm64 --self-contained true `
        '-p:PublishSingleFile=false' '-p:PublishTrimmed=false' -o $macOutput
    if ($LASTEXITCODE -ne 0) { throw 'macOS ARM64 Avalonia publish failed.' }
}

Write-Host "Shared Windows output: $windowsOutput"
if (-not $SkipMacPublish) { Write-Host "Shared macOS output: $macOutput" }
