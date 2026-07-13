$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "虚拟环境不存在。请先按照 README 完成安装。"
}

$existing = Get-NetTCPConnection -State Listen -LocalPort 8550 -ErrorAction SilentlyContinue
if (-not $existing) {
    Start-Process `
        -FilePath $python `
        -ArgumentList "main.py --no-browser --port 8550" `
        -WorkingDirectory $root

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 200
        if (Get-NetTCPConnection -State Listen -LocalPort 8550 -ErrorAction SilentlyContinue) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        throw "V2 服务启动超时"
    }
}

$chromeCandidates = @(
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chrome) {
    throw "未找到 Google Chrome"
}
Start-Process -FilePath $chrome -ArgumentList "http://127.0.0.1:8550"

