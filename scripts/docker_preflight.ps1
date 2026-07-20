$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Check-Path {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [switch] $Required
    )

    if (Test-Path -LiteralPath $Path) {
        Write-Output "OK      $Path"
        return
    }

    if ($Required) {
        Write-Output "MISSING $Path"
        $script:HasError = $true
    } else {
        Write-Output "WARN    $Path"
    }
}

$script:HasError = $false

function Get-DockerCommand {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $defaultPath = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $defaultPath) {
        return $defaultPath
    }

    return $null
}

$Docker = Get-DockerCommand
if (-not $Docker) {
    Write-Output "MISSING docker command"
    $script:HasError = $true
} else {
    & $Docker --version
    & $Docker compose version
    & $Docker info --format "{{.ServerVersion}}"
    if ($LASTEXITCODE -ne 0) {
        Write-Output "MISSING docker engine access"
        $script:HasError = $true
    }
}

Check-Path ".env.docker" -Required
Check-Path "telegram_logs.sqlite3" -Required
Check-Path "telegram_monitor.session" -Required
Check-Path "youtube_token.json" -Required
Check-Path "client_secret.json" -Required
Check-Path "outputs" -Required
Check-Path "assets\backgrounds" -Required
Check-Path "assets\bgm" -Required
Check-Path "backups" -Required
Check-Path "logs" -Required
Check-Path "telegram_dashboard_refresh.session"

$dashboardHost = Select-String -LiteralPath ".env.docker" -Pattern "^DASHBOARD_HOST=" -ErrorAction SilentlyContinue
if ($dashboardHost -and $dashboardHost.Line -notmatch "DASHBOARD_HOST=0\.0\.0\.0") {
    Write-Output "WARN    .env.docker DASHBOARD_HOST is not 0.0.0.0; compose overrides it for dashboard"
}

if ($script:HasError) {
    throw "Docker preflight failed"
}

Write-Output "Docker preflight passed"
