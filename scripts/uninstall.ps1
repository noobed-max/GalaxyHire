#Requires -Version 5.1
<#
.SYNOPSIS
    GalaxyHire uninstaller for Windows.

.DESCRIPTION
    Reverses what scripts/install.ps1 set up:

      1. stops the running app (pid files recorded by start.ps1)
      2. removes the Docker containers, network, and database volume
      3. removes the profile PATH entry the installer added
      4. optionally deletes local profile/settings data (-PurgeData)
      5. optionally deletes the checkout itself (-RemoveFiles)

.PARAMETER Yes
    Assume "yes" for prompts (non-interactive).

.PARAMETER PurgeData
    Also delete the local profile, settings, and leads data.

.PARAMETER RemoveFiles
    Also delete the checkout this script runs from (refuses git checkouts).

.PARAMETER DryRun
    Print what would happen without changing anything.

.PARAMETER Help
    Show this help.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1

.EXAMPLE
    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/noobed-max/GalaxyHire/master/scripts/uninstall.ps1))) -RemoveFiles -Yes
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$PurgeData,
    [switch]$RemoveFiles,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

# ---- paths ------------------------------------------------------------------
$ScriptDir = $PSScriptRoot
$Checkout = ''
if ($env:GALAXYHIRE_DIR) {
    # An explicit target always wins, so a source checkout can clean up a bootstrap install.
    $Checkout = $env:GALAXYHIRE_DIR
}
elseif ($ScriptDir) {
    $candidate = Join-Path $ScriptDir '..'
    if (Test-Path (Join-Path $candidate 'docker-compose.yml')) { $Checkout = (Resolve-Path $candidate).Path }
}
if (-not $Checkout) { $Checkout = Join-Path $HOME 'GalaxyHire' }
$ComposeFile = Join-Path $Checkout 'docker-compose.yml'
$RunDir = Join-Path $Checkout '.run'

# ---- logging ----------------------------------------------------------------
function Write-Info {
    param([string]$Message)
    Write-Host ("  > {0}" -f $Message) -ForegroundColor Cyan
}
function Write-Ok {
    param([string]$Message)
    Write-Host ("  v {0}" -f $Message) -ForegroundColor Green
}
function Write-WarnLine {
    param([string]$Message)
    Write-Host ("  ! {0}" -f $Message) -ForegroundColor Yellow
}
function Fail {
    param([string]$Message)
    Write-Host ("  x {0}" -f $Message) -ForegroundColor Red
    exit 1
}

function Show-Usage {
    Write-Host @'
GalaxyHire uninstaller for Windows

Usage: powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 [options]

Stops GalaxyHire and removes its Docker containers, network, and database volume, plus the
profile PATH entry the installer added. Your profile/settings data and the checkout are kept
unless you ask for them.

Options:
  -Yes           assume "yes" for prompts (non-interactive)
  -PurgeData     also delete local profile/settings/leads data
  -RemoveFiles   also delete the checkout (refuses git checkouts)
  -DryRun        print what would happen without changing anything
  -Help          show this help

Environment:
  GALAXYHIRE_DIR   checkout to operate on (default: this script's checkout, else %USERPROFILE%\GalaxyHire)
  JHM_APP_DATA_DIR local data directory override used by the app
'@
}

# ---- helpers ----------------------------------------------------------------
function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Confirm {
    param([string]$Prompt)
    if ($Yes) { return $true }
    $reply = Read-Host ("  ? {0} [y/N]" -f $Prompt)
    return $reply -match '^[Yy]'
}

function Invoke-Action { # native command, dry-run aware, throws on failure
    param([string]$FilePath, [string[]]$Arguments = @())
    if ($DryRun) {
        Write-Host ("      [dry-run] {0} {1}" -f $FilePath, ($Arguments -join ' ')) -ForegroundColor DarkGray
        return
    }
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')" }
}

function Try-Quiet { # best-effort cleanup: no output, never fails
    param([string]$FilePath, [string[]]$Arguments = @())
    if ($DryRun) {
        Write-Host ("      [dry-run] {0} {1}" -f $FilePath, ($Arguments -join ' ')) -ForegroundColor DarkGray
        return
    }
    try { & $FilePath @Arguments *> $null } catch { $null = $_ }
}

# ---- 1. stop the app --------------------------------------------------------
function Stop-PidFiles {
    if (-not (Test-Path $RunDir)) { return }
    $found = $false
    foreach ($file in @(Get-ChildItem -Path $RunDir -Filter '*.pid' -ErrorAction SilentlyContinue)) {
        $found = $true
        $processId = Get-Content $file.FullName -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($processId) { Try-Quiet -FilePath 'taskkill' -Arguments @('/PID', $processId, '/T', '/F') }
        if (-not $DryRun) { Remove-Item $file.FullName -Force -ErrorAction SilentlyContinue }
    }
    if ($found) { Write-Ok 'app processes stopped' }
}

# ---- 2. Docker --------------------------------------------------------------
function Remove-Docker {
    if (-not (Test-Command docker)) {
        Write-WarnLine 'Docker is not installed; skipping container cleanup.'
        return
    }
    & docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-WarnLine 'The Docker engine is not reachable; skipping container cleanup.'
        return
    }
    if (Test-Path $ComposeFile) {
        Write-Info 'Removing containers, network, and database volume...'
        Invoke-Action -FilePath 'docker' -Arguments @('compose', '-f', $ComposeFile, 'down', '-v', '--remove-orphans')
    }
    else {
        Write-Info 'Removing containers, network, and database volume by name...'
    }
    # Idempotent belt-and-braces when the checkout was renamed or already gone.
    foreach ($container in @('galaxyhire-postgres', 'galaxyhire-redis')) {
        Try-Quiet -FilePath 'docker' -Arguments @('rm', '-f', $container)
    }
    Try-Quiet -FilePath 'docker' -Arguments @('volume', 'rm', 'galaxyhire_pgdata')
    Try-Quiet -FilePath 'docker' -Arguments @('network', 'rm', 'galaxyhire_default')
    Write-Ok 'Docker resources removed'
}

# ---- 3. profile PATH entry --------------------------------------------------
function Remove-ProfilePath {
    if (-not (Test-Path $PROFILE)) { return }
    $marker = '# GalaxyHire toolchain (added by scripts\install.ps1)'
    $lines = @(Get-Content -Path $PROFILE -ErrorAction SilentlyContinue)
    if (-not ($lines -contains $marker)) { return }
    Write-Info ("Removing the toolchain PATH line from {0}" -f $PROFILE)
    if ($DryRun) { return }
    $kept = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -eq $marker) {
            if (($i + 1) -lt $lines.Count -and $lines[$i + 1] -match '^\$env:Path \+=') { $i++ }
            continue
        }
        $kept.Add($lines[$i])
    }
    Set-Content -Path $PROFILE -Value $kept
    Write-Ok 'profile PATH cleaned'
}

# ---- 4. local app data (opt-in) --------------------------------------------
function Get-AppDataDir {
    if ($env:JHM_APP_DATA_DIR) { return $env:JHM_APP_DATA_DIR }
    $base = if ($env:JHM_APP_DATA_BASE_DIR) { $env:JHM_APP_DATA_BASE_DIR }
            elseif ($env:LOCALAPPDATA) { $env:LOCALAPPDATA }
            else { Join-Path $HOME 'AppData\Local' }
    return (Join-Path $base 'JustHireMe')
}

function Remove-AppData {
    $dir = Get-AppDataDir
    if (-not (Test-Path $dir)) {
        Write-Info ("No local app data at {0}" -f $dir)
        return
    }
    if (-not (Confirm "Delete your local profile, settings, and leads at $dir?")) {
        Write-Info 'Keeping local app data.'
        return
    }
    Write-Info ("Deleting {0}..." -f $dir)
    if ($DryRun) {
        Write-Host ("      [dry-run] Remove-Item -LiteralPath {0} -Recurse -Force" -f $dir) -ForegroundColor DarkGray
    }
    else {
        Remove-Item -LiteralPath $dir -Recurse -Force
    }
    Write-Ok 'local app data deleted'
}

# ---- 5. checkout (opt-in) ---------------------------------------------------
function Remove-Checkout {
    if (-not (Test-Path $Checkout)) {
        Write-Info ("Checkout not found at {0}" -f $Checkout)
        return
    }
    if (Test-Path (Join-Path $Checkout '.git')) {
        Write-WarnLine ("{0} is a git checkout; refusing to delete it." -f $Checkout)
        Write-WarnLine ("Delete it yourself if you really want that: Remove-Item -Recurse -Force '{0}'" -f $Checkout)
        return
    }
    if (-not (Confirm "Delete the checkout at $Checkout?")) {
        Write-Info 'Keeping the checkout.'
        return
    }
    Write-Info ("Deleting {0}..." -f $Checkout)
    if ($DryRun) {
        Write-Host ("      [dry-run] Remove-Item -LiteralPath {0} -Recurse -Force" -f $Checkout) -ForegroundColor DarkGray
    }
    else {
        Remove-Item -LiteralPath $Checkout -Recurse -Force
    }
    Write-Ok 'checkout deleted'
}

# ---- main -------------------------------------------------------------------
function Main {
    if ($Help) { Show-Usage; return }
    if ($env:OS -ne 'Windows_NT') {
        throw 'This uninstaller is for Windows. Use ./scripts/uninstall.sh on Linux or macOS.'
    }
    Write-Info ("Checkout: {0}" -f $Checkout)
    Stop-PidFiles
    Remove-Docker
    Remove-ProfilePath
    if ($PurgeData) { Remove-AppData }
    if ($RemoveFiles) { Remove-Checkout }

    Write-Host ''
    Write-Host 'GalaxyHire uninstalled.' -ForegroundColor Green
    Write-Host '  Docker containers, network, and database volume: removed'
    Write-Host '  Profile PATH entry: removed'
    if (-not $PurgeData) { Write-Host ("  Local profile/settings data: kept ({0})" -f (Get-AppDataDir)) }
    if (-not $RemoveFiles -and (Test-Path $Checkout)) { Write-Host ("  Checkout: kept ({0})" -f $Checkout) }
    Write-Host '  Toolchains installed by the installer (uv, Node.js, Bun, Docker) are left alone.'
}

try {
    Main
} catch {
    Write-Host ''
    Write-Host ("Uninstall failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    exit 1
}
