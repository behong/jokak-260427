$ErrorActionPreference = "Stop"

$Version = "v2.12.0"
$Url = "https://github.com/winsw/winsw/releases/download/$Version/WinSW-x64.exe"
$DownloadPath = Join-Path $PSScriptRoot "WinSW-x64.exe"
$MonitorExe = Join-Path $PSScriptRoot "TelegramMonitor.exe"
$DashboardExe = Join-Path $PSScriptRoot "TelegramDashboard.exe"

if (-not (Test-Path -LiteralPath $DownloadPath)) {
    Write-Output "Downloading WinSW $Version..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $DownloadPath
    } catch {
        Write-Output "Invoke-WebRequest failed. Trying curl.exe..."
        & curl.exe -L --fail --output $DownloadPath $Url
        if ($LASTEXITCODE -ne 0) {
            Write-Output "curl.exe failed. Trying Python urllib..."
            $PythonCode = @"
from urllib.request import urlopen
url = r"$Url"
path = r"$DownloadPath"
with urlopen(url, timeout=120) as response:
    with open(path, "wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
"@
            $PythonCode | python -
            if ($LASTEXITCODE -ne 0) {
                throw "Python urllib failed with exit code $LASTEXITCODE"
            }
        }
    }
}

Copy-Item -LiteralPath $DownloadPath -Destination $MonitorExe -Force
Copy-Item -LiteralPath $DownloadPath -Destination $DashboardExe -Force

Write-Output "WinSW is ready."
