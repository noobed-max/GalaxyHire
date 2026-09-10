#Requires -Version 5.1
<#
.SYNOPSIS
    GalaxyHire one-command installer for Windows.

.DESCRIPTION
    Installs and deploys everything a native Windows GalaxyHire needs:

      1. winget packages (Git, Node.js LTS, Docker Desktop)
      2. toolchains: uv (Python), Bun
      3. workspace dependencies (corpus + API via uv, web/extension/scraper/contract via npm/bun)
      4. local configuration (.env files, copied from the committed examples)
      5. Postgres + Redis containers and the corpus schema
      6. production web UI and browser-extension builds
      7. optionally starts the stack in the background

    Design rules:
      - Idempotent: safe to re-run; existing config files are never overwritten.
      - Non-destructive: it never deletes databases, configs, or build output.
      - Honest: it fails with the failing command and a stack trace instead of continuing half-installed.
      - No secrets: nothing is written to the repository; .env files are gitignored.

.PARAMETER Yes
    Answer yes to every prompt (unattended installs).

.PARAMETER Start
    Start GalaxyHire in the background after installing.

.PARAMETER Dev
    Start with the hot-reload dev server (implies -Start).

.PARAMETER DepsOnly
    Only install dependencies; skip Docker, the schema, and builds.

.PARAMETER SkipDocker
    Never install Docker; it must already be installed and running.

.PARAMETER DryRun
    Print every action without changing the system.

.PARAMETER Help
    Show this help.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Start
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$Start,
    [switch]$Dev,
    [switch]$DepsOnly,
    [switch]$SkipDocker,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

# ---- paths ------------------------------------------------------------------
$ScriptDir  = $PSScriptRoot
$RepoRoot   = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$CorpusDir  = Join-Path $RepoRoot 'services\corpus'
$ApiDir     = Join-Path $RepoRoot 'apps\api'
$WebDir     = Join-Path $RepoRoot 'apps\web'
$ExtDir     = Join-Path $RepoRoot 'apps\extension'
$ScraperDir = Join-Path $RepoRoot 'services\scraper-node'
$ContractDir = Join-Path $RepoRoot 'packages\contract'
$RunDir     = Join-Path $RepoRoot '.run'
$ComposeFile = Join-Path $RepoRoot 'docker-compose.yml'
$LogFile    = Join-Path $RunDir ("install-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

# Vite 7 (web + extension) requires Node ^20.19 || >=22.12.
$NodeMinMajorLow = 20; $NodeMinMinorLow = 19
$NodeMinMajorHigh = 22; $NodeMinMinorHigh = 12

$TotalSteps = 8
$Step = 0
$NeedRelogin = $false
$ShellPathUpdated = $false

if ($Dev) { $Start = $true }

# ---- logging ----------------------------------------------------------------
function Write-LogLine {
    param([string]$Message)
    if ($script:DryRun) { return }
    try { Add-Content -Path $script:LogFile -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message) -ErrorAction SilentlyContinue } catch { $null = $_ }
}

function Write-Info {
    param([string]$Message)
    Write-Host ("  > {0}" -f $Message) -ForegroundColor Cyan
    Write-LogLine "INFO  $Message"
}

function Write-Ok {
    param([string]$Message)
    Write-Host ("  v {0}" -f $Message) -ForegroundColor Green
    Write-LogLine "OK    $Message"
}

function Write-WarnLine {
    param([string]$Message)
    Write-Host ("  ! {0}" -f $Message) -ForegroundColor Yellow
    Write-LogLine "WARN  $Message"
}

function Write-Section {
    param([string]$Message)
    $script:Step++
    Write-Host ""
    Write-Host ("==> [{0}/{1}] {2}" -f $script:Step, $script:TotalSteps, $Message) -ForegroundColor White
    Write-LogLine "== $Message"
}

# ---- helpers ----------------------------------------------------------------
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

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Confirm {
    param([string]$Prompt)
    if ($script:Yes) { return $true }
    $reply = Read-Host ("  ? {0} [Y/n]" -f $Prompt)
    if ([string]::IsNullOrWhiteSpace($reply)) { return $true }
    return $reply -match '^[Yy]'
}

# Run a native command. Honors -DryRun, logs it, and throws on a non-zero exit code
# unless -AllowFailure is set (callers then read $script:NativeExit).
$script:NativeExit = 0
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $RepoRoot,
        [switch]$AllowFailure
    )
    $display = "{0} {1}" -f $FilePath, ($Arguments -join ' ')
    if ($script:DryRun) {
        Write-Host ("      [dry-run] {0}" -f $display) -ForegroundColor DarkGray
        Write-LogLine "DRY   $display"
        $script:NativeExit = 0
        return
    }
    Write-LogLine "RUN   $display"
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        $script:NativeExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($script:NativeExit -ne 0 -and -not $AllowFailure) {
        throw "Command failed with exit code $($script:NativeExit): $display"
    }
}

function Install-WingetPackage {
    param([string]$Id, [string]$Label = $Id)
    if (-not (Test-Command winget)) {
        throw "winget is required to install $Label automatically. Install 'App Installer' from the Microsoft Store, then re-run."
    }
    Write-Info "Installing $Label via winget ($Id)..."
    Invoke-Native -FilePath 'winget' -Arguments @(
        'install', '--id', $Id, '-e',
        '--accept-source-agreements', '--accept-package-agreements', '--silent'
    )
    Update-SessionPath
}

function Test-NodeVersion {
    if (-not (Test-Command node)) { return $false }
    try { $raw = (& node -v 2>$null) } catch { return $false }
    if (-not $raw) { return $false }
    $v = $raw.Trim().TrimStart('v')
    $parts = $v.Split('.')
    if ($parts.Count -lt 2) { return $false }
    $major = 0; $minor = 0
    [void][int]::TryParse($parts[0], [ref]$major)
    [void][int]::TryParse($parts[1], [ref]$minor)
    if ($major -gt $NodeMinMajorHigh) { return $true }
    if ($major -eq $NodeMinMajorLow -and $minor -ge $NodeMinMinorLow) { return $true }
    if ($major -eq $NodeMinMajorHigh -and $minor -ge $NodeMinMinorHigh) { return $true }
    return $false
}

# ---- 1. system packages -----------------------------------------------------
function Install-BasePackages {
    Write-Section 'System packages'
    if (Test-Command git) { Write-Ok 'Git is present' }
    else {
        Write-WarnLine 'Git is required to fetch dependencies.'
        if (Confirm 'Install Git with winget?') { Install-WingetPackage -Id 'Git.Git' -Label 'Git' }
        else { throw 'Git is required.' }
    }
    if (-not (Test-Command winget)) {
        Write-WarnLine "winget was not found. Automatic installs of Node.js/Docker require 'App Installer'."
    }
}

# ---- 2. toolchains ----------------------------------------------------------
function Ensure-Uv {
    Update-SessionPath
    if (Test-Command uv) { Write-Ok ("uv {0}" -f ((& uv --version) -replace '^uv\s+', '')) ; return }
    if ($script:DryRun) {
        Write-Info '[dry-run] would install uv from https://astral.sh/uv'
        Write-Ok 'uv would be installed'
        return
    }
    Write-Info 'Installing uv (Python toolchain)...'
    try { Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' | Invoke-Expression }
    catch {
        Write-WarnLine 'The uv installer failed; trying winget.'
        Install-WingetPackage -Id 'astral-sh.uv' -Label 'uv'
    }
    Update-SessionPath
    if (-not (Test-Command uv)) { throw 'uv was installed but is not on PATH (expected %USERPROFILE%\.local\bin\uv.exe).' }
    Write-Ok 'uv installed'
}

function Ensure-Node {
    Update-SessionPath
    if (Test-NodeVersion) { Write-Ok ("Node.js {0}" -f (& node -v)) ; return }
    if (Test-Command node) { Write-WarnLine ("Node.js {0} is older than required (need 20.19+/22.12+)." -f (& node -v)) }
    if ($script:DryRun) {
        Write-Info '[dry-run] would install Node.js LTS via winget'
        Write-Ok 'Node.js would be installed'
        return
    }
    Install-WingetPackage -Id 'OpenJS.NodeJS.LTS' -Label 'Node.js LTS'
    if (-not (Test-NodeVersion)) { throw 'Node.js installation did not produce a supported version; open a new terminal and re-run.' }
    Write-Ok ("Node.js {0}" -f (& node -v))
}

function Ensure-Bun {
    Update-SessionPath
    if (Test-Command bun) { Write-Ok ("Bun {0}" -f (& bun --version)) ; return }
    if ($script:DryRun) {
        Write-Info '[dry-run] would install Bun from https://bun.sh'
        Write-Ok 'Bun would be installed'
        return
    }
    Write-Info 'Installing Bun...'
    try { Invoke-RestMethod -Uri 'https://bun.sh/install.ps1' | Invoke-Expression }
    catch {
        Write-WarnLine 'The Bun installer failed; trying winget.'
        Install-WingetPackage -Id 'Oven-sh.Bun' -Label 'Bun'
    }
    Update-SessionPath
    if (-not (Test-Command bun)) { throw 'Bun was installed but is not on PATH (expected %USERPROFILE%\.bun\bin\bun.exe).' }
    Write-Ok 'Bun installed'
}

function Ensure-ShellPath {
    # uv and Bun install into ~/.local/bin and ~/.bun/bin; make future PowerShell sessions see them.
    $profileDir = Split-Path -Parent $PROFILE
    if (-not $profileDir) { return }
    $existing = ''
    if (Test-Path $PROFILE) { $existing = Get-Content -Path $PROFILE -Raw -ErrorAction SilentlyContinue }
    if ($existing -and $existing -match '\.bun\\bin') { return }
    if ($script:DryRun) {
        Write-Info ("[dry-run] would add the toolchain PATH to {0}" -f $PROFILE)
        return
    }
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    if (-not (Test-Path $PROFILE)) { New-Item -ItemType File -Force -Path $PROFILE | Out-Null }
    $line = "`n# GalaxyHire toolchain (added by scripts\install.ps1)`n" +
            "`$env:Path += ';' + (Join-Path `$HOME '.local\bin') + ';' + (Join-Path `$HOME '.bun\bin')`n"
    Add-Content -Path $PROFILE -Value $line
    $script:ShellPathUpdated = $true
}

function Ensure-Toolchains {
    Write-Section 'Toolchains (Python, Node.js, Bun)'
    Ensure-Uv
    Ensure-Node
    Ensure-Bun
    Ensure-ShellPath
    Write-Ok 'toolchains ready'
}

# ---- 3. Docker --------------------------------------------------------------
function Test-DockerReady {
    if (-not (Test-Command docker)) { return $false }
    & docker info 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Start-DockerDesktop {
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $desktop)) { return $false }
    Write-Info 'Starting Docker Desktop and waiting for the engine...'
    Start-Process -FilePath $desktop | Out-Null
    for ($i = 0; $i -lt 180; $i++) {
        Start-Sleep -Seconds 1
        if (Test-DockerReady) { return $true }
    }
    return $false
}

function Ensure-Docker {
    Write-Section 'Docker'
    if (-not (Test-Command docker)) {
        if ($SkipDocker) { throw 'Docker is not installed and -SkipDocker was given. Install Docker, then re-run.' }
        if ($script:DryRun) {
            Write-Info '[dry-run] would install Docker Desktop via winget and start the engine'
            return
        }
        Write-WarnLine 'Docker Desktop is not installed.'
        if (-not (Confirm 'Install Docker Desktop with winget?')) { throw 'Docker is required to run Postgres and Redis.' }
        if (-not (Test-Admin)) {
            Write-WarnLine 'Docker Desktop needs an elevated install. A UAC prompt may appear; if the install fails, re-run this script from an Administrator PowerShell.'
        }
        Install-WingetPackage -Id 'Docker.DockerDesktop' -Label 'Docker Desktop'
        $script:NeedRelogin = $true
        Write-WarnLine 'Docker Desktop may require a sign-out or reboot before the engine starts.'
    }

    if (-not (Test-DockerReady)) {
        if ($script:DryRun) {
            Write-Info '[dry-run] would verify the Docker engine'
            return
        }
        if (-not (Start-DockerDesktop)) {
            throw 'The Docker engine is not responding. Start Docker Desktop (WSL 2 backend), then re-run. See docs/troubleshooting.md.'
        }
    }

    Invoke-Native -FilePath 'docker' -Arguments @('compose', 'version')
    Write-Ok 'Docker and Compose v2 are ready'
}

# ---- 4. workspace dependencies ----------------------------------------------
function Invoke-UvSync {
    param([string]$Directory, [string[]]$Extra = @())
    $uvArgs = @('sync') + $Extra
    Invoke-Native -FilePath 'uv' -Arguments $uvArgs -WorkingDirectory $Directory
}

function Invoke-NodeInstall {
    param([string]$Directory)
    if ($Directory -eq $WebDir -and (Test-Path (Join-Path $WebDir 'bun.lock'))) {
        Invoke-Native -FilePath 'bun' -Arguments @('install', '--frozen-lockfile') -WorkingDirectory $Directory
    }
    elseif (Test-Path (Join-Path $Directory 'package-lock.json')) {
        Invoke-Native -FilePath 'npm' -Arguments @('ci') -WorkingDirectory $Directory
    }
    else {
        throw "No lockfile found in $Directory; refusing an unpinned install."
    }
}

function Install-WorkspaceDeps {
    Write-Section 'Workspace dependencies'
    Write-Info 'corpus Python environment (services\corpus)...'
    Invoke-UvSync -Directory $CorpusDir -Extra @('--extra', 'embed')
    Write-Info 'API Python environment (apps\api)...'
    Invoke-UvSync -Directory $ApiDir
    Write-Info 'web UI dependencies (apps\web)...'
    Invoke-NodeInstall -Directory $WebDir
    Write-Info 'browser extension dependencies (apps\extension)...'
    Invoke-NodeInstall -Directory $ExtDir
    Write-Info 'scraper dependencies (services\scraper-node)...'
    Invoke-NodeInstall -Directory $ScraperDir
    Write-Info 'contract dependencies (packages\contract)...'
    Invoke-NodeInstall -Directory $ContractDir
    Write-Ok 'all workspace dependencies installed'
}

# ---- 5. configuration -------------------------------------------------------
function Prepare-Env {
    Write-Section 'Configuration'
    if (-not $script:DryRun) {
        New-Item -ItemType Directory -Force -Path (Join-Path $CorpusDir 'data\objects') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $CorpusDir 'data\resumes') | Out-Null
        New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    }

    $corpusEnv = Join-Path $CorpusDir '.env'
    if (Test-Path $corpusEnv) { Write-Info 'Keeping the existing services\corpus\.env' }
    else {
        if ($script:DryRun) { Write-Info "[dry-run] would copy services\corpus\.env.example to .env" }
        else {
            Copy-Item -Path (Join-Path $CorpusDir '.env.example') -Destination $corpusEnv
            Write-Ok 'Created services\corpus\.env from the committed example'
        }
    }

    $scraperEnv = Join-Path $ScraperDir '.env'
    if (Test-Path $scraperEnv) { Write-Info 'Keeping the existing services\scraper-node\.env' }
    else {
        if ($script:DryRun) { Write-Info "[dry-run] would copy services\scraper-node\.env.example to .env" }
        else {
            Copy-Item -Path (Join-Path $ScraperDir '.env.example') -Destination $scraperEnv
            Write-Ok 'Created services\scraper-node\.env from the committed example'
        }
    }

    if ((Test-Path $corpusEnv) -and -not (Select-String -Path $corpusEnv -Pattern 'localhost:5433' -Quiet)) {
        Write-WarnLine 'services\corpus\.env does not reference localhost:5433; the Docker Postgres is published there.'
    }
    Write-Ok 'configuration ready (no secrets are written to the repository)'
}

# ---- 6. infrastructure ------------------------------------------------------
function Wait-Postgres {
    if ($script:DryRun) { Write-Info '[dry-run] would wait for Postgres'; return }
    for ($i = 0; $i -lt 90; $i++) {
        & docker compose -f $ComposeFile exec -T postgres pg_isready -U galaxy 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Ok 'Postgres ready'; return }
        Start-Sleep -Seconds 1
    }
    throw 'Postgres did not become ready. Inspect: docker compose logs postgres'
}

function Wait-Redis {
    if ($script:DryRun) { Write-Info '[dry-run] would wait for Redis'; return }
    for ($i = 0; $i -lt 60; $i++) {
        $pong = & docker compose -f $ComposeFile exec -T redis redis-cli ping 2>$null
        if ($LASTEXITCODE -eq 0 -and ($pong -join '') -match 'PONG') { Write-Ok 'Redis ready'; return }
        Start-Sleep -Seconds 1
    }
    throw 'Redis did not answer its health check. Inspect: docker compose logs redis'
}

function Deploy-Infra {
    Write-Section 'Infrastructure (Postgres + Redis)'
    if (-not (Test-Command docker)) {
        if ($script:DryRun) { Write-Info '[dry-run] would start Postgres + Redis via docker compose'; return }
        throw 'Docker is required. Re-run without -SkipDocker.'
    }
    if (-not (Test-DockerReady)) {
        if ($script:DryRun) { Write-Info '[dry-run] would start Postgres + Redis via docker compose'; return }
        throw 'The Docker engine is not responding. Start Docker Desktop and re-run.'
    }
    Write-Info 'Starting containers...'
    Invoke-Native -FilePath 'docker' -Arguments @('compose', '-f', $ComposeFile, 'up', '-d')
    Wait-Postgres
    Wait-Redis
}

# ---- 7. schema --------------------------------------------------------------
function Invoke-Migrations {
    Write-Section 'Database schema'
    Write-Info 'Applying corpus migrations...'
    Invoke-Native -FilePath 'uv' -Arguments @(
        'run', 'python', '-c',
        'import asyncio; from galaxy.db.engine import migrate; asyncio.run(migrate())'
    ) -WorkingDirectory $CorpusDir
    Write-Ok 'schema is up to date'
}

# ---- 8. builds --------------------------------------------------------------
function Build-Artifacts {
    Write-Section 'Production builds'
    Update-SessionPath
    if (-not (Test-Command bun)) {
        throw 'Bun is required to build apps\web (its build script invokes bun). Re-run the installer, then retry.'
    }
    Write-Info 'Building the web UI...'
    Invoke-Native -FilePath 'bun' -Arguments @('run', 'build') -WorkingDirectory $WebDir
    Write-Info 'Building the browser extension...'
    Invoke-Native -FilePath 'npm' -Arguments @('run', 'build') -WorkingDirectory $ExtDir
    Write-Ok 'builds complete'
}

# ---- start / summary --------------------------------------------------------
function Show-Summary {
    if ($script:DryRun) {
        Write-Host ''
        Write-Host 'Dry run complete - no changes were made.' -ForegroundColor White
        Write-Host '  Re-run without -DryRun to perform the install (add -Start to launch afterwards).'
        return
    }
    Write-Host ''
    Write-Host 'GalaxyHire is installed.' -ForegroundColor Green
    Write-Host '  Web + API   http://127.0.0.1:8000'
    Write-Host '  Corpus      http://127.0.0.1:8100'
    Write-Host ("  Logs        {0}\*.log" -f $RunDir)
    Write-Host ''
    Write-Host '  Start       powershell -ExecutionPolicy Bypass -File scripts\start.ps1'
    Write-Host '  Dev mode    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Dev'
    Write-Host '  Stop        powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop'
    Write-Host '  Docs        docs\getting-started.md'
    if ($script:Start) {
        Write-Host '  Started in the background; stop with scripts\start.ps1 -Stop'
    }
    else {
        Write-Host ''
        Write-Host '  Start it when ready:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1'
    }
    Write-Host ''
    Write-Host '  Next: open the app, go to Settings, and configure an AI provider (docs\ai-providers.md).'
    if ($script:NeedRelogin) {
        Write-Host ''
        Write-Host "  Note: Docker Desktop may require a sign-out or reboot before the engine starts."
    }
    if ($script:ShellPathUpdated) {
        Write-Host '  Open a new PowerShell window before running start.ps1 so the toolchains are on PATH.'
    }
    Write-Host ''
    Write-Host ("  Full install log: {0}" -f $script:LogFile)
}

function Show-Usage {
    Write-Host @'
GalaxyHire installer for Windows

Usage: powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [options]

Options:
  -Yes          assume "yes" for all prompts (non-interactive installs)
  -Start        start GalaxyHire in the background after installing
  -Dev          start with the hot-reload dev server (implies -Start)
  -DepsOnly     only install dependencies; skip Docker, schema, and builds
  -SkipDocker   never install Docker (it must already be present and running)
  -DryRun       print what would happen without changing anything
  -Help         show this help

Exit status: 0 on success, non-zero on the first failed step.
Full output is mirrored to .run\install-*.log.
'@
}

# ---- main -------------------------------------------------------------------
function Main {
    if ($Help) { Show-Usage; return }
    if ($DepsOnly -and ($Start -or $Dev)) {
        throw '-DepsOnly cannot be combined with -Start/-Dev.'
    }
    if ($env:OS -ne 'Windows_NT') {
        throw 'This installer is for Windows. Use ./scripts/install.sh on Linux or macOS.'
    }
    if (-not (Test-Path (Join-Path $RepoRoot 'docker-compose.yml')) -or -not (Test-Path $CorpusDir)) {
        throw 'This does not look like the GalaxyHire repository (expected docker-compose.yml + services\corpus).'
    }

    # Compute the step total for the chosen mode.
    $script:TotalSteps = 8
    if ($Start -or $Dev) { $script:TotalSteps = 9 }
    if ($DepsOnly) { $script:TotalSteps = 4 }

    if (-not $script:DryRun) {
        New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    }
    Write-LogLine ("GalaxyHire installer starting (dry-run={0})" -f $script:DryRun)

    # Fail early with a useful message on a machine with a tiny disk.
    try {
        $drive = (Get-Item $RepoRoot).PSDrive
        if ($drive -and $drive.Free -lt 12GB) {
            Write-WarnLine ("Less than 12 GB free on {0}: dependencies, models, and the corpus may not fit." -f $drive.Name)
        }
    } catch { $null = $_ }

    Install-BasePackages
    Ensure-Toolchains
    Install-WorkspaceDeps
    Prepare-Env

    if ($DepsOnly) {
        Write-Host ''
        Write-Host '==> Dependencies only - finished' -ForegroundColor White
        Write-Ok 'Dependencies are installed. Re-run without -DepsOnly to deploy Docker, the schema, and builds.'
        return
    }

    Ensure-Docker
    Deploy-Infra
    Invoke-Migrations
    Build-Artifacts

    if ($Start -or $Dev) {
        Write-Section 'Starting GalaxyHire'
        # Hashtable splatting: an array of strings would bind '-Dev' positionally, not as a switch.
        $startArgs = @{}
        if ($Dev) { $startArgs['Dev'] = $true }
        & (Join-Path $ScriptDir 'start.ps1') @startArgs
    }

    Show-Summary
}

try {
    Main
} catch {
    Write-Host ''
    Write-Host ("Installation failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    if ((-not $DryRun) -and (Test-Path $LogFile)) { Write-Host ("  Log: {0}" -f $LogFile) }
    exit 1
}
