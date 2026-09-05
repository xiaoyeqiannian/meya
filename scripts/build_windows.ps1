# Meya Windows build
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$command = Get-Command dotnet -ErrorAction SilentlyContinue
$dotnet = if ($command) { $command.Source } else { Join-Path $env:LOCALAPPDATA 'Microsoft\dotnet\dotnet.exe' }
if (-not (Test-Path $dotnet)) {
    throw '.NET 8 SDK was not found.'
}

$tests = Join-Path $root 'windows\Meya.Windows.ContractTests\Meya.Windows.ContractTests.csproj'
$app = Join-Path $root 'windows\Meya.Windows\Meya.Windows.csproj'
$output = Join-Path $root 'dist\Meya.Windows'

& $dotnet build $tests -c Release '-p:Platform=x64'
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }

$testDll = Join-Path $root 'windows\Meya.Windows.ContractTests\bin\x64\Release\net8.0-windows\Meya.Windows.ContractTests.dll'
& $dotnet $testDll $root
if ($LASTEXITCODE -ne 0) { throw 'Windows contract tests failed.' }

& $dotnet publish $app -c Release -r win-x64 --self-contained true '-p:Platform=x64' '-p:PublishSingleFile=true' '-p:IncludeNativeLibrariesForSelfExtract=true' -o $output
if ($LASTEXITCODE -ne 0) { throw 'Windows self-contained publish failed.' }

Write-Host "Windows publish output: $output"
