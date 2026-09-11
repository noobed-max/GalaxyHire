#Requires -Version 5.1
<#
.SYNOPSIS
    Start or stop GalaxyHire on Windows.

.DESCRIPTION
    Deploys the Docker infrastructure, applies the corpus schema, builds the UI, and starts the
    corpus (port 8100) and API + built UI (port 8000) as hidden background processes. Logs and
    pid files live in .run\.

    With -Dev, the API runs without a prebuilt UI and the hot-reload web server starts on 1420.
    With -Stop, every process started by this script is terminated (whole process tree).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Dev,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'

$ScriptDir  = $PSScriptRoot
$RepoRoot   = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$CorpusDir  = Join-Path $RepoRoot 'services\corpus'
$ApiDir     = Join-Path $RepoRoot 'apps\api'
$WebDir     = Join-Path $RepoRoot 'apps\web'
$RunDir     = Join-Path $RepoRoot '.run'
$ComposeFile = Join-Path $RepoRoot 'docker-compose.yml'

$CorpusPort = 8100
$AppPort    = 8000
$WebPort    = 1420

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @()
    if ($machine) { $parts += $machine }
    if ($user) { $parts += $user }
    $parts += (Join-Path $HOME '.local\bin')
    $parts += (Join-Path $HOME '.bun\bin')
    $env:Path = ($parts -join ';')
}

function Test-PortInUse {
    param([int]$Port)
    try { return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop) }
    catch { return $false }
}

function Stop-All {
    if (-not (Test-Path $RunDir)) { Write-Host 'stopped (nothing recorded)'; return }
    Get-ChildItem -Path $RunDir -Filter '*.pid' -ErrorAction SilentlyContinue | ForEach-Object {
        $processId = (Get-Content $_.FullName -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($processId) {
            # /T kills the whole tree: uv spawns python, and bun/npm spawn node.
            taskkill /PID $processId /T /F 2>$null | Out-Null
        }
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Host 'stopped' -ForegroundColor Green
}

function Wait-Http {
    param([string]$Url, [string]$Label, [int]$Tries = 120)
    for ($i = 0; $i -lt $Tries; $i++) {
        $ready = $false
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            $ready = ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400)
        } catch {
            $ready = $false
        }
        if ($ready) {
            Write-Host ("  v {0} ready" -f $Label) -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 1
    }
    throw ("{0} did not become ready; see {1}" -f $Label, $RunDir)
}

function Start-ServiceProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    # Prefer a real Application over a PowerShell shim; Start-Process cannot run npm.ps1 correctly.
    $found = @(Get-Command $FilePath -All -ErrorAction Stop)
    $app = @($found | Where-Object { $_.CommandType -eq 'Application' }) | Select-Object -First 1
    $exe = if ($app) { $app.Source } else { $found[0].Source }
    $proc = Start-Process -FilePath $exe -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput (Join-Path $RunDir "$Name.out.log") `
        -RedirectStandardError (Join-Path $RunDir "$Name.err.log") `
        -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $RunDir "$Name.pid") -Value $proc.Id
    Write-Host ("  > started {0} (pid {1})" -f $Name, $proc.Id) -ForegroundColor Cyan
}

function Wait-Postgres {
    for ($i = 0; $i -lt 90; $i++) {
        & docker compose -f $ComposeFile exec -T postgres pg_isready -U galaxy 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host '  v database ready' -ForegroundColor Green; return }
        Start-Sleep -Seconds 1
    }
    throw 'Postgres did not become ready. Inspect: docker compose logs postgres'
}

if ($Stop) {
    Stop-All
    exit 0
}

if ($env:OS -ne 'Windows_NT') {
    throw 'This script is for Windows. Use ./scripts/start.sh on Linux or macOS.'
}

Update-SessionPath

foreach ($port in @($CorpusPort, $AppPort)) {
    if (Test-PortInUse -Port $port) {
        throw ("port {0} is already in use - run 'scripts\start.ps1 -Stop' first" -f $port)
    }
}

if (-not (Test-Command docker)) {
    throw 'Docker was not found. Run scripts\install.ps1 first (or install Docker Desktop).'
}

Write-Host 'starting postgres + redis' -ForegroundColor Cyan
Push-Location -LiteralPath $RepoRoot
try {
    & docker compose -f $ComposeFile up -d
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }
} finally {
    Pop-Location
}
Wait-Postgres

Write-Host 'applying migrations' -ForegroundColor Cyan
Push-Location -LiteralPath $CorpusDir
try {
    & uv run python -c "import asyncio; from galaxy.db.engine import migrate; asyncio.run(migrate())"
    if ($LASTEXITCODE -ne 0) { throw 'migrations failed' }
} finally {
    Pop-Location
}

if (-not $Dev) {
    if (-not $SkipBuild) {
        if (-not (Test-Command bun)) { throw 'Bun is required to build apps\web; run scripts\install.ps1 first.' }
        Write-Host 'building the UI' -ForegroundColor Cyan
        Push-Location -LiteralPath $WebDir
        try {
            & bun run build
            if ($LASTEXITCODE -ne 0) { throw 'UI build failed' }
        } finally {
            Pop-Location
        }
    }
}

Write-Host ("starting corpus on :{0}" -f $CorpusPort) -ForegroundColor Cyan
Start-ServiceProcess -Name 'corpus' -FilePath 'uv' `
    -Arguments @('run', 'uvicorn', 'galaxy.api.app:app', '--host', '127.0.0.1', '--port', $CorpusPort) `
    -WorkingDirectory $CorpusDir
Wait-Http -Url ("http://127.0.0.1:{0}/health" -f $CorpusPort) -Label 'corpus'

Write-Host ("starting API + UI on :{0}" -f $AppPort) -ForegroundColor Cyan
Start-ServiceProcess -Name 'api' -FilePath 'uv' `
    -Arguments @('run', 'python', 'main.py', '--port', $AppPort) `
    -WorkingDirectory $ApiDir
Wait-Http -Url ("http://127.0.0.1:{0}/health" -f $AppPort) -Label 'API'

$url = "http://127.0.0.1:{0}" -f $AppPort
if ($Dev) {
    if (-not (Test-Command bun)) { throw 'Bun is required for -Dev; run scripts\install.ps1 first.' }
    Write-Host ("starting web dev server on :{0}" -f $WebPort) -ForegroundColor Cyan
    Start-ServiceProcess -Name 'web' -FilePath 'bun' -Arguments @('run', 'dev') -WorkingDirectory $WebDir
    Wait-Http -Url ("http://127.0.0.1:{0}/" -f $WebPort) -Label 'web'
    $url = "http://127.0.0.1:{0}" -f $WebPort
}

Write-Host ''
Write-Host ("GalaxyHire is up  ->  {0}" -f $url) -ForegroundColor Green
Write-Host ("   logs: {0}\*.log     stop: powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop" -f $RunDir)
