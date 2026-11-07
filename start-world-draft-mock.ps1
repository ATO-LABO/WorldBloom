$ErrorActionPreference = 'Stop'
python -c "import yaml; print('PyYAML', yaml.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'Python / PyYAML must already be installed. No installation is performed.' }
$draftRuntime = Join-Path (Split-Path -Parent $PSScriptRoot) 'WorldBloom_new_world_mock_20261026_runtime'
$draftRuns = Join-Path $draftRuntime 'runs'
$draftControl = Join-Path $draftRuntime 'control'
$draftTemp = Join-Path $draftRuntime 'temp'
New-Item -ItemType Directory -Force -Path $draftRuns, $draftControl, $draftTemp | Out-Null
$env:TEMP = $draftTemp
$env:TMP = $draftTemp
Write-Host 'Fixed mock only. Open http://127.0.0.1:8772/worlds/new . Ctrl+C stops this server.'
Push-Location -LiteralPath $PSScriptRoot
try {
  python -B -m viewer.server --repo $PSScriptRoot --runs $draftRuns --control $draftControl --host 127.0.0.1 --port 8772 --world-draft-mock
} finally { Pop-Location }
