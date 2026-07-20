$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$BackupDir = Join-Path $Root "backups"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$PackageName = "portable-glbanjang-$Stamp"
$StageRoot = Join-Path $env:TEMP $PackageName
$PackagePath = Join-Path $BackupDir "$PackageName.zip"
$ManifestPath = Join-Path $StageRoot "PORTABLE_MANIFEST.txt"

$includeFiles = @(
    ".env",
    ".gitignore",
    "README.md",
    "requirements.txt",
    "channels.json",
    "telegram_logs.sqlite3",
    "telegram_monitor.session",
    "telegram_monitor.session-journal",
    "client_secret.json",
    "client_secrets.json",
    "youtube_token.json"
)

$includeDirs = @(
    "assets",
    "outputs",
    "scripts",
    "service",
    "static",
    "templates"
)

$includePy = Get-ChildItem -LiteralPath $Root -Filter "*.py" -File |
    Where-Object { $_.Name -notmatch "^test_" } |
    ForEach-Object { $_.Name }

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null

foreach ($relative in ($includeFiles + $includePy)) {
    $source = Join-Path $Root $relative
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        $target = Join-Path $StageRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
}

foreach ($relative in $includeDirs) {
    $source = Join-Path $Root $relative
    if (Test-Path -LiteralPath $source -PathType Container) {
        $target = Join-Path $StageRoot $relative
        Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
    }
}

$manifest = @(
    "Portable package: $PackageName",
    "Created at: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")",
    "Source: $Root",
    "",
    "Included:",
    "- code (*.py)",
    "- dashboard templates/static",
    "- scripts/service files",
    "- .env",
    "- channels.json",
    "- telegram_logs.sqlite3",
    "- telegram_monitor.session",
    "- YouTube OAuth files",
    "- assets/backgrounds",
    "- outputs",
    "",
    "Excluded:",
    "- .venv",
    "- .git",
    "- backups",
    "- logs",
    "- __pycache__",
    "- .tmp",
    "",
    "Restore:",
    "1. Extract this zip on another Windows PC.",
    "2. Install Python 3.11+ and ffmpeg/ffprobe.",
    "3. Run: python -m venv .venv",
    "4. Run: .\.venv\Scripts\Activate.ps1",
    "5. Run: pip install -r requirements.txt",
    "6. Run: pm2 restart jijogak-monitor --update-env",
    "7. Run: pm2 restart jijogak-dashboard --update-env"
)
$manifest | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

Compress-Archive -Path (Join-Path $StageRoot "*") -DestinationPath $PackagePath -Force
$packageSize = (Get-Item -LiteralPath $PackagePath).Length
Remove-Item -LiteralPath $StageRoot -Recurse -Force

[PSCustomObject]@{
    package = $PackagePath
    size_mb = [Math]::Round($packageSize / 1MB, 2)
    created_at = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
} | ConvertTo-Json
