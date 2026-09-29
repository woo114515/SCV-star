param(
    [ValidateSet('probe', 'inspect', 'smoke', 'fog', 'human')]
    [string]$Mode = 'smoke',
    [string]$RunId = ('m0-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [int]$Seed = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($RunId -notmatch '^[a-zA-Z0-9_-]+$') { throw 'RunId must be a simple directory name' }
$config = Get-Content -LiteralPath (Join-Path $projectRoot 'configs/local/m0.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project M0 environment is not installed' }
$oldPythonPath = $env:PYTHONPATH
Push-Location $projectRoot
try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $arguments = @('-m', 'scv_star.runtime.m0', $Mode, '--install', $config.install_path,
        '--build', [string]$config.base_build, '--engine', 'configs/local/engine-m0.json',
        '--map', $config.map_path, '--map-sha256', $config.map_sha256,
        '--seed', [string]$Seed, '--output', ('artifacts/runs/' + $RunId))
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "M0 run failed; inspect artifacts/runs/$RunId/report.json" }
} finally {
    Pop-Location
    $env:PYTHONPATH = $oldPythonPath
}
