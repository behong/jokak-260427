$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$blockedPaths = @(
    ".env",
    "client_secret.json",
    "client_secrets.json",
    "youtube_token.json",
    "telegram_logs.sqlite3",
    "telegram_monitor.session"
)
$blockedDirs = @(
    "assets/backgrounds",
    "outputs",
    "backups",
    "logs",
    "static/media",
    ".venv",
    ".tmp",
    "vendor"
)

$failed = $false

foreach ($relative in $blockedPaths) {
    $path = Join-Path $Root $relative
    if (Test-Path -LiteralPath $path) {
        Write-Output "LOCAL ONLY: $relative exists. Keep it ignored and never commit it."
    }
}

foreach ($relative in $blockedDirs) {
    $path = Join-Path $Root $relative
    if (Test-Path -LiteralPath $path) {
        Write-Output "LOCAL ONLY: $relative exists. Keep it ignored and never commit it."
    }
}

if (Get-Command git -ErrorAction SilentlyContinue) {
    $safeRoot = $Root.Replace("\", "/")
    $tracked = git -c "safe.directory=$safeRoot" ls-files
    foreach ($relative in ($blockedPaths + $blockedDirs)) {
        $normalized = $relative.Replace("\", "/")
        $matches = $tracked | Where-Object { $_ -eq $normalized -or $_.StartsWith("$normalized/") }
        if ($matches) {
            $failed = $true
            Write-Output "BLOCKED: tracked sensitive path $relative"
        }
    }
}

if ($failed) {
    throw "Secret scan failed. Remove sensitive files or references before pushing."
}

Write-Output "Secret scan passed."
