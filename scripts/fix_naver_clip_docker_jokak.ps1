$ErrorActionPreference = "Stop"

$composePath = "C:\Users\Administrator\code\total-10shop-260514\integrations\naver-clip-api\docker-compose.yml"
$jokakMount = "C:/Users/Administrator/code/jokak-260427:/host/jokak:ro"
$mappingValue = "C:\Users\Administrator\code\total-10shop-260514\media=/host/total10/media;/app/media=/host/total10/media;C:\Users\Administrator\code\jokak-260427=/host/jokak"

if (-not (Test-Path -LiteralPath $composePath)) {
    throw "Naver Clip Docker compose file not found: $composePath"
}

$backupPath = "$composePath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $composePath -Destination $backupPath

$text = Get-Content -LiteralPath $composePath -Raw

$mappingLine = "      NAVER_VIDEO_PATH_MAPPINGS: '" + '${NAVER_VIDEO_PATH_MAPPINGS:-' + $mappingValue + "}'"
$text = [regex]::Replace(
    $text,
    '(?m)^\s*NAVER_VIDEO_PATH_MAPPINGS:\s*.*$',
    [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $mappingLine }
)

if ($text -notmatch [regex]::Escape($jokakMount)) {
    $anchor = "      - ../../media:/host/total10/media:ro"
    $replacement = "$anchor`r`n      - ${jokakMount}"
    if ($text.Contains($anchor)) {
        $text = $text.Replace($anchor, $replacement)
    } else {
        throw "Could not find volume anchor in compose file. Backup left at: $backupPath"
    }
}

Set-Content -LiteralPath $composePath -Value $text -Encoding UTF8

$docker = "docker"
$dockerExe = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
if (Test-Path -LiteralPath $dockerExe) {
    $docker = $dockerExe
}

Push-Location (Split-Path -Parent $composePath)
try {
    & $docker compose up -d --build
    & $docker compose ps
}
finally {
    Pop-Location
}

Write-Host "Updated Naver Clip Docker compose."
Write-Host "Backup: $backupPath"
Write-Host "Jokak videos are mounted at /host/jokak inside total10-naver-clip-api."
