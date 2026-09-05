# Meya Windows model downloader
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Windows Python environment is missing. Run bootstrap_windows.ps1 first.'
}

$targets = @(
    @('iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online', 'models\paraformer\iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online'),
    @('iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch', 'models\paraformer\iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'),
    @('iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch', 'models\punctuation\iic--punc_ct-transformer_zh-cn-common-vocab272727-pytorch')
)

foreach ($item in $targets) {
    $model = $item[0]
    $target = Join-Path $root $item[1]
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    & $python -c 'from modelscope import snapshot_download; import sys; snapshot_download(sys.argv[1], local_dir=sys.argv[2])' $model $target
    if ($LASTEXITCODE -ne 0) { throw "Model download failed: $model" }
}

Write-Host 'Windows Paraformer models are ready.'
